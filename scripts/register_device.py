#!/usr/bin/env python3
"""注册设备并签发 token（命令行一次性显示）。用法：
  python3 register_device.py --name srv-111 --role admin
  python3 register_device.py --name oracle-141            # 普通设备
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth import hash_for_store, issue_token  # noqa: E402
from app.db import init_db, get_connection, write_transaction  # noqa: E402
import secrets  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="memoryd 设备注册")
    parser.add_argument("--name", required=True, help="设备名（唯一）")
    parser.add_argument("--role", choices=["admin", "device"], default="device")
    args = parser.parse_args()

    init_db()
    conn = get_connection()
    if conn.execute("SELECT id FROM devices WHERE name=?", (args.name,)).fetchone():
        print(f"✗ 设备 {args.name} 已存在")
        return 1

    token = issue_token()
    salt = secrets.token_hex(16)
    with write_transaction() as tx:
        cur = tx.execute(
            "INSERT INTO devices(name, role, token_hash, token_salt) VALUES(?,?,?,?)",
            (args.name, args.role, hash_for_store(token, salt), salt),
        )
        device_id = cur.lastrowid
        tx.execute(
            "INSERT INTO audit_log(device_id, action, scope, scope_ref, detail) VALUES(?,?,?,?,?)",
            (device_id, "device_create", "device", str(device_id), f"cli name={args.name} role={args.role}"),
        )
    print(f"✓ 设备注册成功 id={device_id} name={args.name} role={args.role}")
    print(f"TOKEN={token}")
    print("（token 只显示这一次，请妥善保存；服务端仅存哈希）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())