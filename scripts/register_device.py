#!/usr/bin/env python3
"""agent 自助注册 memoryd：生成/复用本地指纹 → 提交注册请求 → 轮询等待 admin 批准 → 领取 token。

用法（新 agent 一条龙）:
  # 提交请求并阻塞等待批准（指纹持久化在 ~/.memoryd/fingerprint，重复运行幂等）
  python3 register_device.py --name my-agent --endpoint https://memoryd.235301.xyz --wait

  # 只提交不等待，稍后再来领取
  python3 register_device.py --name my-agent --endpoint https://memoryd.235301.xyz
  python3 register_device.py --name my-agent --endpoint https://memoryd.235301.xyz --claim-only

  # 领取后保存到文件（600 权限）
  python3 register_device.py --name my-agent --endpoint https://memoryd.235301.xyz --wait --token-out ~/.memoryd/token

依赖：仅 Python 标准库。流程：POST /v1/auth/register → admin 在管理台批准 → POST /v1/auth/register/claim。
"""
import argparse
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_FINGERPRINT = Path.home() / ".memoryd" / "fingerprint"


def http_json(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}
    except urllib.error.URLError as e:
        print(f"✗ 无法连接 {url}: {e.reason}", file=sys.stderr)
        sys.exit(2)


def load_or_create_fingerprint(path: Path) -> str:
    if path.exists():
        fp = path.read_text().strip()
        if len(fp) >= 16:
            return fp
    fp = secrets.token_hex(32)  # 256-bit
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fp + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return fp


def main() -> int:
    ap = argparse.ArgumentParser(description="memoryd 设备自助注册（审批流）")
    ap.add_argument("--endpoint", required=True, help="服务地址，如 https://memoryd.235301.xyz")
    ap.add_argument("--name", default="", help="设备名（--claim-only 时可省略）")
    ap.add_argument("--note", default="", help="备注（如机器用途，便于 admin 审批）")
    ap.add_argument("--fingerprint-file", type=Path, default=DEFAULT_FINGERPRINT,
                    help=f"指纹文件（默认 {DEFAULT_FINGERPRINT}，自动生成持久化）")
    ap.add_argument("--claim-only", action="store_true", help="跳过提交，直接领取")
    ap.add_argument("--wait", action="store_true", help="提交后轮询等待批准并领取 token")
    ap.add_argument("--interval", type=float, default=5, help="--wait 轮询间隔秒（默认 5）")
    ap.add_argument("--timeout", type=float, default=600, help="--wait 最长等待秒（默认 600）")
    ap.add_argument("--token-out", type=Path, help="token 保存到文件（600 权限）")
    args = ap.parse_args()

    base = args.endpoint.rstrip("/")
    fp = load_or_create_fingerprint(args.fingerprint_file)
    print(f"指纹: {fp[:16]}…（{args.fingerprint_file}）")

    if not args.claim_only:
        if not args.name:
            print("✗ 需要 --name（或 --claim-only）", file=sys.stderr)
            return 1
        submit_deadline = time.time() + max(args.timeout, 300)
        while True:
            code, r = http_json("POST", f"{base}/v1/auth/register",
                                {"name": args.name, "fingerprint": fp, "note": args.note})
            if code == 202:
                print(f"✓ 注册请求已提交 request_id={r['request_id']} 状态={r['status']}")
                print(r.get("detail", ""))
                break
            if code == 429:
                if time.time() >= submit_deadline:
                    print(f"✗ 限流且等待超时，请稍后重试或 --claim-only 领取", file=sys.stderr)
                    return 3
                print(f"… 限流中，{args.interval:.0f}s 后重试")
                time.sleep(args.interval)
                continue
            print(f"✗ 提交失败 HTTP {code}: {r.get('detail', r)}", file=sys.stderr)
            return 1
        if not args.wait:
            print("提示：等 admin 在管理台批准后，重新运行本命令加 --claim-only（或 --wait）领取 token。")
            return 0

    deadline = time.time() + args.timeout
    while True:
        code, r = http_json("POST", f"{base}/v1/auth/register/claim", {"fingerprint": fp})
        if code == 200 and r.get("status") == "issued" and r.get("token"):
            token = r["token"]
            print("✓ token 已领取（只显示这一次）:")
            print(f"TOKEN={token}")
            if args.token_out:
                args.token_out.parent.mkdir(parents=True, exist_ok=True)
                args.token_out.write_text(token + "\n")
                os.chmod(args.token_out, 0o600)
                print(f"已保存到 {args.token_out}（600 权限）")
            print("配置插件时填 apiKey（或环境变量 MEMORYD_API_KEY）。")
            return 0
        if code == 200 and r.get("status") == "pending":
            if not args.wait:
                print("状态：等待 admin 审批（稍后加 --wait 或 --claim-only 领取）")
                return 0
            if time.time() >= deadline:
                print(f"✗ 等待超时（{args.timeout}s），稍后可 --claim-only 再领。", file=sys.stderr)
                return 4
            time.sleep(args.interval)
            continue
        # 403 拒绝 / 404 无请求 / 410 已领取
        print(f"✗ 领取失败 HTTP {code}: {r.get('detail', r)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
