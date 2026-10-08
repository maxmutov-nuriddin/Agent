"""Shaxsiy Telegram akkaunt (rasmiy foydalanuvchi API'si, Telethon). Bot emas: sizning akkauntingiz sifatida.

Xavfsizlik: sessiya fayli akkauntga TO'LIQ kirish beradi. U faqat shu kompyuterda, 600 ruxsat bilan turadi
va git'ga tushmaydi (data/ papkasi .gitignore da).
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("aicompany.tg")
CONNECT_TIMEOUT = 25  # soniya: Telegram serveriga ulanib bo'lmasa, abadiy kutmaymiz
REQUEST_TIMEOUT = 30
NO_CONNECT = ("Telegram serverlariga ulanib bo'lmadi ({s} soniya). Internet yoki VPN'ni tekshiring. "
              "Telegram ilovangiz proksi orqali ishlasa, o'sha proksi havolasini (tg://proxy?... yoki socks5://...) "
              "\"Proksi\" maydoniga qo'ying.")


class TgError(Exception):
    """Foydalanuvchiga tushunarli Telegram xatosi."""


def parse_proxy(raw: str | None):
    """Proksi satri -> ("mtproxy", (host, port, secret)) | ("socks", {...}) | None. Noto'g'ri bo'lsa ValueError."""
    raw = (raw or "").strip()
    if not raw:
        return None
    u = urlparse(raw)
    if u.scheme == "tg" or (u.netloc in ("t.me", "telegram.me") and u.path.strip("/") == "proxy"):
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if not (q.get("server") and str(q.get("port", "")).isdigit() and q.get("secret")):
            raise ValueError("proksi havolasida server, port va secret bo'lishi kerak")
        if q["secret"].lower().startswith("ee"):
            raise ValueError("bu turdagi (ee... fake-TLS) MTProxy qo'llanmaydi: SOCKS5 yoki boshqa proksi kerak")
        return "mtproxy", (q["server"], int(q["port"]), q["secret"])
    if u.scheme in ("socks5", "socks4", "http") and u.hostname and u.port:
        return "socks", {"proxy_type": u.scheme, "addr": u.hostname, "port": u.port, "rdns": True,
                         "username": u.username, "password": u.password}
    raise ValueError("proksi tg://proxy?server=..&port=..&secret=.. yoki socks5://host:port ko'rinishida bo'lsin")


CODE_VIA = {
    "SentCodeTypeApp": "Telegram ilovasiga: shu akkaunt ochiq turgan telefon/kompyuterdagi \"Telegram\" rasmiy chatiga",
    "SentCodeTypeSms": "SMS bilan",
    "SentCodeTypeFirebaseSms": "SMS bilan",
    "SentCodeTypeSmsWord": "SMS bilan (so'z ko'rinishida)",
    "SentCodeTypeSmsPhrase": "SMS bilan (ibora ko'rinishida)",
    "SentCodeTypeCall": "qo'ng'iroq bilan (kodni aytib beradi)",
    "SentCodeTypeFlashCall": "qisqa qo'ng'iroq bilan (raqamning oxirgi xonalari kod)",
    "SentCodeTypeMissedCall": "o'tkazib yuborilgan qo'ng'iroq bilan (qo'ng'iroq qilgan raqamning oxirgi xonalari kod)",
    "SentCodeTypeEmailCode": "emailingizga",
    "SentCodeTypeFragmentSms": "fragment.com orqali (anonim raqam)",
    "SentCodeTypeSetUpEmailRequired": "Telegram avval email ulashni talab qilyapti: rasmiy ilovada shu raqam bilan kirib, email qo'shing",
    "CodeTypeSms": "SMS bilan", "CodeTypeCall": "qo'ng'iroq bilan", "CodeTypeFlashCall": "qisqa qo'ng'iroq bilan",
    "CodeTypeMissedCall": "o'tkazib yuborilgan qo'ng'iroq bilan", "CodeTypeFragmentSms": "fragment.com orqali",
}


def code_via(obj) -> str:
    return CODE_VIA.get(type(obj).__name__, "") if obj is not None else ""


class TgStale(TgError):
    """Sessiya fayli bor, lekin ichida kirilgan akkaunt yo'q (tugallanmagan kirish qoldig'i)."""


def session_file(settings) -> Path:
    return Path(settings.tg_session + ".session")


