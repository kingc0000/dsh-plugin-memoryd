"""insight 手动触发端点（/v1/insights/run）。"""
from fastapi import APIRouter, Depends

from ..auth import require_admin
from .. import insight

router = APIRouter()


@router.post("/v1/insights/run", tags=["insights"])
def run_now(admin: dict = Depends(require_admin)):
    return insight.run_insight()


@router.get("/v1/insights", tags=["insights"])
def list_insights(device: dict = Depends(require_admin)):
    from ..db import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, content, created_at FROM memories WHERE kind='insight' "
        "AND deleted_at IS NULL AND superseded_by IS NULL ORDER BY id DESC LIMIT 20"
    ).fetchall()
    return {"results": [dict(r) for r in rows]}