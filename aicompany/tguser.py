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


class TgStale(TgError):
    """Sessiya fayli bor, lekin ichida kirilgan akkaunt yo'q (tugallanmagan kirish qoldig'i)."""


def session_file(settings) -> Path:
    return Path(settings.tg_session + ".session")


class TgUser:
    def __init__(self, settings, client_factory: Callable[[], Any] | None = None):
        self.s = settings
        self.api_id, self.api_hash = settings.tg_api_id, settings.tg_api_hash  # panel orqali ham kiritilishi mumkin
        self._factory = client_factory
        self._client = None
        self.me = ""
        self._login: dict | None = None  # panel orqali kirish jarayoni: {"client", "phone", "hash"}

    def has_keys(self) -> bool:
        return bool(self.api_id and self.api_hash)

    def login_pending(self) -> bool:
        return self._login is not None

    def configured(self) -> bool:
        return bool(self.has_keys() and (self._factory or session_file(self.s).exists()))

    def _new_client(self):
        if self._factory:
            return self._factory()
        try:
            from telethon import TelegramClient
        except ImportError as e:
            raise TgError("Telethon o'rnatilmagan: `pip install telethon`") from e
        return TelegramClient(self.s.tg_session, self.api_id, self.api_hash)

    async def client(self):
        if self._client is not None:
            return self._client
        if not self.configured():
            raise TgError("Telegram akkaunt ulanmagan: panelda Hisob -> Telegram akkaunt (yoki `python -m aicompany tglogin`)")
        client = self._new_client()
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            raise TgStale("Telegram sessiyasi eskirgan: panelda Hisob -> Telegram akkaunt orqali qayta ulang")
        self._client = client
        return self._client

    async def close(self):
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    # ---------- panel orqali kirish (telefon + kod [+ parol]) ----------
    @staticmethod
    def _friendly(e: Exception) -> TgError:
        name = type(e).__name__
        known = {
            "PhoneNumberInvalidError": "Telefon raqami noto'g'ri (+998901234567 ko'rinishida yozing)",
            "PhoneCodeInvalidError": "Kod noto'g'ri",
            "PhoneCodeExpiredError": "Kod eskirgan: yangi kod so'rang",
            "PhoneCodeEmptyError": "Kodni kiriting",
            "PasswordHashInvalidError": "Ikki bosqichli parol noto'g'ri",
            "ApiIdInvalidError": "TG_API_ID yoki TG_API_HASH noto'g'ri (my.telegram.org dan qayta oling)",
            "PhoneNumberBannedError": "Bu raqam Telegramda bloklangan",
            "PhoneNumberFloodError": "Juda ko'p urinish: keyinroq qayta urining",
        }
        if name == "FloodWaitError":
            return TgError(f"Telegram kutishni so'radi: {getattr(e, 'seconds', '?')} soniyadan keyin qayta urining")
        return TgError(known.get(name) or f"Telegram xatosi: {name}")

    async def login_start(self, phone: str) -> None:
        if not self.has_keys():
            raise TgError("Avval TG_API_ID va TG_API_HASH ni kiriting")
        await self.close()  # bir vaqtda ikki ulanish sessiya faylini buzmasin
        await self._drop_login()
        client = self._new_client()
        try:
            await client.connect()
            sent = await client.send_code_request(phone)
        except TgError:
            raise
        except Exception as e:  # noqa: BLE001 — Telethon turli xatolar beradi
            await client.disconnect()
            raise self._friendly(e) from e
        self._login = {"client": client, "phone": phone, "hash": getattr(sent, "phone_code_hash", None)}

    async def login_verify(self, code: str = "", password: str = "") -> str:
        """'ok' (kirdi) yoki 'password' (ikki bosqichli parol kerak). Xato bo'lsa TgError."""
        if not self._login:
            raise TgError("Avval telefon raqamini kiriting va kod so'rang")
        client, phone = self._login["client"], self._login["phone"]
        try:
            if password and not code:
                await client.sign_in(password=password)
            else:
                await client.sign_in(phone=phone, code=code, phone_code_hash=self._login["hash"])
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ == "SessionPasswordNeededError":
                if not password:
                    return "password"
                try:
                    await client.sign_in(password=password)
                except Exception as e2:  # noqa: BLE001
                    raise self._friendly(e2) from e2
            else:
                raise self._friendly(e) from e
        me = await client.get_me()
        self._client, self._login = client, None
        if not self._factory:
            lock_down(session_file(self.s))
        self.me = self.name_of(me) + (f" (@{me.username})" if getattr(me, "username", None) else "")
        return "ok"

    async def _drop_login(self):
        if self._login:
            try:
                await self._login["client"].disconnect()
            except Exception:  # noqa: BLE001
                pass
            self._login = None

    async def logout(self) -> None:
        """Sessiyani Telegramda ham tugatadi va faylni o'chiradi."""
        await self._drop_login()
        try:
            client = self._client or self._new_client()
            if not client.is_connected():
                await client.connect()
            await client.log_out()
        except Exception:  # noqa: BLE001 — baribir mahalliy faylni o'chiramiz
            pass
        await self.close()
        for suffix in (".session", ".session-journal"):
            try:
                Path(self.s.tg_session + suffix).unlink()
            except OSError:
                pass

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