class TgUser:
    def __init__(self, settings, client_factory: Callable[[], Any] | None = None):
        self.s = settings
        self.api_id, self.api_hash = settings.tg_api_id, settings.tg_api_hash  # panel orqali ham kiritilishi mumkin
        self.proxy = settings.tg_proxy
        self._factory = client_factory
        self._client = None
        self.me = ""
        self._login: dict | None = None  # panel orqali kirish jarayoni: {"client", "phone", "hash"}

    def has_keys(self) -> bool:
        return bool(self.api_id and self.api_hash)

    def login_pending(self) -> bool:
        return self._login is not None

    def configured(self) -> bool:
        # kirish davom etayotganda fayl bor, lekin akkaunt hali ulanmagan: "ulangan" deb hisoblamaymiz
        return bool(self.has_keys() and not self._login and (self._factory or session_file(self.s).exists()))

    def _new_client(self):
        if self._factory:
            return self._factory()
        try:
            from telethon import TelegramClient, connection
        except ImportError as e:
            raise TgError("Telethon o'rnatilmagan: `pip install telethon`") from e
        kw = {"connection_retries": 1, "retry_delay": 1, "timeout": 10, "request_retries": 2}
        try:
            px = parse_proxy(self.proxy)
        except ValueError as e:
            raise TgError(f"Proksi noto'g'ri: {e}") from e
        if px and px[0] == "mtproxy":
            kw.update(connection=connection.ConnectionTcpMTProxyRandomizedIntermediate, proxy=px[1])
        elif px:
            try:
                import python_socks  # noqa: F401 — Telethon SOCKS uchun shuni ishlatadi
            except ImportError as e:
                raise TgError("SOCKS proksi uchun: pip install 'python-socks[asyncio]'") from e
            kw["proxy"] = px[1]
        return TelegramClient(self.s.tg_session, self.api_id, self.api_hash, **kw)

    async def _connect(self, client):
        try:
            await asyncio.wait_for(client.connect(), CONNECT_TIMEOUT)
        except (asyncio.TimeoutError, OSError, ConnectionError) as e:
            log.warning("Telegram connect failed: %r", e)
            await self._quiet_disconnect(client)
            raise TgError(NO_CONNECT.format(s=CONNECT_TIMEOUT)) from e

    @staticmethod
    async def _quiet_disconnect(client):
        try:
            await asyncio.wait_for(client.disconnect(), 5)
        except Exception:  # noqa: BLE001
            pass

    async def client(self):
        if self._client is not None:
            return self._client
        if not self.configured():
            raise TgError("Telegram akkaunt ulanmagan: panelda Hisob -> Telegram akkaunt (yoki `python -m aicompany tglogin`)")
        client = self._new_client()
        await self._connect(client)
        if not await client.is_user_authorized():
            await client.disconnect()
            raise TgStale("Telegram sessiyasi eskirgan: panelda Hisob -> Telegram akkaunt orqali qayta ulang")
        self._client = client
        return self._client

    async def close(self):
        if self._client is not None:
            await self._quiet_disconnect(self._client)
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
        self._remove_files()  # har urinish toza sessiyadan: eski chala fayl ulanishni buzmasin
        client = self._new_client()
        await self._connect(client)
        try:
            sent = await asyncio.wait_for(client.send_code_request(phone), REQUEST_TIMEOUT)
        except asyncio.TimeoutError as e:
            await self._quiet_disconnect(client)
            raise TgError(f"Telegram {REQUEST_TIMEOUT} soniyada javob bermadi: qayta urining yoki proksi qo'ying") from e
        except Exception as e:  # noqa: BLE001 — Telethon turli xatolar beradi
            log.warning("Telegram send_code failed: %r", e)
            await self._quiet_disconnect(client)
            raise self._friendly(e) from e
        self._login = {"client": client, "phone": phone}
        self._remember_sent(sent)

    def _remember_sent(self, sent):
        self._login.update(hash=getattr(sent, "phone_code_hash", None) or self._login.get("hash"),
                           via=code_via(getattr(sent, "type", None)) or "noma'lum yo'l bilan",
                           next=code_via(getattr(sent, "next_type", None)), timeout=getattr(sent, "timeout", None))
        log.info("Telegram code sent: type=%s next=%s", type(getattr(sent, "type", None)).__name__,
                 type(getattr(sent, "next_type", None)).__name__)

    def login_info(self) -> dict:
        if not self._login:
            return {}
        return {k: self._login.get(k) for k in ("via", "next", "timeout")}

    async def login_resend(self) -> dict:
        """Kodni keyingi yo'l bilan qayta yuborish (Telethon shu mijozda ResendCodeRequest qiladi)."""
        if not self._login:
            raise TgError("Avval telefon raqamini kiriting va kod so'rang")
        try:
            sent = await asyncio.wait_for(self._login["client"].send_code_request(self._login["phone"]), REQUEST_TIMEOUT)
        except asyncio.TimeoutError as e:
            raise TgError("Telegram javob bermadi: qayta urining") from e
        except Exception as e:  # noqa: BLE001
            log.warning("Telegram resend failed: %r", e)
            if type(e).__name__ in ("SendCodeUnavailableError", "PhoneCodeExpiredError"):
                raise TgError("Telegram boshqa yo'l taklif qilmayapti: biroz kutib \"Boshqa raqam / qayta\" ni bosing") from e
            raise self._friendly(e) from e
        self._remember_sent(sent)
        return self.login_info()

    async def login_verify(self, code: str = "", password: str = "") -> str:
        """'ok' (kirdi) yoki 'password' (ikki bosqichli parol kerak). Xato bo'lsa TgError."""
        if not self._login:
            raise TgError("Avval telefon raqamini kiriting va kod so'rang")
        client, phone = self._login["client"], self._login["phone"]
        try:
            if password and not code:
                await asyncio.wait_for(client.sign_in(password=password), REQUEST_TIMEOUT)
            else:
                await asyncio.wait_for(client.sign_in(phone=phone, code=code, phone_code_hash=self._login["hash"]), REQUEST_TIMEOUT)
        except asyncio.TimeoutError as e:
            raise TgError("Telegram javob bermadi: qayta urining") from e
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
            await self._quiet_disconnect(self._login["client"])
            self._login = None

    def _remove_files(self):
        if self._factory:
            return
        for suffix in (".session", ".session-journal"):
            try:
                Path(self.s.tg_session + suffix).unlink()
            except OSError:
                pass

    async def logout(self) -> None:
        """Sessiyani Telegramda ham tugatadi va faylni o'chiradi."""
        await self._drop_login()
        client = self._client
        try:
            if client is None and (self._factory or session_file(self.s).exists()):
                client = self._new_client()
            if client is not None:
                if not client.is_connected():
                    await asyncio.wait_for(client.connect(), CONNECT_TIMEOUT)
                await asyncio.wait_for(client.log_out(), REQUEST_TIMEOUT)
        except Exception:  # noqa: BLE001 — baribir mahalliy faylni o'chiramiz
            pass
        finally:
            if client is not None:
                await self._quiet_disconnect(client)  # oldin bu ulanish ochiq qolib ketardi
            self._client = None
        self._remove_files()

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
