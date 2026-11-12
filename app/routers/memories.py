"""记忆 CRUD / 检索 / scope 迁移（/v1/memories）。"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import (
    assert_no_secret, audit, can_read, can_write, device_readable_scopes,
    get_current_device,
)
from ..config import settings
from ..db import get_connection, now_iso, write_transaction
from ..models import BulkDeleteRequest, MemoryCreate, MemoryCreateResponse, MemoryPromote, MemoryUpdate, SearchRequest
from ..search import bm25_search, build_match, index_memory, unindex_memory
from .events import push_event

router = APIRouter()


def _resolve_scope(scope: str, scope_ref: str, device: dict) -> tuple[str, str]:
    """把缺省别名解析成具体 scope（device 无后缀=本人，agent* 等）。"""
    if scope == "device" and not scope_ref:
        return scope, str(device["id"])
    if scope == "agent" and not scope_ref:
        return scope, "*"
    return scope, scope_ref


def _row_to_dict(row) -> dict:
    d = dict(row)
    for k in ("tags", "origin"):
        try:
            d[k] = json.loads(d[k] or "[]" if k == "tags" else d[k] or "{}")
        except Exception:
            pass
    return d


@router.post("/v1/memories", response_model=MemoryCreateResponse, tags=["memories"])
def create_memory(body: MemoryCreate, device: dict = Depends(get_current_device)):
    if body.scope not in ("user", "project", "device", "agent", "session"):
        raise HTTPException(status_code=400, detail="scope 取值非法")
    scope, scope_ref = _resolve_scope(body.scope, body.scope_ref, device)
    conn = get_connection()
    if not can_write(scope, scope_ref, device, conn):
        raise HTTPException(status_code=403, detail=f"无权写入 {scope}/{scope_ref}")
    assert_no_secret(body.content, scope)
    origin = {"device_id": device["id"], "agent_id": body.agent_id, "session_id": body.session_id}
    with write_transaction() as tx:
        cur = tx.execute(
            "INSERT INTO memories(scope, scope_ref, content, kind, tags, origin, source_ref, confidence) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (scope, scope_ref, body.content, body.kind, json.dumps(body.tags, ensure_ascii=False),
             json.dumps(origin, ensure_ascii=False), body.source_ref, body.confidence),
        )
        mem_id = cur.lastrowid
        index_memory(tx, mem_id, body.content)
        audit(tx, device, "write", scope, scope_ref, f"id={mem_id}")
    push_event("memory.write", scope, scope_ref, mem_id, device["id"])
    return MemoryCreateResponse(id=mem_id, scope=scope, scope_ref=scope_ref, version=1)


@router.get("/v1/memories", tags=["memories"])
def list_memories(scope: str | None = None, scope_ref: str | None = None,
                  limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                  device: dict = Depends(get_current_device)):
    """记忆列表（读全开）：默认返回全部活跃记忆；scope 参数收窄视图。

    写权限仍严格（增删改走 can_write）；此处读权限对全部活跃设备开放。
    """
    conn = get_connection()
    conds = ["deleted_at IS NULL AND superseded_by IS NULL"]
    args: list = []
    if scope:
        if scope not in ("user", "project", "device", "agent", "session"):
            raise HTTPException(status_code=400, detail="scope 取值非法")
        sref = scope_ref or ""
        if scope == "device" and not sref:
            sref = str(device["id"])
        elif scope == "agent" and not sref:
            sref = "*"
        if sref:
            conds.append("scope=? AND scope_ref=?")
            args += [scope, sref]
        else:
            # project 不带 slug：返回全部项目区条目（管理台总览用）
            conds.append("scope=?")
            args.append(scope)
    where = " AND ".join(conds)
    rows = conn.execute(
        f"SELECT * FROM memories WHERE {where} ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
        (*args, limit, offset),
    ).fetchall()
    total = conn.execute(
        f"SELECT COUNT(*) AS c FROM memories WHERE {where}", args
    ).fetchone()["c"]
    return {"results": [_row_to_dict(r) for r in rows], "total": total,
            "limit": limit, "offset": offset}


@router.get("/v1/memories/{mem_id}", tags=["memories"])
def get_memory(mem_id: int, device: dict = Depends(get_current_device)):
    """按逻辑记忆取最新活跃版本（跟随版本链）。"""
    conn = get_connection()
    cur_id = mem_id
    seen: set[int] = set()
    row = None
    while cur_id and cur_id not in seen:
        seen.add(cur_id)
        row = conn.execute(
            "SELECT * FROM memories WHERE id=? AND deleted_at IS NULL", (cur_id,)
        ).fetchone()
        if row is None or row["superseded_by"] is None:
            break
        cur_id = row["superseded_by"]
    if row is None:
        raise HTTPException(status_code=404, detail="记忆不存在")
    if not can_read(row["scope"], row["scope_ref"], device, conn):
        raise HTTPException(status_code=403, detail="无权读取该记忆")
    return _row_to_dict(row)


@router.patch("/v1/memories/{mem_id}", tags=["memories"])
def update_memory(mem_id: int, body: MemoryUpdate, device: dict = Depends(get_current_device)):
    conn = get_connection()
    old = conn.execute(
        "SELECT * FROM memories WHERE id=? AND deleted_at IS NULL AND superseded_by IS NULL", (mem_id,)
    ).fetchone()
    if old is None:
        raise HTTPException(status_code=404, detail="记忆不存在或已更新")
    if not can_write(old["scope"], old["scope_ref"], device, conn):
        raise HTTPException(status_code=403, detail="无权修改")
    content = body.content if body.content is not None else old["content"]
    assert_no_secret(content, old["scope"])
    new_tags = body.tags if body.tags is not None else json.loads(old["tags"])
    with write_transaction() as tx:
        cur = tx.execute(
            "INSERT INTO memories(scope, scope_ref, content, kind, tags, origin, source_ref, "
            "confidence, valid_until, version) "
            "SELECT scope, scope_ref, ?, ?, ?, origin, source_ref, ?, ?, version+1 "
            "FROM memories WHERE id=?",
            (content,
             body.kind if body.kind is not None else old["kind"],
             json.dumps(new_tags, ensure_ascii=False),
             body.confidence if body.confidence is not None else old["confidence"],
             body.valid_until if body.valid_until is not None else old["valid_until"],
             mem_id),
        )
        new_id = cur.lastrowid
        tx.execute("UPDATE memories SET superseded_by=? WHERE id=?", (new_id, mem_id))
        index_memory(tx, new_id, content)
        audit(tx, device, "update", old["scope"], old["scope_ref"], f"id={mem_id}->{new_id}")
    push_event("memory.update", old["scope"], old["scope_ref"], new_id, device["id"])
    row = get_connection().execute("SELECT * FROM memories WHERE id=?", (new_id,)).fetchone()
    return _row_to_dict(row)


@router.delete("/v1/memories/{mem_id}", tags=["memories"])
def delete_memory(mem_id: int, device: dict = Depends(get_current_device)):
    conn = get_connection()
    row = conn.execute("SELECT * FROM memories WHERE id=? AND deleted_at IS NULL", (mem_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="记忆不存在")
    if not can_write(row["scope"], row["scope_ref"], device, conn):
        raise HTTPException(status_code=403, detail="无权删除")
    with write_transaction() as tx:
        tx.execute("UPDATE memories SET deleted_at=? WHERE id=? AND deleted_at IS NULL",
                   (now_iso(), mem_id))
        unindex_memory(tx, mem_id)
        audit(tx, device, "delete", row["scope"], row["scope_ref"], f"id={mem_id}")
    return {"ok": True, "deleted_id": mem_id}


@router.post("/v1/memories:bulk-delete", tags=["memories"])
def bulk_delete_memories(body: BulkDeleteRequest, device: dict = Depends(get_current_device)):
    """批量删除：逐条权限检查，无权/不存在的 id 计入 skipped。默认软删。"""
    conn = get_connection()
    deleted = 0
    skipped: list[int] = []
    with write_transaction() as tx:
        for mem_id in body.ids:
            row = tx.execute(
                "SELECT * FROM memories WHERE id=? AND deleted_at IS NULL", (mem_id,)
            ).fetchone()
            if row is None or not can_write(row["scope"], row["scope_ref"], device, conn):
                skipped.append(mem_id)
                continue
            if body.hard:
                # 物理删除：记忆行 + FTS 索引 + 指向它的提案引用
                tx.execute("DELETE FROM memories WHERE id=?", (mem_id,))
                tx.execute("DELETE FROM memory_fts WHERE rowid=?", (mem_id,))
                tx.execute("DELETE FROM proposals WHERE memory_id=?", (mem_id,))
            else:
                tx.execute(
                    "UPDATE memories SET deleted_at=? WHERE id=? AND deleted_at IS NULL",
                    (now_iso(), mem_id),
                )
                unindex_memory(tx, mem_id)
            audit(tx, device, "delete", row["scope"], row["scope_ref"],
                  f"id={mem_id}" + (" hard" if body.hard else ""))
            deleted += 1
    return {"ok": True, "deleted": deleted, "skipped": skipped}


@router.get("/v1/stats", tags=["admin"])
def service_stats(device: dict = Depends(get_current_device)):
    """管理台首页统计（一次请求替代 health + memories?limit=500 + proposals）。"""
    conn = get_connection()
    by_scope = {
        r["scope"]: r["n"]
        for r in conn.execute(
            "SELECT scope, COUNT(*) AS n FROM memories "
            "WHERE deleted_at IS NULL AND superseded_by IS NULL GROUP BY scope"
        ).fetchall()
    }
    pending = conn.execute("SELECT COUNT(*) FROM proposals WHERE status='pending'").fetchone()[0]
    devices = conn.execute("SELECT COUNT(*) FROM devices WHERE active=1").fetchone()[0]
    return {"memories": sum(by_scope.values()), "by_scope": by_scope,
            "pending_proposals": pending, "devices": devices}


@router.post("/v1/memories:search", tags=["memories"])
def search_memories(body: SearchRequest, device: dict = Depends(get_current_device)):
    conn = get_connection()

    def scope_match(scope: str, scope_ref: str) -> bool:
        # 读全开：所有活跃设备可检索全部条目（写端权限不受影响）
        return True

    match_expr = build_match(body.query)
    candidates = bm25_search(conn, match_expr, settings.search_top_k)

    # 应用层：取最新版本 + 权限过滤（永远强制）+ scopes 收窄 + 时间过滤
    wanted_types: set | None = None
    wanted_specific: set | None = None
    if body.scopes is not None:
        wanted_types = {s.split(":")[0] for s in body.scopes}
        wanted_specific = set(body.scopes)

    results = []
    for cand in candidates:
        row = conn.execute(
            "SELECT * FROM memories WHERE id=? AND deleted_at IS NULL AND superseded_by IS NULL",
            (cand["rowid"],),
        ).fetchone()
        if row is None:
            continue
        # insight 是聚合导航条目（内容 = 源记忆的合并摘要），默认不参与检索召回，
        # 避免与源记忆同时命中造成"重复"；需要看时显式 include_insight=true
        if row["kind"] == "insight" and not body.include_insight:
            continue
        # 权限检查不可跳过：scopes 只能收窄可见范围，不能放宽
        if not scope_match(row["scope"], row["scope_ref"]):
            continue
        if wanted_types is not None:
            specific = f"{row['scope']}:{row['scope_ref']}"
            if row["scope"] not in wanted_types and specific not in wanted_specific:
                continue
        if body.since and row["updated_at"] < body.since:
            continue
        d = _row_to_dict(row)
        d["score"] = round(float(cand["rank"]), 3)
        results.append(d)
        if len(results) >= body.limit:
            break
    return {"results": results, "matched": len(results), "query": body.query}


@router.post("/v1/memories/{mem_id}:promote", tags=["memories"])
def promote_memory(mem_id: int, body: MemoryPromote, device: dict = Depends(get_current_device)):
    conn = get_connection()
    old = conn.execute(
        "SELECT * FROM memories WHERE id=? AND deleted_at IS NULL AND superseded_by IS NULL", (mem_id,)
    ).fetchone()
    if old is None:
        raise HTTPException(status_code=404, detail="记忆不存在或已更新")
    if old["scope"] == body.to_scope and old["scope_ref"] == (body.to_scope_ref or old["scope_ref"]):
        raise HTTPException(status_code=400, detail="目标 scope 与当前相同")
    to_scope, to_ref = _resolve_scope(body.to_scope, body.to_scope_ref, device)
    if not can_write(to_scope, to_ref, device, conn):
        raise HTTPException(status_code=403, detail=f"无权写入 {to_scope}/{to_ref}")
    assert_no_secret(old["content"], to_scope)
    with write_transaction() as tx:
        cur = tx.execute(
            "INSERT INTO memories(scope, scope_ref, content, kind, tags, origin, source_ref, confidence, "
            "valid_until, version) SELECT ?, ?, content, kind, tags, origin, source_ref, confidence, valid_until, version+1 "
            "FROM memories WHERE id=?",
            (to_scope, to_ref, mem_id),
        )
        new_id = cur.lastrowid
        tx.execute("UPDATE memories SET superseded_by=? WHERE id=?", (new_id, mem_id))
        index_memory(tx, new_id, old["content"])
        audit(tx, device, "promote", old["scope"], old["scope_ref"],
              f"id={mem_id}->{to_scope}/{to_ref} reason={body.reason[:200]}")
    push_event("memory.promote", to_scope, to_ref, new_id, device["id"])
    return {"ok": True, "memory_id": new_id, "scope": to_scope, "scope_ref": to_ref,
            "from_scope": old["scope"], "from_scope_ref": old["scope_ref"]}