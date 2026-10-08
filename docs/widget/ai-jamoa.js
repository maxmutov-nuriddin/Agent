// AI Jamoa — iPhone bosh ekran vidjeti (Scriptable ilovasi uchun, App Store'da bepul).
// O'rnatish: Scriptable → "+" → shu kodni qo'ying → nomi "AI Jamoa" → bosh ekranda
// vidjet qo'shing (Scriptable) → "Script" = AI Jamoa.
// DIQQAT: WIDGET_TOKEN faqat umumiy holatni o'qiydi (boshqarib bo'lmaydi), lekin baribir maxfiy saqlang.

const BASE_URL = "https://SIZNING-MANZIL";      // masalan http://192.168.1.20:8080 yoki Tailscale/Tunnel manzili
const WIDGET_TOKEN = "WIDGET_TOKEN_NI_SHU_YERGA"; // .env dagi WIDGET_TOKEN qiymati

const LIME = new Color("#c7f23a"), BG = new Color("#0a0c0e"), MUTED = new Color("#8a958e"), WHITE = new Color("#eef2ee");
const RED = new Color("#ff5c5c");

async function load() {
  const req = new Request(`${BASE_URL}/api/widget?token=${encodeURIComponent(WIDGET_TOKEN)}`);
  req.timeoutInterval = 10;
  return await req.loadJSON();
}

function line(w, text, size, color, bold) {
  const t = w.addText(text);
  t.font = bold ? Font.boldSystemFont(size) : Font.systemFont(size);
  t.textColor = color;
  t.lineLimit = 1;
  return t;
}

async function build() {
  const w = new ListWidget();
  w.backgroundColor = BG;
  w.url = BASE_URL;
  w.setPadding(14, 14, 14, 14);
  let d;
  try {
    d = await load();
  } catch (e) {
    line(w, "AI Jamoa", 15, LIME, true);
    line(w, "Aloqa yo'q", 13, RED, false);
    w.refreshAfterDate = new Date(Date.now() + 5 * 60 * 1000);
    return w;
  }
  const busy = d.working.length > 0;
  line(w, (d.paused ? "⏸ " : busy ? "● " : "○ ") + "AI Jamoa", 15, LIME, true);
  w.addSpacer(4);
  line(w, d.paused ? "To'xtatilgan" : busy ? d.working.slice(0, 2).join(", ") + " ishlayapti" : "Hammasi tinch", 12, WHITE, false);
  w.addSpacer(6);
  if (d.pending > 0) line(w, `🔐 ${d.pending} ta qaror kutmoqda`, 13, LIME, true);
  line(w, `Bugun: $${d.today.toFixed(2)}`, 12, MUTED, false);
  line(w, `Qoldi: $${d.budget_left.toFixed(2)}`, 12, MUTED, false);
  if (d.last_task && config.widgetFamily !== "small") {
    w.addSpacer(6);
    line(w, `#${d.last_task.id} ${d.last_task.status}: ${d.last_task.request}`, 12, WHITE, false);
  }
  w.refreshAfterDate = new Date(Date.now() + 5 * 60 * 1000); // iOS o'zi qaror qiladi, odatda 5-15 daqiqa
  return w;
}

const widget = await build();
if (config.runsInWidget) Script.setWidget(widget);
else await widget.presentMedium();
Script.complete();
