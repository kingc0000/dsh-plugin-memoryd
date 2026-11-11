"""对话摄取端点（/v1/ingest）：提取 → 裁决 → 入库。"""
import json
import time

import jieba
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import assert_no_secret, audit, can_write, get_current_device
from ..db import get_connection, write_transaction
from ..search import build_match, bm25_search, index_memory
from .events import push_event
from .. import llm

router = APIRouter()


class IngestRequest(BaseModel):
    """对话批次提交。dialogue 为按时间排序的消息文本列表。"""
    dialogue: list[str] = Field(min_length=1, max_length=200)
    session_id: str | None = None
    agent_id: str = "root"
    async_mode: bool = True   # true=后台处理，返回受理号；false=同步等结果


class IngestResponse(BaseModel):
    accepted: bool
    batch_id: str
    extracted: int = 0
    written: list[dict] = []
    note: str = ""


# 简单内存任务表（进程内）；进程重启丢失任务状态但记忆已落库的不受影响
_tasks: dict[str, dict] = {}


def _similarity(a: str, b: str) -> float:
    """jieba 分词后的 token Jaccard 相似度（中文语义比字符 n-gram 准）。"""
    ta = {t for t in jieba.lcut(a) if len(t) > 1}
    tb = {t for t in jieba.lcut(b) if len(t) > 1}
    return len(ta & tb) / max(len(ta | tb), 1)


