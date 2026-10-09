# AI Kompaniya (MVP)

Telegram orqali boshqariladigan AI jamoa: rahbar (CEO) vazifani rejalashtiradi, mutaxassislarga tarqatadi, HR kerak bo'lsa yangi xodim oladi, QA natijani tekshiradi. Claude, ChatGPT va Gemini bitta router orqali ishlaydi, har provayder uchun alohida byudjet limiti bor.

## Ishga tushirish (lokal)
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # keyin .env ni o'zingiz to'ldiring (kalitlarni hech kimga yubormang)
python -m aicompany check   # kalitlar ishlayaptimi, models.yaml dagi model ID'lar to'g'rimi
python -m aicompany ask "Kofexona uchun Instagram kontent rejasi tuz"   # Telegramsiz sinash
python -m aicompany run     # Telegram botni ishga tushirish
python -m pytest            # testlar
```
Faqat Claude bilan boshlash: `.env` da faqat `ANTHROPIC_API_KEY` ni to'ldiring, boshqa provayderlar kalitsiz avtomatik o'tkazib yuboriladi.

`.env` da kerak: `TELEGRAM_BOT_TOKEN` (@BotFather), `OWNER_TELEGRAM_ID` (@userinfobot), kamida bitta API kalit.

## Telegram
`/team` `/tasks` `/task <id>` `/budget` `/report` `/memory` `/hire <nom> <sabab>` `/fire <nom>` `/review` `/pause` `/resume` `/stop <id>` `/reminders` (`/reminders cancel <id>`) `/home` `/tg` `/ai` `/web`. Oddiy gap = suhbat, ish so'rasangiz = vazifa (rahbar o'zi ajratadi); fayl yuborsangiz vazifaga ilova bo'ladi. Tayyor fayllar vazifa tugagach Telegramga yuboriladi. Har kuni `REPORT_HOUR` da hisobot keladi (LLM ishlatmaydi, pul sarflamaydi). Bot faqat `OWNER_TELEGRAM_ID` ga javob beradi.

## Veb-panel (PWA) va iPhone
`python -m aicompany run` Telegram botdan tashqari veb-panelni ham ishga tushiradi (faqat veb: `python -m aicompany web`). Terminalda maxfiy havola chiqadi: `http://...:8080/#token=...`. Kalit birinchi ishga tushirishda avtomatik yaratilib `.env` ga yoziladi.

