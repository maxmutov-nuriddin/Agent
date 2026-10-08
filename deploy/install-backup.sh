#!/usr/bin/env bash
# Kunlik zaxira nusxani yoqadi: pg_dump 17 (Supabase Postgres 17 bilan mos), systemd taymer (har kuni 03:00, Toshkent vaqti).
# Ishlatish (root): bash /opt/aijamoa/src/deploy/install-backup.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "root sifatida ishga tushiring"; exit 1; }
export DEBIAN_FRONTEND=noninteractive
if [ ! -x /usr/lib/postgresql/17/bin/pg_dump ]; then
  apt-get update -y
  apt-get install -y postgresql-common
  /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
  apt-get install -y postgresql-client-17
fi

cat > /etc/systemd/system/aijamoa-backup.service <<'UNIT'
[Unit]
Description=AI Jamoa: bazaning zaxira nusxasi
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/bash /opt/aijamoa/src/deploy/backup.sh
UNIT

cat > /etc/systemd/system/aijamoa-backup.timer <<'UNIT'
[Unit]
Description=AI Jamoa: har kuni tunda zaxira nusxa

[Timer]
OnCalendar=*-*-* 03:00:00 Asia/Tashkent
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now aijamoa-backup.timer
echo "==> Sinov: hozir bitta nusxa olinadi"
systemctl start aijamoa-backup.service
journalctl -u aijamoa-backup -n 5 --no-pager
echo
systemctl list-timers aijamoa-backup.timer --no-pager
