"""Shaxsiy Telegram akkaunt (rasmiy foydalanuvchi API'si, Telethon). Bot emas: sizning akkauntingiz sifatida.

Xavfsizlik: sessiya fayli akkauntga TO'LIQ kirish beradi. U faqat shu kompyuterda, 600 ruxsat bilan turadi
va git'ga tushmaydi (data/ papkasi .gitignore da).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable


class TgError(Exception):
    """Foydalanuvchiga tushunarli Telegram xatosi."""


def session_file(settings) -> Path:
    return Path(settings.tg_session + ".session")


class TgUser:
    def __init__(self, settings, client_factory: Callable[[], Any] | None = None):
        self.s = settings
        self._factory = client_factory
        self._client = None

    def configured(self) -> bool:
        return bool(self.s.tg_api_id and self.s.tg_api_hash and (self._factory or session_file(self.s).exists()))

    async def client(self):
        if self._client is not None:
            return self._client
        if not self.configured():
            raise TgError("Telegram akkaunt ulanmagan: `python -m aicompany tglogin` ni ishga tushiring")
        if self._factory:
            self._client = self._factory()
        else:
            try:
                from telethon import TelegramClient
            except ImportError as e:
                raise TgError("Telethon o'rnatilmagan: `pip install telethon`") from e
            self._client = TelegramClient(self.s.tg_session, self.s.tg_api_id, self.s.tg_api_hash)
        await self._client.connect()
        if not await self._client.is_user_authorized():
            raise TgError("Telegram sessiyasi eskirgan: `python -m aicompany tglogin` ni qayta bajaring")
        return self._client

    async def close(self):
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    # ---------- yordamchilar ----------
    @staticmethod
    def name_of(entity) -> str:
        if entity is None:
            return "?"
        for attr in ("title",):
            if getattr(entity, attr, None):
                return entity.title
        first = " ".join(x for x in (getattr(entity, "first_name", None), getattr(entity, "last_name", None)) if x)
        return first or getattr(entity, "username", None) or str(getattr(entity, "id", "?"))

    async def dialogs(self, limit=100):
        return await (await self.client()).get_dialogs(limit=limit)

    async def resolve(self, ref: str):
        """Chat: @username, raqamli ID yoki ism bo'yicha. Bir nechta mos kelsa, aniqlashtirishni so'raydi."""
        c = await self.client()
        ref = ref.strip()
        if not ref:
            raise TgError("chat nomi bo'sh")
        if ref.startswith("@") or ref.lstrip("-").isdigit() or ref.startswith("+"):
            try:
                return await c.get_entity(int(ref) if ref.lstrip("-").isdigit() else ref)
            except Exception as e:  # noqa: BLE001 — Telethon turli xato turlarini beradi
                raise TgError(f"'{ref}' topilmadi") from e
        matches = [d for d in await self.dialogs(200) if ref.lower() in (d.name or "").lower()]
        if not matches:
            raise TgError(f"'{ref}' nomli chat topilmadi (tg_chats bilan ro'yxatni ko'ring)")
        exact = [d for d in matches if (d.name or "").lower() == ref.lower()]
        if len(exact) == 1:
            return exact[0].entity
        if len(matches) > 1:
            raise TgError("bir nechta mos chat: " + ", ".join(d.name for d in matches[:6]) + ". Aniqroq yozing yoki @username ishlating")
        return matches[0].entity

    def allowed(self, entity, display: str) -> bool:
        """TG_ALLOWED bo'sh bo'lsa hamma (baribir tasdiq bilan); to'ldirilgan bo'lsa faqat shular."""
        if not self.s.tg_allowed:
            return True
        keys = {str(getattr(entity, "id", "")).lower(), (getattr(entity, "username", "") or "").lower(), display.lower()}
        return bool(keys & set(self.s.tg_allowed))


def lock_down(path: Path):
    """Sessiya faylini faqat egasi o'qiy oladigan qiladi."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
