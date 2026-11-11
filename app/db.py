"""SQLite（WAL）连接与 schema。"""
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  role TEXT NOT NULL DEFAULT 'device',          -- admin | device
  token_hash TEXT NOT NULL,
  token_salt TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  last_seen_at TEXT,
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS memberships(
  project_id INTEGER NOT NULL REFERENCES projects(id),
  device_id INTEGER NOT NULL REFERENCES devices(id),
  role TEXT NOT NULL DEFAULT 'member',          -- owner | member | readonly
  PRIMARY KEY(project_id, device_id)
);

CREATE TABLE IF NOT EXISTS memories(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope TEXT NOT NULL,                          -- user|project|device|agent|session
  scope_ref TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'fact',            -- fact|preference|procedure|observation|insight
  tags TEXT NOT NULL DEFAULT '[]',              -- JSON array
  origin TEXT NOT NULL DEFAULT '{}',            -- JSON {device_id,agent_id,session_id}
  source_ref TEXT,
  confidence REAL NOT NULL DEFAULT 0.5,
  valid_until TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  superseded_by INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_mem_scope ON memories(scope, scope_ref);
CREATE INDEX IF NOT EXISTS idx_mem_live ON memories(superseded_by, deleted_at);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(seg);

CREATE TABLE IF NOT EXISTS proposals(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id INTEGER NOT NULL REFERENCES devices(id),  -- 提出申请的设备
  type TEXT NOT NULL,                                 -- scope_upgrade | project_join
  memory_id INTEGER,                                  -- scope_upgrade: 待升级条目 id
  target_scope TEXT NOT NULL,                         -- user | agent | project:slug
  reason TEXT,                                         -- 申请理由/建议依据
  status TEXT NOT NULL DEFAULT 'pending',             -- pending | approved | rejected
  decided_at TEXT,
  decided_by INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_prop_status ON proposals(status, id);

CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id INTEGER,
  action TEXT NOT NULL,                         -- write|update|delete|promote|device_create|...
  scope TEXT,
  scope_ref TEXT,
  detail TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS registration_requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,                           -- agent 自报设备名
  fingerprint TEXT NOT NULL,                    -- agent 侧持久化的随机指纹（领取凭证）
  note TEXT,                                    -- 备注（如机器信息/用途）
  status TEXT NOT NULL DEFAULT 'pending',       -- pending | approved | rejected | issued
  device_id INTEGER,                            -- 批准后关联的设备 id
  issued_token TEXT,                            -- 批准后暂存的一次性 token（领取后清空）
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  decided_at TEXT,
  decided_by INTEGER
);
CREATE INDEX IF NOT EXISTS idx_reg_status ON registration_requests(status, id);
CREATE INDEX IF NOT EXISTS idx_reg_fp ON registration_requests(fingerprint);
"""

_db_local = threading.local()
_write_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_connection() -> sqlite3.Connection:
    """线程级连接（FastAPI 每线程一个）。"""
    conn = getattr(_db_local, "conn", None)
    if conn is None:
        conn = _connect()
        _db_local.conn = conn
    return conn


@contextmanager
def write_transaction():
    """全局单写锁 + 事务（SQLite 单写者，WAL 下安全）。"""
    with _write_lock:
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def now_iso() -> str:
    """当前本地时间（Asia/Shanghai），格式与 SQLite datetime 一致。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_db() -> None:
    import os
    os.makedirs(os.path.dirname(settings.db_path), exist_ok=True)
    conn = _connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()