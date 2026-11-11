"""insight 提炼：定期把近期记忆聚合为知识摘要（kind=insight）。

调度：默认每日 04:30（低峰）后台线程运行；可手动 POST /v1/insights/run。
"""
import json
import logging
import threading
import time

from . import llm
from .auth import audit
from .db import get_connection, now_iso, write_transaction
from .search import index_memory
from .routers.events import push_event

log = logging.getLogger("memoryd.insight")

INSIGHT_SYSTEM = """你是记忆整理员。下面是近 24 小时新增的用户级记忆清单，每行格式为「- [类型] 内容」。
任务：输出一份编号列表摘要，要求：
1. 清单中每一条记忆的信息都必须在摘要里出现（可以合并讲同一件事的多条，但绝不能整条丢失）；
2. 中文，每条一行，简洁压缩，总长 400 字以内；
3. 不要编造，不要任何推理过程，不要开场白和解释，只输出编号列表本身。
若某条含密钥/密码/API Key 字样，删除该条不输出。"""


def run_insight() -> dict:
    """用户级记忆 → LLM 摘要 → 写 insight 记忆。"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM memories WHERE scope='user' AND deleted_at IS NULL "
        "AND superseded_by IS NULL AND kind != 'insight' "
        "AND updated_at >= datetime('now','-1 day','localtime') "
        "ORDER BY updated_at DESC LIMIT 60"
    ).fetchall()
    if not rows:
        return {"status": "skipped", "reason": "近24h无新增用户级记忆"}
    # 小库聚合无价值（内容≈源记忆的压缩重述，产生"重复"观感），记忆量少时跳过
    if len(rows) < 5:
        return {"status": "skipped",
                "reason": f"源记忆仅 {len(rows)} 条（<5），小库聚合无价值，跳过"}
    src_ids = sorted(r["id"] for r in rows)
    # 去重：源记忆集合与最近一次 insight 完全相同 → 无新内容，跳过
    last = conn.execute(
        "SELECT source_ref FROM memories WHERE scope='user' AND kind='insight' "
        "AND deleted_at IS NULL AND superseded_by IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if last and last["source_ref"]:
        try:
            prev_ids = json.loads(last["source_ref"])
            if isinstance(prev_ids, list) and sorted(prev_ids) == src_ids:
                return {"status": "skipped", "reason": "源记忆集合与上次 insight 相同（无新内容）"}
        except (ValueError, TypeError):
            pass  # 旧格式 source_ref，正常生成
    items = "\n".join(f"- [{r['kind']}] {r['content']}" for r in rows)
    try:
        summary = llm._chat(INSIGHT_SYSTEM, f"记忆清单：\n{items}", max_tokens=2500, timeout=120).strip()
    except Exception as e:  # noqa: BLE001
        log.error("insight llm failed: %s", e)
        return {"status": "error", "reason": str(e)[:200]}
    if not summary:
        return {"status": "error", "reason": "LLM 返回空摘要（可能只返回了推理过程）"}
    origin = {"device_id": "system", "agent_id": "insight", "session_id": None}
    with write_transaction() as tx:
        cur = tx.execute(
            "INSERT INTO memories(scope, scope_ref, content, kind, tags, origin, source_ref, confidence) "
            "VALUES('user', '', ?, 'insight', ?, ?, ?, 0.9)",
            (summary, json.dumps(["insight"], ensure_ascii=False),
             json.dumps(origin, ensure_ascii=False),
             json.dumps(src_ids)),  # 记录源记忆集合，下次运行比对去重
        )
        mem_id = cur.lastrowid
        index_memory(tx, mem_id, summary)
        audit(tx, None, "insight", "user", "", f"id={mem_id} items={len(rows)}")
    push_event("insight", "user", "", mem_id, None, f"提炼 {len(rows)} 条近期记忆")
    return {"status": "done", "memory_id": mem_id, "items": len(rows),
            "summary": summary[:300]}


def _scheduler(interval_hour: int = 4, interval_minute: int = 30) -> None:
    """后台调度线程：每天固定时刻跑 insight。"""
    while True:
        now = time.localtime()
        target = time.mktime(
            (now.tm_year, now.tm_mon, now.tm_mday, interval_hour, interval_minute, 0, 0, 0, -1)
        )
        if target <= time.time():
            target += 86400
        time.sleep(max(target - time.time(), 60))
        try:
            result = run_insight()
            log.info("insight: %s", result.get("status"))
        except Exception as e:  # noqa: BLE001
            log.error("insight scheduler: %s", e)


def start_scheduler() -> None:
    threading.Thread(target=_scheduler, daemon=True, name="memoryd-insight").start()