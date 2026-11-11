"""会话黑板（/v1/sessions）：session scope 的读写。"""
import json

from fastapi import APIRouter, Depends, HTTPException

from ..auth import assert_no_secret, audit, get_current_device
from ..db import get_connection, write_transaction
from ..models import SessionPost
from ..search import index_memory
from .memories import _row_to_dict

router = APIRouter()


@router.post("/v1/sessions/{session_id}/post", tags=["sessions"])
def session_post(session_id: str, body: SessionPost, device: dict = Depends(get_current_device)):
    """把一条内容写入会话黑板（session scope）。"""
    assert_no_secret(body.content, "session")
    origin = {"device_id": device["id"], "agent_id": body.agent_id, "session_id": session_id}
    with write_transaction() as tx:
        cur = tx.execute(
            "INSERT INTO memories(scope, scope_ref, content, kind, origin) "
            "VALUES('session', ?, ?, 'fact', ?)",
            (session_id, body.content, json.dumps(origin, ensure_ascii=False)),
        )
        mem_id = cur.lastrowid
        index_memory(tx, mem_id, body.content)
        audit(tx, device, "session_post", "session", session_id, f"id={mem_id}")
    return {"ok": True, "id": mem_id, "session_id": session_id}


@router.get("/v1/sessions/{session_id}/list", tags=["sessions"])
def session_list(session_id: str, device: dict = Depends(get_current_device)):
    """读取会话黑板条目（本会话相关设备）。"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM memories WHERE scope='session' AND scope_ref=? "
        "AND deleted_at IS NULL AND superseded_by IS NULL ORDER BY id DESC LIMIT 50",
        (session_id,),
    ).fetchall()
    return {"results": [_row_to_dict(r) for r in rows]}


@router.delete("/v1/sessions/{session_id}", tags=["sessions"])
def session_close(session_id: str, device: dict = Depends(get_current_device)):
    """会话结束：黑板软删归档（审计可查）。"""
    with write_transaction() as tx:
        tx.execute(
            "UPDATE memories SET deleted_at=datetime('now','localtime') WHERE scope='session' "
            "AND scope_ref=? AND deleted_at IS NULL", (session_id,))
        audit(tx, device, "session_close", "session", session_id)
    return {"ok": True, "archived": True}