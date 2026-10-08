#!/usr/bin/env bash
# Bazaning zaxira nusxasi: har kuni tunda systemd taymeri ishga tushiradi (deploy/install-backup.sh).
# Postgres (Supabase): faqat ilova jadvallari (public sxema) pg_dump bilan, gzip. SQLite: fayl nusxasi.
# Oxirgi KEEP ta nusxa qoladi, eskilari o'chadi. Qo'lda: bash /opt/aijamoa/src/deploy/backup.sh
set -euo pipefail
ENV_FILE="${AIJ_ENV:-/opt/aijamoa/src/.env}"
DIR="${AIJ_BACKUP_DIR:-/opt/aijamoa/backups}"
KEEP="${AIJ_BACKUP_KEEP:-14}"
PY="${AIJ_PY:-/opt/aijamoa/venv/bin/python}"

umask 077
mkdir -p "$DIR"
URL="$(grep -E '^DATABASE_URL=' "$ENV_FILE" | tail -n1 | cut -d= -f2- | tr -d '"'"'"' \r')"
[ -n "$URL" ] || { echo "DATABASE_URL topilmadi: $ENV_FILE"; exit 1; }
TS="$(date +%Y-%m-%d_%H%M)"

case "$URL" in
  postgres*)
    URL="${URL/+asyncpg/}"; URL="${URL/ssl=require/sslmode=require}"   # SQLAlchemy shakli -> libpq shakli
    PGDUMP=/usr/lib/postgresql/17/bin/pg_dump; [ -x "$PGDUMP" ] || PGDUMP=pg_dump
    OUT="$DIR/aijamoa-$TS.sql.gz"
    "$PGDUMP" "$URL" --schema=public --no-owner --no-privileges | gzip -9 > "$OUT.tmp"
    gzip -t "$OUT.tmp"
    ;;
  sqlite*)
    DB="${URL#*:///}"; OUT="$DIR/aijamoa-$TS.db.gz"
    "$PY" - "$DB" "$OUT.raw" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
src.backup(dst); dst.close(); src.close()
PY
    gzip -9 -c "$OUT.raw" > "$OUT.tmp"; rm -f "$OUT.raw"
    ;;
  *) echo "Noma'lum baza turi: ${URL%%:*}"; exit 1 ;;
esac

[ -s "$OUT.tmp" ] || { rm -f "$OUT.tmp"; echo "Nusxa bo'sh chiqdi"; exit 1; }
mv "$OUT.tmp" "$OUT"
ls -1t "$DIR"/aijamoa-* 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f
echo "Zaxira nusxa tayyor: $OUT ($(du -h "$OUT" | cut -f1)). Saqlanganlar: $(ls -1 "$DIR"/aijamoa-* | wc -l)"