def _process_batch(batch_id: str, device: dict, req: IngestRequest) -> None:
    dialogue_text = "\n".join(req.dialogue)
    result = {"status": "running", "extracted": 0, "written": [], "error": None}
    _tasks[batch_id] = result
    try:
        facts = llm.extract_facts(dialogue_text)
        result["extracted"] = len(facts)
        conn = get_connection()
        for fact in facts:
            # 检索已有相关记忆（裁决依据）：BM25 召回 + 同 scope 活跃记忆兜底
            related = []
            seen_ids = set()
            match_expr = build_match(fact["content"])
            for cand in bm25_search(conn, match_expr, 15):
                row = conn.execute(
                    "SELECT * FROM memories WHERE id=? AND deleted_at IS NULL AND superseded_by IS NULL",
                    (cand["rowid"],),
                ).fetchone()
                if row and row["scope"] in ("user", "project", "agent", "device") \
                        and row["kind"] != "insight" and row["id"] not in seen_ids:
                    related.append(dict(row))
                    seen_ids.add(row["id"])
            # BM25 召不回措辞差异大的同义记忆 → 补同 scope 全量对照（记忆量小，代价低）
            if len(related) < 8:
                for scope_name in (fact.get("target_scope", "device"), "user", "device", "project", "agent"):
                    for row in conn.execute(
                        "SELECT * FROM memories WHERE scope=? AND deleted_at IS NULL "
                        "AND superseded_by IS NULL AND kind != 'insight' ORDER BY updated_at DESC LIMIT 20",
                        (scope_name,),
                    ):
                        if row["id"] not in seen_ids:
                            related.append(dict(row))
                            seen_ids.add(row["id"])
                    if len(related) >= 12:
                        break
            verdict = llm.adjudicate(fact, related[:8])

            # 兜底去重：LLM 判 ADD 但与已有记忆高度相似（分词 Jaccard）→ 强制 UPDATE
            if verdict["action"] == "ADD":
                for r in related[:8]:
                    if _similarity(fact["content"], r["content"]) > 0.4:
                        verdict = {"action": "UPDATE", "update_id": r["id"],
                                   "merged_content": fact["content"],
                                   "target_scope": verdict["target_scope"],
                                   "reason": f"分词相似度>0.4 兜底去重（与 id={r['id']}）"}
                        break

            scope = verdict["target_scope"]
            content = verdict["merged_content"]
            # 权限与红线双重校验（LLM 输出不可信）
            degraded_to: str = ""   # 记录降级前的目标 scope（用于自动报送升级申请）
            if not can_write(scope, "*" if scope == "agent" else "", device, conn) and scope != "device":
                degraded_to = scope
                scope = "device"
            try:
                assert_no_secret(content, scope)
            except HTTPException:
                degraded_to = ""    # 密钥降级不申请升级（共享区本来也不允许密钥）
                scope = "device"  # 含密钥降级私有区再试；device 允许
                assert_no_secret(content, scope)

            if verdict["action"] == "NOOP":
                continue
            origin = {"device_id": device["id"], "agent_id": req.agent_id, "session_id": req.session_id}
            with write_transaction() as tx:
                if verdict["action"] == "UPDATE" and verdict["update_id"]:
                    old = tx.execute(
                        "SELECT * FROM memories WHERE id=? AND deleted_at IS NULL",
                        (verdict["update_id"],),
                    ).fetchone()
                    # 目标已被版本链取代 → 跟到最新活跃版本再 UPDATE（防止退化为 ADD 产生重复）
                    hops = 0
                    while old is not None and old["superseded_by"] and hops < 10:
                        nxt = tx.execute(
                            "SELECT * FROM memories WHERE id=? AND deleted_at IS NULL",
                            (old["superseded_by"],),
                        ).fetchone()
                        if nxt is None:
                            break
                        old = nxt
                        hops += 1
                    if old and old["superseded_by"] is None and can_write(old["scope"], old["scope_ref"], device, conn):
                        cur = tx.execute(
                            "INSERT INTO memories(scope, scope_ref, content, kind, tags, origin, source_ref, "
                            "confidence, valid_until, version) "
                            "SELECT scope, scope_ref, ?, kind, tags, ?, source_ref, ?, valid_until, version+1 "
                            "FROM memories WHERE id=?",
                            (content, json.dumps(origin, ensure_ascii=False), fact["confidence"], old["id"]),
                        )
                        new_id = cur.lastrowid
                        tx.execute("UPDATE memories SET superseded_by=? WHERE id=?", (new_id, old["id"]))
                        index_memory(tx, new_id, content)
                        audit(tx, device, "ingest_update", old["scope"], old["scope_ref"],
                              f"batch={batch_id} {old['id']}->{new_id}")
                        push_event("memory.update", old["scope"], old["scope_ref"], new_id, device["id"])
                        result["written"].append({"id": new_id, "action": "UPDATE", "scope": old["scope"]})
                        continue
                # ADD（或 UPDATE 目标失效则退化为 ADD）
                scope_ref_val = str(device["id"]) if scope == "device" else ("*" if scope == "agent" else "")
                cur = tx.execute(
                    "INSERT INTO memories(scope, scope_ref, content, kind, tags, origin, source_ref, confidence) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (scope, scope_ref_val, content, fact["kind"],
                     json.dumps(fact["tags"], ensure_ascii=False),
                     json.dumps(origin, ensure_ascii=False), req.session_id, fact["confidence"]),
                )
                new_id = cur.lastrowid
                index_memory(tx, new_id, content)
                audit(tx, device, "ingest_write", scope, scope_ref_val, f"batch={batch_id} id={new_id}")
                push_event("memory.write", scope, scope_ref_val, new_id, device["id"])
                result["written"].append({"id": new_id, "action": "ADD", "scope": scope})
                # 降级发生 → 自动报送升级申请（插件侧自动判定：LLM 判共享区但无权，等 admin 审批）
                if degraded_to and degraded_to in ("user", "agent"):
                    with write_transaction() as tx2:
                        cur2 = tx2.execute(
                            "INSERT INTO proposals(device_id, type, memory_id, target_scope, reason) "
                            "VALUES(?,?,?,?,?)",
                            (device["id"], "scope_upgrade", new_id, degraded_to,
                             f"ingest 自动报送：LLM 判定应属 {degraded_to} 共享区，但设备无权写入，"
                             f"已暂存 device 区待审批。{verdict.get('reason', '')}"[:2000]),
                        )
                        audit(tx2, device, "proposal_create", "", degraded_to,
                              f"proposal={cur2.lastrowid} memory={new_id}（ingest 降级自动申请）")
                    result["written"][-1]["proposal"] = cur2.lastrowid
        result["status"] = "done"
    except Exception as e:  # noqa: BLE001
        result["status"] = "error"
        result["error"] = str(e)[:300]


@router.post("/v1/ingest", response_model=IngestResponse, tags=["ingest"])
def ingest(body: IngestRequest, background: BackgroundTasks,
           device: dict = Depends(get_current_device)):
    batch_id = f"batch-{device['id']}-{time.strftime('%Y%m%d%H%M%S')}-{id(body) % 10000}"
    if body.async_mode:
        background.add_task(_process_batch, batch_id, device, body)
        return IngestResponse(accepted=True, batch_id=batch_id,
                             note="已受理，后台处理中；用 GET /v1/ingest/{batch_id} 查询结果")
    _process_batch(batch_id, device, body)
    t = _tasks.get(batch_id, {})
    return IngestResponse(
        accepted=True, batch_id=batch_id, extracted=t.get("extracted", 0),
        written=t.get("written", []), note=t.get("error") or "",
    )


@router.get("/v1/ingest/{batch_id}", tags=["ingest"])
def ingest_status(batch_id: str, device: dict = Depends(get_current_device)):
    if batch_id not in _tasks:
        raise HTTPException(status_code=404, detail="批次不存在或已过期（进程重启后不可查）")
    return _tasks[batch_id]