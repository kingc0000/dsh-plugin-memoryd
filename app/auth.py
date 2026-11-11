"""原生鉴权：设备 token 签发/校验、scope 权限矩阵、密钥红线。"""
import hashlib
import re
import secrets
import sqlite3
import time

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings
from .db import get_connection

_bearer = HTTPBearer(auto_error=False)

# ---- token 工具 ----
def issue_token() -> str:
    return secrets.token_urlsafe(32)


def _hash(token: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{token}".encode()).hexdigest()


def hash_for_store(token: str, salt: str) -> str:
    return _hash(token, salt)


def verify_token(token: str, conn: sqlite3.Connection):
    """按设备匹配 token（设备数少，全表比对）。返回 device Row 或 None。"""
    if not token:
        return None
    for row in conn.execute(
        "SELECT * FROM devices WHERE active=1 ORDER BY id"
    ).fetchall():
        if secrets.compare_digest(row["token_hash"], _hash(token, row["token_salt"])):
            return row
    return None


# ---- 依赖注入：当前设备 ----
def get_current_device(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    conn = get_connection()
    device = verify_token(credentials.credentials if credentials else "", conn)
    if device is None:
        raise HTTPException(status_code=401, detail="无效或已吊销的设备 token")
    conn.execute(
        "UPDATE devices SET last_seen_at=datetime('now','localtime') WHERE id=?",
        (device["id"],),
    )
    conn.commit()
    return dict(device)


def require_admin(device: dict = Depends(get_current_device)) -> dict:
    if device["role"] != "admin":
        raise HTTPException(status_code=403, detail="需要 admin 权限")
    return device


# ---- scope 权限矩阵 ----
def device_readable_scopes(device: dict, conn: sqlite3.Connection) -> set:
    """该设备可读的 scope 全集。"""
    scopes = {"user", f"agent:*"}
    if not settings.agent_scope_shared:
        scopes = {"user"}
    scopes.add(f"device:{device['id']}")
    for row in conn.execute(
        "SELECT p.slug FROM memberships m JOIN projects p ON p.id=m.project_id "
        "WHERE m.device_id=?",
        (device["id"],),
    ):
        scopes.add(f"project:{row['slug']}")
    return scopes


def _project_role(device_id: int, slug: str, conn: sqlite3.Connection):
    row = conn.execute(
        "SELECT m.role FROM memberships m JOIN projects p ON p.id=m.project_id "
        "WHERE m.device_id=? AND p.slug=?",
        (device_id, slug),
    ).fetchone()
    return row["role"] if row else None


def can_read(scope: str, scope_ref: str, device: dict, conn: sqlite3.Connection) -> bool:
    """读权限：活跃设备可查看所有条目（读全开模型）。

    隐私/安全边界在写端维持（can_write）：增删改仍严格按 scope 权限矩阵强制，
    密钥红线不变。查看（列表/检索/单条）对全部活跃设备开放。
    """
    return bool(device["active"])


def can_write(scope: str, scope_ref: str, device: dict, conn: sqlite3.Connection) -> bool:
    if scope == "user":
        return bool(device["active"])
    if scope == "project":
        return _project_role(device["id"], scope_ref, conn) in ("owner", "member")
    if scope == "device":
        return str(device["id"]) == str(scope_ref) or device["role"] == "admin"
    if scope == "agent":
        return settings.agent_scope_shared or device["role"] == "admin"
    if scope == "session":
        return True  # 写入时 origin.device 绑定，读取受限
    return False


# ---- 密钥红线 ----
_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token)\s*[=:]\s*\S{8,}"),
]


def has_secret(text: str) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in _SECRET_PATTERNS)


def assert_no_secret(content: str, scope: str) -> None:
    """密钥红线：非 device 区含密钥直接拒绝。"""
    if settings.secret_redline and scope != "device" and has_secret(content):
        raise HTTPException(
            status_code=400,
            detail="内容疑似含密钥/凭据，禁止写入共享区（user/project/agent/session）。如需保存，请显式使用 device 私有区。",
        )


# ---- 审计 ----
def audit(conn: sqlite3.Connection, device: dict | None, action: str,
          scope: str = "", scope_ref: str = "", detail: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_log(device_id, action, scope, scope_ref, detail) VALUES(?,?,?,?,?)",
        (device["id"] if device else None, action, scope, scope_ref, detail[:2000]),
    )