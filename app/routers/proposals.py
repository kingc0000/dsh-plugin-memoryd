"""申请与审批（/v1/proposals）：设备报送 scope 升级 / 项目加入 → admin 审批执行。

设计：
- 任意活跃设备可提交申请（type=scope_upgrade 需 memory_id 且可读该条目；project_join 由
  插件/LLM 裁决层在写 project 区被降级时自动报送）。
- admin 审批：approve 立即执行动作（升级记忆 scope / 添加项目成员），全程审计 + SSE 推送。
- 读权限：admin 可看全部申请；普通设备可看自己的申请。
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import audit, get_current_device, require_admin
from ..db import get_connection, write_transaction
from ..models import ProposalCreate, ProposalDecision
from ..search import index_memory
from .events import push_event

router = APIRouter()

_VALID_TARGET = ("user", "agent")


def _validate_target(target_scope: str, device: dict, conn) -> tuple[str, str]:
    """解析 target_scope → (scope, ref)。project:slug 要求 slug 存在。"""
    if target_scope in _VALID_TARGET:
        return target_scope, ("*" if target_scope == "agent" else "")
    if target_scope.startswith("project:"):
        slug = target_scope.split(":", 1)[1]
        if not conn.execute("SELECT id FROM projects WHERE slug=?", (slug,)).fetchone():
            raise HTTPException(status_code=404, detail=f"项目 {slug} 不存在")
        return "project", slug
    raise HTTPException(status_code=400, detail="target_scope 取值非法")


def create_proposal(device: dict, ptype: str, memory_id: int | None,
                    target_scope: str, reason: str, conn=None) -> int:
    """供路由与 ingest 内部复用：落一条 pending 申请。返回申请 id。"""
    conn = conn or get_connection()
    cur = conn.execute(
        "INSERT INTO proposals(device_id, type, memory_id, target_scope, reason) VALUES(?,?,?,?,?)",
        (device["id"], ptype, memory_id, target_scope, reason[:2000]),
    )
    conn.commit()
    return cur.lastrowid


@router.post("/v1/proposals", tags=["proposals"])
def submit_proposal(body: ProposalCreate, device: dict = Depends(get_current_device)):
    conn = get_connection()
    mem = None
    if body.type == "scope_upgrade":
        if not body.memory_id:
            raise HTTPException(status_code=400, detail="scope_upgrade 需要 memory_id")
        mem = conn.execute(
            "SELECT * FROM memories WHERE id=? AND deleted_at IS NULL AND superseded_by IS NULL",
            (body.memory_id,),
        ).fetchone()
        if mem is None:
            raise HTTPException(status_code=404, detail="记忆不存在或已失效")
        if mem["scope"] == body.target_scope or \
           (mem["scope"] == "project" and body.target_scope == f"project:{mem['scope_ref']}"):
            raise HTTPException(status_code=400, detail="目标 scope 与当前一致，无需升级")
    elif body.type == "project_join":
        if not body.target_scope.startswith("project:"):
            raise HTTPException(status_code=400, detail="project_join 的 target_scope 必须是 project:slug")
    _validate_target(body.target_scope, device, conn)
    with write_transaction() as tx:
        cur = tx.execute(
            "INSERT INTO proposals(device_id, type, memory_id, target_scope, reason) VALUES(?,?,?,?,?)",
            (device["id"], body.type, body.memory_id, body.target_scope, body.reason[:2000]),
        )
        pid = cur.lastrowid
        audit(tx, device, "proposal_create", "", body.target_scope,
              f"proposal={pid} type={body.type} memory={body.memory_id}")
    return {"id": pid, "status": "pending", "type": body.type,
            "target_scope": body.target_scope}


@router.get("/v1/proposals", tags=["proposals"])
def list_proposals(status: str = Query("pending", pattern="^(pending|approved|rejected|all)$"),
                    limit: int = Query(100, ge=1, le=500),
                    device: dict = Depends(get_current_device)):
    conn = get_connection()
    where, args = ["1=1"], []
    if status != "all":
        where.append("p.status=?")
        args.append(status)
    if device["role"] != "admin":
        where.append("p.device_id=?")
        args.append(device["id"])
    rows = conn.execute(
        "SELECT p.*, d.name AS device_name, m.content AS memory_content "
        "FROM proposals p JOIN devices d ON d.id=p.device_id "
        "LEFT JOIN memories m ON m.id=p.memory_id "
        f"WHERE {' AND '.join(where)} ORDER BY p.id DESC LIMIT ?",
        (*args, limit),
    ).fetchall()
    return {"results": [dict(r) for r in rows], "count": len(rows)}


def _approve(pid: int, prop: dict, admin: dict, note: str) -> dict:
    """执行审批通过的申请动作。"""
    conn = get_connection()
    ptype = prop["type"]
    target = prop["target_scope"]
    if ptype == "scope_upgrade":
        mem = conn.execute(
            "SELECT * FROM memories WHERE id=? AND deleted_at IS NULL AND superseded_by IS NULL",
            (prop["memory_id"],),
        ).fetchone()
        if mem is None:
            raise HTTPException(status_code=409, detail="目标记忆已不存在或被更新")
        scope, ref = _validate_target(target, admin, conn)
        # 内容红线：升级到共享区前扫描
        from ..auth import assert_no_secret
        assert_no_secret(mem["content"], scope)
        with write_transaction() as tx:
            cur = tx.execute(
                "INSERT INTO memories(scope, scope_ref, content, kind, tags, origin, source_ref, "
                "confidence, valid_until, version) "
                "SELECT ?, ?, content, kind, tags, origin, source_ref, confidence, valid_until, version+1 "
                "FROM memories WHERE id=?",
                (scope, ref, mem["id"]),
            )
            new_id = cur.lastrowid
            tx.execute("UPDATE memories SET superseded_by=? WHERE id=?", (new_id, mem["id"]))
            index_memory(tx, new_id, mem["content"])
            tx.execute(
                "UPDATE proposals SET status='approved', decided_at=datetime('now','localtime'), decided_by=? WHERE id=?",
                (admin["id"], pid),
            )
            audit(tx, admin, "proposal_approve", mem["scope"], mem["scope_ref"],
                  f"proposal={pid} {mem['id']}→{scope}/{ref} {note[:100]}")
        push_event("memory.promote", scope, ref, new_id, admin["id"])
        return {"ok": True, "action": "scope_upgrade", "new_memory_id": new_id,
                "scope": scope, "scope_ref": ref}

    if ptype == "project_join":
        if not target.startswith("project:"):
            raise HTTPException(status_code=400, detail="project_join 目标必须是 project:slug")
        slug = target.split(":", 1)[1]
        project = conn.execute("SELECT id FROM projects WHERE slug=?", (slug,)).fetchone()
        if project is None:
            raise HTTPException(status_code=404, detail=f"项目 {slug} 不存在")
        with write_transaction() as tx:
            tx.execute(
                "INSERT OR REPLACE INTO memberships(project_id, device_id, role) VALUES(?,?,'member')",
                (project["id"], prop["device_id"]),
            )
            tx.execute(
                "UPDATE proposals SET status='approved', decided_at=datetime('now','localtime'), decided_by=? WHERE id=?",
                (admin["id"], pid),
            )
            audit(tx, admin, "proposal_approve", "project", slug,
                  f"proposal={pid} device={prop['device_id']} 加入为 member {note[:100]}")
        return {"ok": True, "action": "project_join", "slug": slug,
                "device_id": prop["device_id"], "role": "member"}

    raise HTTPException(status_code=400, detail="未知申请类型")


@router.post("/v1/proposals/{pid}:approve", tags=["proposals"])
def approve_proposal(pid: int, body: ProposalDecision,
                     admin: dict = Depends(require_admin)):
    conn = get_connection()
    prop = conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
    if prop is None:
        raise HTTPException(status_code=404, detail="申请不存在")
    if prop["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"申请已处理（{prop['status']}）")
    return _approve(pid, dict(prop), admin, body.note)


@router.post("/v1/proposals/{pid}:reject", tags=["proposals"])
def reject_proposal(pid: int, body: ProposalDecision,
                    admin: dict = Depends(require_admin)):
    conn = get_connection()
    prop = conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
    if prop is None:
        raise HTTPException(status_code=404, detail="申请不存在")
    if prop["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"申请已处理（{prop['status']}）")
    with write_transaction() as tx:
        tx.execute(
            "UPDATE proposals SET status='rejected', decided_at=datetime('now','localtime'), decided_by=? WHERE id=?",
            (admin["id"], pid),
        )
        audit(tx, admin, "proposal_reject", "", prop["target_scope"],
              f"proposal={pid} {body.note[:100]}")
    return {"ok": True, "status": "rejected"}