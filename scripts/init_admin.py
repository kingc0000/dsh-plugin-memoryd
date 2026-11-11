#!/usr/bin/env python3
"""初始化首个 admin 设备（本地直插 DB，bootstrap 用）。用法：
  python3 init_admin.py --name admin
后续新设备请用 register_device.py（自助注册审批流）或管理台手动创建。
"""
import argparse
import secrets as pysecrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth import hash_for_store, issue_token  # noqa: E402
from app.db import init_db, get_connection, write_transaction  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="memoryd 初始化首个 admin")
    parser.add_argument("--name", default="admin", help="admin 设备名（默认 admin）")
    args = parser.parse_args()

    init_db()
    conn = get_connection()
    if conn.execute("SELECT id FROM devices WHERE role='admin'").fetchone():
        print("✗ 已存在 admin 设备（bootstrap 只用一次）；新设备请用 register_device.py 或管理台")
        return 1
    if conn.execute("SELECT id FROM devices WHERE name=?", (args.name,)).fetchone():
        print(f"✗ 设备 {args.name} 已存在")
        return 1

    token = issue_token()
    salt = pysecrets.token_hex(16)
    with write_transaction() as tx:
        cur = tx.execute(
            "INSERT INTO devices(name, role, token_hash, token_salt) VALUES(?,?,?,?)",
            (args.name, "admin", hash_for_store(token, salt), salt),
        )
        device_id = cur.lastrowid
        tx.execute(
            "INSERT INTO audit_log(device_id, action, scope, scope_ref, detail) VALUES(?,?,?,?,?)",
            (device_id, "device_create", "device", str(device_id), f"bootstrap admin name={args.name}"),
        )
    print(f"✓ admin 已创建 id={device_id} name={args.name}")
    print(f"TOKEN={token}")
    print("（token 只显示这一次，请妥善保存；服务端仅存哈希）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
