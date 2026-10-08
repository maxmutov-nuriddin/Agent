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
`.env` da kerak: `TELEGRAM_BOT_TOKEN` (@BotFather), `OWNER_TELEGRAM_ID` (@userinfobot), kamida bitta API kalit.

## Telegram buyruqlari
`/team` `/tasks` `/task <id>` `/budget` `/hire <nom> <sabab>` `/fire <nom>` `/pause` `/resume`. Oddiy matn = yangi vazifa. Bot faqat `OWNER_TELEGRAM_ID` ga javob beradi.

## Tejamkorlik
Router vazifa darajasiga (cheap/mid/strong) qarab eng arzon provayderni tanlaydi, xato bo'lsa keyingisiga o'tadi. Claude uchun prompt caching va past `effort` yoqilgan. Limit tugagan provayder o'tkazib yuboriladi. Bitta vazifa uchun `MAX_TASK_USD` chegarasi bor. 80% da Telegramga ogohlantirish keladi. Limitni oshirish: `.env` dagi `BUDGET_USD_*`.

## Muhim
`models.yaml` dagi OpenAI/Gemini model nomlari va narxlar tasdiqlanmagan. Kalit qo'ygach `check` ni ishga tushiring.

## Hozircha yo'q (keyingi bosqich)
Asboblar (kod bajarish, veb, GitHub), vektor xotira, tasdiqlash kartalari, HR'ning o'zi ishdan bo'shatishi, VPS/Supabase.
