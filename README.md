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
`/team` `/tasks` `/task <id>` `/budget` `/report` `/memory` `/hire <nom> <sabab>` `/fire <nom>` `/review` `/pause` `/resume`. Oddiy matn = yangi vazifa; fayl yuborsangiz vazifaga ilova bo'ladi. Tayyor fayllar vazifa tugagach Telegramga yuboriladi. Har kuni `REPORT_HOUR` da hisobot keladi (LLM ishlatmaydi, pul sarflamaydi). Bot faqat `OWNER_TELEGRAM_ID` ga javob beradi.

## Veb-panel (PWA) va iPhone
`python -m aicompany run` Telegram botdan tashqari veb-panelni ham ishga tushiradi (faqat veb: `python -m aicompany web`). Terminalda maxfiy havola chiqadi: `http://...:8080/#token=...`. Kalit birinchi ishga tushirishda avtomatik yaratilib `.env` ga yoziladi.

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

## Agentlar nima qila oladi (asboblar)
| Guruh | Asboblar | Eslatma |
|---|---|---|
| files | write_file, read_file, list_files | faqat vazifaning `workspace/task_N/` papkasi ichida |
| web | web_search, fetch_url | ichki/lokal manzillar bloklangan; natija "ishonchsiz" deb belgilanadi |
| memory | remember, recall | uzoq muddatli xotira |
| shell | run_command | **har bir buyruq Telegramda sizning tasdig'ingizni kutadi** (✅/❌), 10 daqiqada javob bo'lmasa rad etiladi |

Rollarga guruhlar biriktirilgan (masalan faqat dasturchida `shell` bor). HR yangi xodim yaratganda unga kerakli guruhlarni tanlaydi.

**Xavfsizlik chegarasi:** `run_command` to'liq izolyatsiyalangan sandbox EMAS. U workspace papkasida, API kalitlarisiz (toza muhit) ishlaydi, lekin sizning kompyuteringizdagi oddiy jarayon. Shuning uchun har buyruq tasdiqlanadi. Buyruqni o'qib chiqib keyin ruxsat bering. Kelajakda Docker sandbox qo'shiladi.

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

## Hozircha yo'q
Docker sandbox, GitHub asbobi, ovozli xabar, OpenAI/Gemini uchun asbob qo'llash (ular faqat matn rejimida ishlaydi), vektor qidiruv (xotira hozir kalit so'z bo'yicha), veb-panel, VPS/Supabase joylashtirish.
