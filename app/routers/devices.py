"""设备管理与鉴权相关端点（/v1/auth, /v1/devices, /v1/registrations）。"""
import secrets as pysecrets
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth import audit, get_current_device, hash_for_store, issue_token, require_admin
from ..db import write_transaction, get_connection
from ..models import DeviceCreate, DeviceCreateResponse, RegisterBody, RegisterClaimBody

router = APIRouter()

# ---- 匿名端点限流（每 IP 时间窗内计数）----
_rate_lock = threading.Lock()
_rate_hits: dict[str, deque] = defaultdict(deque)
_RATE_WINDOW_S = 60


def _rate_limit(request: Request, key: str, max_hits: int) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    with _rate_lock:
        q = _rate_hits[f"{key}:{ip}"]
        while q and now - q[0] > _RATE_WINDOW_S:
            q.popleft()
        if len(q) >= max_hits:
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
        q.append(now)



@router.get("/v1/devices", tags=["devices"])
def list_devices(limit: int = Query(20, ge=1, le=500), offset: int = Query(0, ge=0),
                 admin: dict = Depends(require_admin)):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM devices ORDER BY id LIMIT ? OFFSET ?", (limit, offset)
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) AS c FROM devices").fetchone()["c"]
    return {
        "results": [
            {
                "id": d["id"], "name": d["name"], "role": d["role"],
                "active": d["active"], "last_seen_at": d["last_seen_at"],
                "created_at": d["created_at"],
            }
            for d in rows
        ],
        "total": total, "limit": limit, "offset": offset,
    }


@router.post("/v1/devices", response_model=DeviceCreateResponse, tags=["devices"])
def create_device(body: DeviceCreate, admin: dict = Depends(require_admin)):
    conn = get_connection()
    exists = conn.execute("SELECT id FROM devices WHERE name=?", (body.name,)).fetchone()
    if exists:
        raise HTTPException(status_code=409, detail=f"设备名 {body.name} 已存在")
    token = issue_token()
    salt = pysecrets.token_hex(16)
    with write_transaction() as tx:
        cur = tx.execute(
            "INSERT INTO devices(name, role, token_hash, token_salt) VALUES(?,?,?,?)",
            (body.name, body.role, hash_for_store(token, salt), salt),
        )
        device_id = cur.lastrowid
        audit(tx, admin, "device_create", "device", str(device_id), f"name={body.name} role={body.role}")
    return DeviceCreateResponse(device_id=device_id, name=body.name, role=body.role, token=token)


