# AI Kompaniya — reja

Maqsad: bitta vazifa beriladi, AI jamoa (rahbar, HR, dasturchi, marketolog, QA va h.k.) uni mustaqil bajarib tayyor natija topshiradi. Odam faqat muhim qarorlarni tasdiqlaydi.

## Qabul qilingan qarorlar
| Mavzu | Qaror |
|---|---|
| Modellar | Claude + GPT + Gemini, bitta umumiy adapter orqali |
| Tejamkorlik | Model kaskadi (arzon -> qimmat), prompt caching, natija keshi, qisqa kontekst, qattiq byudjet limiti |
| Til | Python (yadro), keyinroq TypeScript (veb-panel) |
| Interfeys | Telegram bot (aiogram), faqat egasining ID'iga javob beradi |
| Boshqaruv | Foydalanuvchi faqat CEO-agent bilan gaplashadi |
| Xotira ("miya") | Postgres: lokalda Docker, keyinroq Supabase; pgvector xotira uchun |
| Ishga tushirish | Hozir lokal, loyiha tayyor bo'lgach VPS |
| Asboblar | MCP asosida kengayuvchan; agent yetishmagan ruxsatni tasdiqlash kartasi orqali so'raydi |
| Xavfsizlik | O'qish: erkin. Yozish: jurnal bilan. Qaytarib bo'lmaydigan amal: tasdiq bilan |

## Arxitektura
```
Egasi (Telegram) <-> Bot <-> CEO-agent <-> HR / Dasturchi / Marketolog / QA ...
                                  |
                      Postgres (agents, tasks, messages,
                      memories, usage, approvals, audit_log)
```

## Lokal ishga tushirish uchun talablar
- Kod VPS'ga ko'chirishga tayyor yoziladi: barcha sozlama `.env` da, hech narsa qattiq yozilmaydi.
- Postgres `docker compose` bilan; Supabase'ga o'tish faqat ulanish satrini almashtirishdir.
- Telegram long polling (webhook keyinroq).
- Kalitlar faqat `.env` da, `.gitignore` ga kiritilgan, repoga hech qachon tushmaydi.

## Bosqichlar
1. **MVP:** model adapterlari, kaskad, byudjet hisobi, CEO + 2-3 mutaxassis + QA, Telegram bot (matn, /team, /tasks, /budget, /pause), tasdiqlash kartalari.
2. **HR:** agent yaratish/o'chirish, uzoq muddatli xotira (pgvector), kunlik hisobot.
3. **Asboblar:** kod sandbox, GitHub, veb-qidiruv, brauzer, MCP ulanishlar.
4. **Joylashtirish:** VPS, Supabase, doimiy ishlash (systemd/Docker).
5. **Qo'shimcha:** veb-panel (PWA), qurilma agenti, mobil ilova.

## Xavflar
- Cheksiz sikl va xarajat: byudjet limiti va chuqurlik chegarasi.
- Sifat: QA agent va testsiz natija "tayyor" hisoblanmaydi.
- Xavfsizlik: kod izolyatsiyalangan muhitda; kalitlar agentga to'g'ridan-to'g'ri berilmaydi.

## Ochiq savollar
- Birinchi haqiqiy vazifa nima? (tizimni shu bilan sinaymiz)
- Egasining qurilmalari (iPhone/Android, Windows/Mac) — qurilma agenti uchun.
- Kunlik/oylik byudjet chegarasi qancha?
