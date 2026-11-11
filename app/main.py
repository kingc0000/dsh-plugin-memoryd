"""memoryd 入口：FastAPI 组装。"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import __version__
from . import insight
from .db import init_db
from .routers import audit, devices, events, ingest, insights, memories, projects, proposals, sessions

app = FastAPI(
    title="memoryd",
    description="DSH 全新记忆体系 API 服务端（设备 token 鉴权 / scope 多租户隔离 / 中文 BM25 检索）",
    version=__version__,
)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    insight.start_scheduler()


@app.get("/v1/health", tags=["health"])
def health():
    return {"ok": True, "service": "memoryd", "version": __version__}


@app.get("/v1/health/ready", tags=["health"])
def ready():
    """健康+数据库连通（活跃记忆数 = 未软删且未被版本链取代）。"""
    from .db import get_connection
    conn = get_connection()
    n_devices = conn.execute("SELECT COUNT(*) c FROM devices").fetchone()["c"]
    n_memories = conn.execute(
        "SELECT COUNT(*) c FROM memories WHERE deleted_at IS NULL AND superseded_by IS NULL"
    ).fetchone()["c"]
    return {"ok": True, "devices": n_devices, "memories": n_memories}


app.include_router(devices.router)
app.include_router(projects.router)
app.include_router(memories.router)
app.include_router(sessions.router)
app.include_router(ingest.router)
app.include_router(events.router)
app.include_router(insights.router)
app.include_router(audit.router)
app.include_router(proposals.router)

# 可视化管理台（只读静态页）：/ui
_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=str(_STATIC_DIR), html=True), name="ui")