Panelda hamma qurilgan imkoniyat bor (Telegramdagi buyruqlarning panel analoglari ham): **Jamoa** (HR tahlili tugmasi), **Hisob** (ulanishlar holati, limitlar, modellar tekshiruvi (`check` o'rni), iPhone vidjeti havolasi, joylashuv va uy, xotirani qo'shish/o'chirish, hisobot, jurnal, asosiy AI), chatda 🗑 tozalash, vazifa formasida 📎 fayl biriktirish.

Panel bo'limlari: **Jamoa** (kim ishlayapti, Rahbar kartasi, xodim yollash/bo'shatish), **Kartalar** (xavfli buyruqlarga ruxsat), **Vazifalar** (natija, fayllar, to'xtatish, arxiv), **Hisob** (byudjet, sarf, xotira, pauza).

- **Chat** (Rahbar kartasidagi «Chatni ochish») faqat suhbat uchun: oddiy xabarlar vazifa bo'lmaydi. Ish so'ralsa Rahbar «Vazifa qilib topshirish» tugmasini taklif qiladi.
- **«Vazifa berish»** alohida forma: yozganingiz to'g'ridan-to'g'ri vazifa bo'ladi.
- **Vazifalar:** ishlayotganini to'xtatish (Telegramda `/stop <id>`), tugaganini arxivga olish, arxivdan qaytarish yoki **butunlay o'chirish** (fayllari bilan; faqat arxivdagini).

**iPhone bosh ekraniga o'rnatish:** Safari'da havolani oching → Ulashish → **Bosh ekranga qo'shish**. Ilova kabi to'liq ekranda ochiladi.

**Telefondan ulanish (tanlang):**
1. *Uy WiFi:* `.env` da `WEB_HOST=0.0.0.0`, havola `http://<kompyuter-IP>:8080/`. Kompyuter yoqilgan bo'lishi kerak. Diqqat: HTTP shifrlanmagan, faqat ishonchli WiFi.
2. *Tailscale (tavsiya, bepul):* kompyuter va telefonga Tailscale o'rnating, havola `http://<tailscale-IP>:8080/`. Trafik shifrlangan, internetga ochilmaydi.
3. *Cloudflare Tunnel:* internetdan HTTPS bilan. Faqat `WEB_TOKEN` uzun va maxfiy bo'lsa.

**Vidjet:** iOS'da PWA haqiqiy vidjet bera olmaydi (buning uchun native ilova kerak). Bepul **Scriptable** ilovasiga `docs/widget/ai-jamoa.js` skriptini qo'ying (ichidagi `BASE_URL` va `WIDGET_TOKEN` ni to'ldiring). Bosh ekranda ishlayotgan agentlar, kutayotgan qarorlar, bugungi sarf va qolgan byudjet ko'rinadi. `WIDGET_TOKEN` faqat o'qiydi, boshqarib bo'lmaydi.

**Xavfsizlik:** barcha `/api` so'rovlari `WEB_TOKEN` talab qiladi (8 ta xato urinishdan keyin 1 daqiqa bloklanadi), qat'iy CSP, fayllar faqat workspace ichidan yuklanadi, token yo'q bo'lsa server ishga tushmaydi.

## Yordamchi: joylashuv, yo'l vaqti, Telegram akkaunt
Yangi xodim **assistant** shu imkoniyatlarga ega.

**Joylashuv va xarita** (hech narsa o'rnatish shart emas, bepul OpenStreetMap):
- Telegramda botga 📎 → **Joylashuv** yuboring (jonli joylashuv ham bo'ladi). Uyingizni `/home` deb yozib, joylashuvni yuborib saqlang (yoki `/home <manzil>`).
- Keyin so'rang: «uyga necha daqiqada yetaman?», «yaqin dorixonani top». Asboblar: `where_am_i`, `route_eta`, `find_places`, `save_place`.
- `GOOGLE_MAPS_API_KEY` qo'ysangiz tirbandlik va jamoat transporti hisobga olinadi, joy qidiruv aniqroq. Bo'lmasa vaqt tirbandliksiz taxminiy.
- Panel yoki iPhone Shortcuts: `POST /api/location {"lat":..,"lon":..}` (Authorization: Bearer WEB_TOKEN).

**Shaxsiy Telegram akkaunt** (rasmiy foydalanuvchi API'si; agent uchun ortiqcha akkaunt tavsiya etiladi):
1. Panel → **Hisob → Ulanishlar → 📨 Telegram akkauntni ulash**. Qadamlar: (1) my.telegram.org dan olingan `api_id` va `api_hash` ni kiriting, (2) telefon raqami, (3) Telegramga kelgan kod (ikki bosqichli parol bo'lsa, uni ham). Bir marta bajariladi.
   Terminal varianti: `.env` ga `TG_API_ID`/`TG_API_HASH` yozib `python -m aicompany tglogin`.
   Kod so'rashda «Telegram serverlariga ulanib bo'lmadi» chiqsa: tarmoq Telegram serverlarini to'sayapti. Telegram ilovangizdagi proksi havolasini (Sozlamalar → Ma'lumotlar va xotira → Proksi; `tg://proxy?...` yoki `socks5://...`) shu oynadagi «Proksi» maydoniga qo'ying (yoki `.env` da `TG_PROXY`). `ee...` turidagi (fake-TLS) MTProxy qo'llanmaydi.
2. Panelda kiritilgan kalitlar `data/` bazasida saqlanadi (git'ga tushmaydi). Uzish: xuddi shu oyna → «Akkauntni uzish» (sessiya Telegramda ham tugatiladi).
3. Boshlanishda `TG_MODE=read` (faqat o'qish va javob loyihasi). Yuborish uchun `TG_MODE=write`: **har bir xabar** Kartalar/Telegram tugmalarida sizga kimga va qanday matn bilan ketishi ko'rsatilib, tasdig'ingizdan keyingina yuboriladi. Soatiga `TG_MAX_SENDS_PER_HOUR` dan ko'p yuborilmaydi, `TG_ALLOWED` bilan faqat ma'lum kontaktlarga cheklash mumkin.
4. Asboblar: `tg_chats` (o'qilmaganlar xulosasi bilan), `tg_contacts` (kontaktlardan qidirish), `tg_read`, `tg_send` (yozishmasi yo'q kontaktga ham). Chatdagi xabarlar «ishonchsiz matn» deb belgilanadi: ularda «pul o'tkaz» kabi ko'rsatma bo'lsa bajarilmaydi.

**Agent akkaunti bot o'rnida:** asosiy akkauntingizdan agent akkauntiga yozsangiz, u botdagidek javob beradi (suhbat, vazifa, ovozli xabar, fayl, natija fayllari). Faqat panelda ko'rsatilgan chat ID'lardan (bo'sh bo'lsa `OWNER_TELEGRAM_ID`) kelgan shaxsiy xabarlarga javob beradi; boshqa odamlar va guruhlarga umuman javob bermaydi. Yoqish/o'chirish va ID'lar: Hisob → Telegram akkaunt.

**Ruxsat rejimlari** (Hisob → Telegram akkaunt): «Faqat o'qish», «Tasdiq bilan» (har amal sizdan so'raladi), «Cheklovsiz» (tasdiqsiz, limitsiz). Qo'shimcha asboblar: `tg_create_group` (guruh/kanal), `tg_add_members`, `tg_join`, `tg_leave`, `tg_forward`, `tg_delete_messages`, `tg_send_file`, `tg_mark_read`.

**Maxfiylik rejimi:** `PRIVATE_PROVIDERS=anthropic` qo'ysangiz, shaxsiy Telegram yozishmalari avvalo faqat shu AI'ga ketadi. U ulanmagan yoki limiti tugagan bo'lsa (`PRIVATE_MODE=prefer`, standart) tizim **to'xtamaydi**: matn boshqa AI'ga ketadi, lekin karta, parol, bir martalik kod va kalitlar yashiriladi va sizga ogohlantirish keladi (jurnalga ham yoziladi). To'liq qat'iylik kerak bo'lsa `PRIVATE_MODE=strict`: maxfiy AI bo'lmasa Telegram ishlari to'xtaydi.

**Xavfsizlik va maxfiylik (muhim):**
- `data/tg.session` fayli akkauntingizga **to'liq kirish** beradi: git'ga tushmaydi, 600 ruxsat bilan turadi, hech kimga bermang. Chiqarish: Telegram → Sozlamalar → Qurilmalar.
- Shaxsiy yozishmalar matni AI xizmatiga yuboriladi. **Bepul Gemini kaliti ma'lumotni o'qitishda ishlatishi mumkin**, shuning uchun `PRIVATE_PROVIDERS=anthropic` kabi qilib shaxsiy chat faqat ishonchli provayderga yuborilsin (aks holda hamma ulangan AI'ga ketadi).
- Instagram uchun parol bilan kirish YO'Q (shartlarga zid). Rasmiy yo'l: akkauntni Creator/Business qilib Graph API ulash (keyingi bosqich).

## Agentlar nima qila oladi (asboblar)
| Guruh | Asboblar | Eslatma |
|---|---|---|
| files | write_file, read_file, list_files | faqat vazifaning `workspace/task_N/` papkasi ichida |
| web | web_search, fetch_url | ichki/lokal manzillar bloklangan; natija "ishonchsiz" deb belgilanadi |
| memory | remember, recall | uzoq muddatli xotira |
| time | set_reminder, list_reminders, cancel_reminder | eslatmalar |
| shell | run_command | **har bir buyruq Telegramda sizning tasdig'ingizni kutadi** (✅/❌), 10 daqiqada javob bo'lmasa rad etiladi |

Rollarga guruhlar biriktirilgan (masalan faqat dasturchida `shell` bor). HR yangi xodim yaratganda unga kerakli guruhlarni tanlaydi.

**Xavfsizlik chegarasi:** `run_command` to'liq izolyatsiyalangan sandbox EMAS. U workspace papkasida, API kalitlarisiz (toza muhit) ishlaydi, lekin sizning kompyuteringizdagi oddiy jarayon. Shuning uchun har buyruq tasdiqlanadi. Buyruqni o'qib chiqib keyin ruxsat bering. Kelajakda Docker sandbox qo'shiladi.

## Vazifa natijasi
Har tugagan vazifada:
- **Natija** (panel/Telegram): so'ralgan savolga qisqa, aniq javob.
- **`NATIJA.md`**: so'ralgan narsaning hammasi bitta tayyor faylda.
- **`PROMPT.md`**: boshqa istalgan AI'ga bersangiz, shu ishni to'liq qayta bajaradigan tayyor prompt (panelda «Nusxalash»).
- Jamoa ishi (reja, har xodim natijasi, QA tekshiruvi) panelda odam o'qiydigan ko'rinishda, yig'ilgan holda.

## Eslatmalar
- Botga yoki panel chatiga: "ertaga 9:00 da onamga qo'ng'iroq qilishni eslat", "30 daqiqadan keyin choyni eslat". Rahbar eslatmani darhol qo'yadi (vazifa ochmaydi).
- Vaqt `REPORT_TZ` bo'yicha. Vaqti kelganda Telegramga (va panel chatiga) keladi.
- Ro'yxat: `/reminders`, bekor qilish `/reminders cancel <id>`. Panelda: Hisob → Eslatmalar (qo'shish/bekor qilish).

## Avvalgi vazifani davom ettirish
- Panelda vazifa kartasidagi **✏️ O'zgartirish / davom** tugmasi yoki chatda "#12 dagi saytga qora rejim qo'sh" deb yozing. Yangi vazifa eski vazifaning fayllari va natijasidan boshlaydi (eski vazifa o'zgarmaydi).

## Xodimlar
- HR yangi xodim yaratadi (jamoa `MAX_AGENTS` bilan cheklangan, yangi xodim hech qachon avtomatik `strong` bo'lmaydi).
- Har 10-vazifadan keyin (yoki `/review`) HR-yollagan va oxirgi 10 vazifada ishlamagan xodimlarni bo'shatadi. Asosiy xodimlar (ceo/hr/qa/generalist) va siz yollaganlar avtomatik bo'shatilmaydi.
- Rahbar har qadamga model darajasini (cheap/mid/strong) o'zi belgilaydi, noaniq bo'lsa arzonini tanlaydi.

## Bir nechta AI (Claude / Gemini / ChatGPT)
Kalitlari `.env` da bor provayderlar avtomatik ishlatiladi, kaliti yo'qlari o'tkazib yuboriladi: faqat `ANTHROPIC_API_KEY` bo'lsa faqat Claude, faqat `GEMINI_API_KEY` bo'lsa faqat Gemini, ikkalasi bo'lsa birgalikda.
- **Asosiy AI:** `PRIMARY_PROVIDER` (`auto` = eng arzoni) yoki panelda Hisob → Asosiy AI, yoki Telegramda `/ai gemini`. Tanlangani birinchi ishlaydi, ikkinchisi zaxira (xato yoki limit tugasa).
- **Daraja bo'yicha:** `PROVIDER_BY_TIER=cheap:gemini,strong:anthropic`.
- **Bitta ish = bitta provayder:** agentning asbob sikli boshdan oxirigacha bitta provayderda bajariladi. U o'rtada yiqilsa, ish boshqasida qayta boshlanadi.
- **Ovozli xabarlar:** Telegramda ovoz yuborsangiz yoki panelda mikrofon tugmasini bossangiz, Gemini uni matnga aylantiradi (asosiy AI Claude bo'lsa ham). `GEMINI_API_KEY` kerak. Panelda mikrofon faqat HTTPS (yoki localhost) orqali ishlaydi.
- **Gemini bepul tarifi:** sinov uchun yaxshi, lekin so'rov tezligi cheklangan va Google bepul tarifdagi ma'lumotlardan foydalanishi mumkin. Maxfiy ma'lumot yubormang.

## Tejamkorlik
Router vazifa darajasiga (cheap/mid/strong) qarab eng arzon provayderni tanlaydi, xato bo'lsa keyingisiga o'tadi. Claude uchun prompt caching va past `effort` yoqilgan. Limit tugagan provayder o'tkazib yuboriladi. Bitta vazifa uchun `MAX_TASK_USD` chegarasi bor. 80% da Telegramga ogohlantirish keladi. Limitni oshirish: `.env` dagi `BUDGET_USD_*`.

## Muhim
`models.yaml` dagi OpenAI/Gemini model nomlari va narxlar tasdiqlanmagan. Kalit qo'ygach `check` ni ishga tushiring.

## Render'ga joylash
1. Render → **New → Blueprint** → shu repo (`render.yaml` o'zi o'qiladi). Yoki **New → Web Service**: Build `pip install -r requirements.txt`, Start `python -m aicompany run`, Health check path `/health`.
2. **Environment**: `TELEGRAM_BOT_TOKEN`, `OWNER_TELEGRAM_ID`, `WEB_TOKEN` va `WIDGET_TOKEN` (uzun tasodifiy satr), `ANTHROPIC_API_KEY` va/yoki `GEMINI_API_KEY`. Port va tashqi manzil (`PORT`, `RENDER_EXTERNAL_URL`) avtomatik olinadi.
3. Panel: `https://<nom>.onrender.com/#token=<WEB_TOKEN>` (HTTPS bo'lgani uchun mikrofon ham ishlaydi).
4. **Uxlamasligi uchun:** UptimeRobot → HTTP(s) monitor → `https://<nom>.onrender.com/health`, har 5 daqiqa. Javob: `{"ok": true, ...}` (tokensiz, maxfiy ma'lumotsiz).
5. **Bir vaqtda faqat bitta joyda ishlating:** kompyuterda ham `run` ishlab tursa, Telegram bot ikkalasida ishlamaydi (conflict).

**Ma'lumotlar saqlanishi uchun Supabase (bepul):** bepul Render'da disk yo'q, shuning uchun bazani Supabase'ga ulang:
1. supabase.com → New project (parolni eslab qoling).
2. **Connect** → **Session pooler** satrini oling (`postgresql://postgres.xxxx:PAROL@aws-0-....pooler.supabase.com:5432/postgres`). Direct connection emas: u IPv6, Render ulana olmaydi.
3. Render → Environment: `DATABASE_URL` = shu satr, `SECRET_KEY` = uzun tasodifiy satr.

Shunda vazifalar, xotira, xarajat, eslatmalar, **vazifa fayllari** (har biri 5 MB gacha) va **Telegram akkaunt sessiyasi** (`SECRET_KEY` bilan shifrlangan) bazada turadi: qayta ishga tushganda o'zi tiklanadi, disk kerak emas. `SECRET_KEY` ni o'zgartirsangiz, Telegram akkauntni qayta ulash kerak bo'ladi. Supabase bepul loyihasi bir hafta umuman ishlatilmasa pauzaga tushadi (ilova ishlab tursa tushmaydi).

DATABASE_URL qo'yilmasa (bepul Render + SQLite): har deploy/qayta ishga tushishda baza, fayllar va Telegram sessiyasi o'chadi.

## Hozircha yo'q (navbatda)
Ovozli javob (TTS) va jonli qo'ng'iroq, Google Kalendar/Gmail, WhatsApp Business, Instagram (Graph API), Telegram qo'ng'iroqlari, agent uchun alohida raqam, Docker sandbox, GitHub asbobi, vektor qidiruv (xotira hozir kalit so'z bo'yicha), VPS/Supabase joylashtirish.

Panelga yuklangan fayllar vazifaga nusxalanadi; `workspace/inbox` dagi 7 kundan eski yuklamalar ishga tushganda o'chiriladi.

## Ovozli qo'ng'iroq (ixtiyoriy)
Agent Telegram akkaunti (Telethon) orqali egasi bilan gaplashadi: qo'ng'iroq qilsangiz ko'taradi, hisobot/savollarga ovoz bilan javob beradi; panelda yoqilsa, vazifa tugaganda o'zi qo'ng'iroq qiladi. Faqat egasi ID'si (`OWNER_TELEGRAM_ID` yoki panelda kiritilgan) bilan ishlaydi. `py-tgcalls` kerak (requirements.txt'da), ovoz uchun `GEMINI_API_KEY`. Panel → Telegram akkaunt → Ovozli qo'ng'iroq. Doimiy ochiq server tavsiya etiladi (bepul Render uxlab qoladi).

## Zaxira nusxa (server)
O'rnatish (bir marta, root): `bash /opt/aijamoa/src/deploy/install-backup.sh` — har kuni 03:00 (Toshkent) bazaning nusxasi `/opt/aijamoa/backups/` ga yoziladi, oxirgi 14 tasi saqlanadi.
- Ro'yxat: `ls -lh /opt/aijamoa/backups` · Qo'lda nusxa: `systemctl start aijamoa-backup` · Holat: `systemctl list-timers aijamoa-backup.timer`
- Tiklash (Postgres, EHTIYOT: hozirgi ma'lumot ustiga yoziladi): `systemctl stop aijamoa`, keyin
  `gunzip -c /opt/aijamoa/backups/aijamoa-SANA.sql.gz | psql "$DATABASE_URL"` (bo'sh bazaga), so'ng `systemctl start aijamoa`.

## Ovozni tanish zanjiri (ixtiyoriy kalitlar)
Avto rejim: Google Cloud STT → Microsoft Azure Speech → Whisper (serverda, bepul) → Gemini. Bepul limit tugashiga 1 daqiqa qolsa keyingisiga o'tadi; natija ishonchsiz bo'lsa ham keyingisiga, oxirida Gemini ma'noni tushunadi. Sozlamalar → "Ovozni tanish" da rejim tanlanadi.
`.env` ga (bo'lganlarini): `GOOGLE_STT_KEY=` (Google Cloud → Speech-to-Text API kaliti), `AZURE_SPEECH_KEY=` va `AZURE_SPEECH_REGION=` (masalan `westeurope`), `WHISPER_MODEL=small` (yoki o'zbekcha CTranslate2 modeli yo'li; `off` — o'chirish). Bepul limitlar: `STT_GOOGLE_FREE_MIN=60`, `STT_AZURE_FREE_MIN=300`.
