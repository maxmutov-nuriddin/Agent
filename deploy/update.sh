#!/usr/bin/env bash
# Serverda yangilash: kodni tortadi, kutubxonalarni yangilaydi va qayta ishga tushiradi.
# Ishlatish:  bash /opt/aijamoa/src/deploy/update.sh
set -euo pipefail
cd /opt/aijamoa/src
git pull --ff-only
/opt/aijamoa/venv/bin/pip install -q -r requirements.txt
chown -R aijamoa:aijamoa /opt/aijamoa
systemctl restart aijamoa
sleep 4
systemctl is-active aijamoa
journalctl -u aijamoa -n 8 --no-pager
