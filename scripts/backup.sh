#!/bin/bash
# memoryd 每日备份：SQLite 在线安全备份（python sqlite3 backup API）+ 滚动保留 7 份
set -euo pipefail
SRC=/var/lib/memoryd/memoryd.db
DEST=/root/backup/memoryd
mkdir -p "$DEST"
STAMP=$(date +%Y%m%d-%H%M)
OUT="$DEST/memoryd-$STAMP.db"
python3 - "$SRC" "$OUT" <<'PYEOF'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
src.close()
dst.close()
PYEOF
# 滚动保留最近 7 份
ls -1t "$DEST"/memoryd-*.db 2>/dev/null | tail -n +8 | xargs -r rm -f
echo "$(date '+%F %T') backup ok: $OUT ($(du -h "$OUT" | cut -f1))"