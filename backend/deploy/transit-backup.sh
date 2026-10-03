#!/bin/sh
# Weekly consistent copy of the SQLite DB (online backup API, safe while the
# collector is writing). Keeps the newest 2 compressed copies. Only old BACKUP
# files are pruned, never the live database.
set -e
DIR=/var/backups/transit
mkdir -p "$DIR"
OUT="$DIR/transit-$(date +%F).db"
python3 - "$OUT" <<'PY'
import sqlite3, sys
src = sqlite3.connect("file:/opt/adelaide-transit/data/transit.db?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[1])
src.backup(dst)
dst.close()
PY
gzip -f "$OUT"
ls -1t "$DIR"/transit-*.db.gz | tail -n +3 | xargs -r rm -f
