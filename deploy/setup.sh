#!/usr/bin/env bash
# Ubuntu 24.04 serverga AI Jamoa'ni o'rnatadi (doimiy ishlaydi, uxlamaydi, HTTPS bilan).
# Ishlatish (server ichida, root sifatida):
#   curl -fsSL https://raw.githubusercontent.com/maxmutov-nuriddin/Agent/main/deploy/setup.sh -o setup.sh
#   bash setup.sh                 # domen yo'q bo'lsa IP asosidagi bepul sslip.io manzil ishlatiladi
#   bash setup.sh panel.mening.uz # o'z domeningiz bo'lsa (A yozuvi shu serverga qaragan bo'lsin)
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "root sifatida ishga tushiring (sudo -i)"; exit 1; }

REPO="https://github.com/maxmutov-nuriddin/Agent.git"
APP=/opt/aijamoa
IP="$(curl -4 -fsS https://api.ipify.org || hostname -I | awk '{print $1}')"
DOMAIN="${1:-${IP//./-}.sslip.io}"

echo "==> Paketlar"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git ffmpeg ufw curl debian-keyring debian-archive-keyring apt-transport-https gpg
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y && apt-get install -y caddy
fi

echo "==> Kod"
id aijamoa >/dev/null 2>&1 || useradd --system --create-home --home-dir "$APP" --shell /usr/sbin/nologin aijamoa
if [ -d "$APP/src/.git" ]; then git -C "$APP/src" pull --ff-only; else git clone "$REPO" "$APP/src"; fi
python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q -U pip
"$APP/venv/bin/pip" install -q -r "$APP/src/requirements.txt"
mkdir -p "$APP/data" "$APP/workspace"

if [ ! -f "$APP/src/.env" ]; then
  cat > "$APP/src/.env" <<ENV
# Kalitlarni shu faylga o'zingiz yozing (nano $APP/src/.env). Hech kimga yubormang.
TELEGRAM_BOT_TOKEN=
OWNER_TELEGRAM_ID=
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
SECRET_KEY=
WEB_HOST=127.0.0.1
WEB_PORT=8080
WEB_PUBLIC_URL=https://$DOMAIN
DATABASE_URL=sqlite+aiosqlite:///$APP/data/company.db
WORKSPACE_DIR=$APP/workspace
REPORT_TZ=Asia/Tashkent
ENV
  chmod 600 "$APP/src/.env"
fi
chown -R aijamoa:aijamoa "$APP"

echo "==> Servis"
cp "$APP/src/deploy/aijamoa.service" /etc/systemd/system/aijamoa.service
cat > /etc/caddy/Caddyfile <<CADDY
$DOMAIN {
    encode gzip
    reverse_proxy 127.0.0.1:8080
}
CADDY
systemctl daemon-reload
systemctl enable aijamoa >/dev/null
systemctl restart caddy

echo "==> Xavfsizlik devori (faqat SSH, HTTP, HTTPS)"
ufw allow OpenSSH >/dev/null; ufw allow 80,443/tcp >/dev/null; ufw --force enable >/dev/null

echo
echo "TAYYOR. Endi:"
echo "  1) nano $APP/src/.env      (TELEGRAM_BOT_TOKEN, OWNER_TELEGRAM_ID, GEMINI_API_KEY, SECRET_KEY ni yozing)"
echo "  2) systemctl start aijamoa && journalctl -u aijamoa -f"
echo "  3) Panel: https://$DOMAIN   (kirish kaliti jurnalda chiqadi: journalctl -u aijamoa | grep -i token)"
