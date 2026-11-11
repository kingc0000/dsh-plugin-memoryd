"""审计流水查询（/v1/audit，admin only，只读）。"""
from fastapi import APIRouter, Depends, Query

from ..auth import require_admin
from ..db import get_connection

router = APIRouter()


@router.get("/v1/audit", tags=["audit"])
def list_audit(limit: int = Query(20, ge=1, le=500), offset: int = Query(0, ge=0),
               device: dict = Depends(require_admin)):
    conn = get_connection()
    rows = conn.execute(
        "SELECT a.id, a.device_id, d.name AS device_name, a.action, a.scope, a.scope_ref, "
        "a.detail, a.created_at FROM audit_log a "
        "LEFT JOIN devices d ON d.id = a.device_id "
        "ORDER BY a.id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"]
    return {"results": [dict(r) for r in rows], "total": total,
            "limit": limit, "offset": offset}