@router.delete("/v1/devices/{device_id}/token", tags=["devices"])
def revoke_device_token(device_id: int, admin: dict = Depends(require_admin)):
    with write_transaction() as tx:
        row = tx.execute("SELECT id FROM devices WHERE id=?", (device_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="设备不存在")
        tx.execute("UPDATE devices SET token_hash='revoked', token_salt='' WHERE id=?", (device_id,))
        audit(tx, admin, "device_revoke", "device", str(device_id))
    return {"ok": True, "detail": f"设备 {device_id} token 已吊销"}


@router.post("/v1/devices/{device_id}/token", tags=["devices"])
def rotate_device_token(device_id: int, admin: dict = Depends(require_admin)):
    conn = get_connection()
    row = conn.execute("SELECT name, role FROM devices WHERE id=?", (device_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    token = issue_token()
    salt = pysecrets.token_hex(16)
    with write_transaction() as tx:
        tx.execute(
            "UPDATE devices SET token_hash=?, token_salt=?, active=1 WHERE id=?",
            (hash_for_store(token, salt), salt, device_id),
        )
        audit(tx, admin, "device_rotate", "device", str(device_id))
    return {"device_id": device_id, "token": token, "note": "旧 token 已失效"}


@router.post("/v1/devices/{device_id}/deactivate", tags=["devices"])
def deactivate_device(device_id: int, admin: dict = Depends(require_admin)):
    with write_transaction() as tx:
        tx.execute("UPDATE devices SET active=0 WHERE id=?", (device_id,))
        audit(tx, admin, "device_deactivate", "device", str(device_id))
    return {"ok": True, "detail": f"设备 {device_id} 已停用"}


@router.post("/v1/devices/{device_id}/activate", tags=["devices"])
def activate_device(device_id: int, admin: dict = Depends(require_admin)):
    with write_transaction() as tx:
        row = tx.execute("SELECT token_hash FROM devices WHERE id=?", (device_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="设备不存在")
        if row["token_hash"] == "revoked":
            raise HTTPException(status_code=409, detail="token 已吊销，恢复请先轮换签发新 token")
        tx.execute("UPDATE devices SET active=1 WHERE id=?", (device_id,))
        audit(tx, admin, "device_activate", "device", str(device_id))
    return {"ok": True, "detail": f"设备 {device_id} 已恢复活跃"}


@router.get("/v1/auth/whoami", tags=["auth"])
def whoami(device: dict = Depends(get_current_device)):
    return {"device_id": device["id"], "name": device["name"], "role": device["role"]}


# ---- 设备自助注册审批流 ----
# 流程：agent 匿名 POST /v1/auth/register（带指纹）→ admin 批准 →
#       agent POST /v1/auth/register/claim（同指纹）领取一次性 token

@router.post("/v1/auth/register", tags=["auth"], status_code=202)
def register(body: RegisterBody, request: Request):
    """匿名提交注册请求，等待 admin 审批。同指纹重复请求幂等返回。"""
    _rate_limit(request, "register", 5)
    conn = get_connection()
    fp = body.fingerprint.strip()
    # 同指纹已有待审/已批未领的请求 → 幂等返回同一个 request_id
    row = conn.execute(
        "SELECT id, status FROM registration_requests WHERE fingerprint=? AND status IN('pending','approved') ORDER BY id DESC LIMIT 1",
        (fp,),
    ).fetchone()
    if row:
        return {"request_id": row["id"], "status": row["status"],
                "detail": "已有同指纹的注册请求，等待审批或领取" if row["status"] == "pending" else "已批准，请领取 token"}
    with write_transaction() as tx:
        cur = tx.execute(
            "INSERT INTO registration_requests(name, fingerprint, note) VALUES(?,?,?)",
            (body.name.strip(), fp, body.note.strip()),
        )
        rid = cur.lastrowid
    return {"request_id": rid, "status": "pending", "detail": "已提交，等待 admin 审批"}


@router.post("/v1/auth/register/claim", tags=["auth"])
def register_claim(body: RegisterClaimBody, request: Request):
    """凭指纹领取 token。pending→202 等待；approved→返回一次性 token；rejected→403；issued→410。"""
    _rate_limit(request, "claim", 15)
    conn = get_connection()
    fp = body.fingerprint.strip()
    rows = conn.execute(
        "SELECT id, name, status, issued_token FROM registration_requests WHERE fingerprint=? ORDER BY id DESC LIMIT 1",
        (fp,),
    ).fetchone()
    if rows is None:
        raise HTTPException(status_code=404, detail="无此指纹的注册请求")
    if rows["status"] == "pending":
        return {"request_id": rows["id"], "status": "pending", "detail": "等待 admin 审批"}
    if rows["status"] == "rejected":
        raise HTTPException(status_code=403, detail="注册请求已被拒绝")
    if rows["status"] == "issued" or not rows["issued_token"]:
        raise HTTPException(status_code=410, detail="token 已被领取，如需重发请联系 admin 轮换设备 token")
    # approved：一次性发放并立刻清空明文
    with write_transaction() as tx:
        row = tx.execute(
            "SELECT issued_token, status FROM registration_requests WHERE id=?",
            (rows["id"],),
        ).fetchone()
        if row is None or row["status"] != "approved" or not row["issued_token"]:
            raise HTTPException(status_code=410, detail="token 已被领取")
        token = row["issued_token"]
        tx.execute(
            "UPDATE registration_requests SET status='issued', issued_token=NULL WHERE id=?",
            (rows["id"],),
        )
    return {"request_id": rows["id"], "status": "issued", "token": token,
            "note": "token 只显示这一次，请立即保存"}


@router.get("/v1/registrations", tags=["registrations"])
def list_registrations(status: str = Query("pending", pattern="^(pending|approved|rejected|issued|all)$"),
                        limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0),
                        admin: dict = Depends(require_admin)):
    conn = get_connection()
    where = "" if status == "all" else "WHERE status=?"
    args = () if status == "all" else (status,)
    rows = conn.execute(
        f"SELECT id, name, note, status, device_id, created_at, decided_at FROM registration_requests {where} ORDER BY id DESC LIMIT ? OFFSET ?",
        args + (limit, offset),
    ).fetchall()
    total = conn.execute(
        f"SELECT COUNT(*) AS c FROM registration_requests {where}", args
    ).fetchone()["c"]
    return {"results": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.post("/v1/registrations/{rid}:approve", tags=["registrations"])
def approve_registration(rid: int, admin: dict = Depends(require_admin)):
    """批准：创建设备 + 生成 token 暂存，agent 凭指纹领取。"""
    conn = get_connection()
    req = conn.execute(
        "SELECT id, name, status FROM registration_requests WHERE id=?", (rid,)
    ).fetchone()
    if req is None:
        raise HTTPException(status_code=404, detail="注册请求不存在")
    if req["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"请求状态为 {req['status']}，仅 pending 可批准")
    if conn.execute("SELECT id FROM devices WHERE name=?", (req["name"],)).fetchone():
        raise HTTPException(status_code=409, detail=f"设备名 {req['name']} 已存在，请先改名")
    token = issue_token()
    salt = pysecrets.token_hex(16)
    with write_transaction() as tx:
        cur = tx.execute(
            "INSERT INTO devices(name, role, token_hash, token_salt) VALUES(?,?,?,?)",
            (req["name"], "device", hash_for_store(token, salt), salt),
        )
        device_id = cur.lastrowid
        tx.execute(
            "UPDATE registration_requests SET status='approved', device_id=?, issued_token=?, decided_at=datetime('now','localtime'), decided_by=? WHERE id=?",
            (device_id, token, admin["id"], rid),
        )
        audit(tx, admin, "registration_approve", "device", str(device_id),
              f"request={rid} name={req['name']}")
    return {"ok": True, "registration_id": rid, "device_id": device_id,
            "detail": f"已批准 {req['name']}（设备 {device_id}），等待对方凭指纹领取 token"}


@router.post("/v1/registrations/{rid}:reject", tags=["registrations"])
def reject_registration(rid: int, admin: dict = Depends(require_admin)):
    with write_transaction() as tx:
        req = tx.execute(
            "SELECT id, name, status FROM registration_requests WHERE id=?", (rid,)
        ).fetchone()
        if req is None:
            raise HTTPException(status_code=404, detail="注册请求不存在")
        if req["status"] != "pending":
            raise HTTPException(status_code=409, detail=f"请求状态为 {req['status']}，仅 pending 可拒绝")
        tx.execute(
            "UPDATE registration_requests SET status='rejected', issued_token=NULL, decided_at=datetime('now','localtime'), decided_by=? WHERE id=?",
            (admin["id"], rid),
        )
        audit(tx, admin, "registration_reject", "device", str(rid), f"name={req['name']}")
    return {"ok": True, "detail": "已拒绝"}