"""项目管理（/v1/projects, /v1/memberships）。"""
from fastapi import APIRouter, Depends, HTTPException

from ..auth import audit, require_admin
from ..db import get_connection, write_transaction
from ..models import MembershipUpdate, ProjectCreate

router = APIRouter()


@router.get("/v1/projects", tags=["projects"])
def list_projects(device: dict = Depends(require_admin)):
    conn = get_connection()
    return [
        {"slug": p["slug"], "name": p["name"], "created_at": p["created_at"]}
        for p in conn.execute("SELECT * FROM projects ORDER BY id").fetchall()
    ]


@router.post("/v1/projects", tags=["projects"])
def create_project(body: ProjectCreate, admin: dict = Depends(require_admin)):
    conn = get_connection()
    exists = conn.execute("SELECT id FROM projects WHERE slug=?", (body.slug,)).fetchone()
    if exists:
        raise HTTPException(status_code=409, detail=f"项目 slug {body.slug} 已存在")
    with write_transaction() as tx:
        cur = tx.execute("INSERT INTO projects(slug, name) VALUES(?,?)", (body.slug, body.name))
        tx.execute(
            "INSERT OR REPLACE INTO memberships(project_id, device_id, role) VALUES(?,?, 'owner')",
            (cur.lastrowid, admin["id"]),
        )
        audit(tx, admin, "project_create", "project", body.slug)
    return {"id": cur.lastrowid, "slug": body.slug, "name": body.name}


@router.post("/v1/memberships", tags=["projects"])
def add_membership(body: MembershipUpdate, admin: dict = Depends(require_admin)):
    conn = get_connection()
    project = conn.execute("SELECT id FROM projects WHERE slug=?", (body.slug,)).fetchone()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    with write_transaction() as tx:
        tx.execute(
            "INSERT OR REPLACE INTO memberships(project_id, device_id, role) VALUES(?,?,?)",
            (project["id"], body.device_id, body.role),
        )
        audit(tx, admin, "membership_set", "project", body.slug,
              f"device={body.device_id} role={body.role}")
    return {"ok": True}


@router.delete("/v1/memberships/{slug}/{device_id}", tags=["projects"])
def remove_membership(slug: str, device_id: int, admin: dict = Depends(require_admin)):
    conn = get_connection()
    project = conn.execute("SELECT id FROM projects WHERE slug=?", (slug,)).fetchone()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    with write_transaction() as tx:
        tx.execute("DELETE FROM memberships WHERE project_id=? AND device_id=?", (project["id"], device_id))
        audit(tx, admin, "membership_remove", "project", slug, f"device={device_id}")
    return {"ok": True}