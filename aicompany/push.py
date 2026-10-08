"""PWA push-bildirishnomalar (Web Push, VAPID). Vazifa tugaganda, ruxsat kerak bo'lganda, eslatma vaqtida telefonga keladi.

Kalitlar bazada saqlanadi (shaxsiy kalit shifrlangan). Obunalar: kv `push_subs`. Sozlama: kv `push_prefs`.
iPhone'da faqat bosh ekranga o'rnatilgan ilovada va HTTPS orqali ishlaydi (iOS 16.4+).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging

log = logging.getLogger("aicompany.push")

KINDS = {"done": "Vazifa tayyor bo'lganda", "failed": "Vazifa bajarilmaganda / xato", "approval": "Ruxsat so'ralganda",
         "reminder": "Eslatma vaqti kelganda", "morning": "Ertalabki xulosa", "watch": "Kuzatuv topganda"}
MAX_SUBS = 10


def available() -> bool:
    try:
        import pywebpush  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


class PushService:
    def __init__(self, app):
        self.app = app
        self._vapid = None

    @property
    def store(self):
        return self.app.store

    def _secret(self) -> str | None:
        s = self.app.settings
        return s.secret_key or s.web_token

    # ---------- kalitlar ----------
    async def _load_vapid(self):
        if self._vapid is not None:
            return self._vapid
        from py_vapid import Vapid
        from .persist import seal, unseal
        secret = self._secret()
        pem = unseal(await self.store.get_kv("vapid_private"), secret)
        if pem:
            self._vapid = Vapid.from_pem(pem.encode())
        else:
            v = Vapid()
            v.generate_keys()
            await self.store.set_kv("vapid_private", seal(v.private_pem().decode(), secret))
            self._vapid = v
        return self._vapid

    async def public_key(self) -> str:
        from cryptography.hazmat.primitives import serialization
        v = await self._load_vapid()
        raw = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    # ---------- obunalar va sozlama ----------
    async def subs(self) -> list[dict]:
        try:
            return json.loads(await self.store.get_kv("push_subs") or "[]")
        except ValueError:
            return []

    async def _save_subs(self, subs: list[dict]):
        await self.store.set_kv("push_subs", json.dumps(subs[-MAX_SUBS:]))

    async def subscribe(self, sub: dict, ua: str = "") -> int:
        if not isinstance(sub, dict) or not str(sub.get("endpoint", "")).startswith("https://") \
                or not (sub.get("keys") or {}).get("p256dh") or not (sub.get("keys") or {}).get("auth"):
            raise ValueError("obuna ma'lumoti noto'g'ri")
        subs = [s for s in await self.subs() if s["sub"]["endpoint"] != sub["endpoint"]]
        subs.append({"sub": {"endpoint": sub["endpoint"], "keys": {"p256dh": sub["keys"]["p256dh"], "auth": sub["keys"]["auth"]}},
                     "ua": ua[:80]})
        await self._save_subs(subs)
        return len(subs[-MAX_SUBS:])

    async def unsubscribe(self, endpoint: str):
        await self._save_subs([s for s in await self.subs() if s["sub"]["endpoint"] != endpoint])

    async def prefs(self) -> dict:
        try:
            saved = json.loads(await self.store.get_kv("push_prefs") or "{}")
        except ValueError:
            saved = {}
        return {k: bool(saved.get(k, True)) for k in KINDS}

    async def set_prefs(self, data: dict) -> dict:
        p = await self.prefs()
        for k in KINDS:
            if k in data:
                p[k] = bool(data[k])
        await self.store.set_kv("push_prefs", json.dumps(p))
        return p

    # ---------- yuborish ----------
    async def notify(self, kind: str, title: str, body: str = "", url: str = "/", *, force: bool = False) -> int:
        """Obuna bo'lgan hamma qurilmaga yuboradi. Qaytadi: yetkazilganlar soni. O'chirilgan turdagi xabar yuborilmaydi."""
        if not available() or not (await self.subs()):
            return 0
        if not force and not (await self.prefs()).get(kind, True):
            return 0
        payload = json.dumps({"title": title[:80], "body": body[:200], "url": url, "tag": kind}, ensure_ascii=False)
        vapid = await self._load_vapid()
        subs, ok, dead = await self.subs(), 0, []
        for s in subs:
            try:
                await asyncio.to_thread(self._send, s["sub"], payload, vapid)
                ok += 1
            except Exception as e:  # noqa: BLE001
                code = getattr(getattr(e, "response", None), "status_code", None)
                if code in (404, 410):
                    dead.append(s["sub"]["endpoint"])  # qurilma obunani bekor qilgan
                else:
                    log.warning("push yuborilmadi: %s", str(e)[:120])
        if dead:
            await self._save_subs([s for s in subs if s["sub"]["endpoint"] not in dead])
        return ok

    def _send(self, sub: dict, payload: str, vapid):
        from pywebpush import webpush
        webpush(sub, payload, vapid_private_key=vapid, vapid_claims={"sub": self.app.settings.web_public_url or "mailto:admin@example.com"}, ttl=3600, timeout=10)

    # ---------- ulanish nuqtalari ----------
    async def task_done(self, res: dict):
        if res.get("kind") == "chat":
            return
        tid = res.get("task_id")
        if res.get("status") == "done":
            text = " ".join((res.get("result") or "").split())[:160]
            await self.notify("done", f"✅ Vazifa #{tid} tayyor", text, f"/?task={tid}")
        elif res.get("status") in ("failed", "limit", "interrupted"):
            await self.notify("failed", f"⚠️ Vazifa #{tid} bajarilmadi", (res.get("error") or res.get("status") or "")[:160], f"/?task={tid}")

    async def approval(self, approval_id, task_id, agent, description, kind="command"):
        await self.notify("approval", "🔐 Ruxsat kerak", f"{agent}: {description}", "/?tab=cards")

    def reminder_sender(self):
        async def send(text):
            await self.notify("reminder", "⏰ Eslatma", str(text), "/")
        return send
