"""设备管理与鉴权相关端点（/v1/auth, /v1/devices）。"""
import secrets as pysecrets

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import audit, get_current_device, hash_for_store, issue_token, require_admin
from ..db import write_transaction, get_connection
from ..models import DeviceCreate, DeviceCreateResponse

router = APIRouter()


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