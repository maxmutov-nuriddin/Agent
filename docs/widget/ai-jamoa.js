// AI Jamoa — iPhone vidjeti (Scriptable, App Store'da bepul).
// O'rnatish: Scriptable → "+" → shu kodni qo'ying → nomi "AI Jamoa" → pastdagi ikki qatorni to'ldiring →
// bosh ekran yoki BLOKIROVKA ekraniga Scriptable vidjetini qo'shing → Script: AI Jamoa.
// Kichik/o'rta/katta o'lcham va blokirovka ekrani (aylana, to'rtburchak, bir qator) qo'llanadi.
// DIQQAT: WIDGET_TOKEN faqat umumiy holatni o'qiydi (boshqarib bo'lmaydi), lekin baribir maxfiy saqlang.

const BASE_URL = "https://SIZNING-MANZIL";
const WIDGET_TOKEN = "WIDGET_TOKEN_NI_SHU_YERGA";

const LIME = new Color("#c7f23a"), BG = new Color("#0a0c0e"), CARD = new Color("#15191c"), MUTED = new Color("#8a958e");
const WHITE = new Color("#eef2ee"), RED = new Color("#ff5c5c"), AMBER = new Color("#ffb03a");
const fam = config.widgetFamily || "medium";

async function load() {
  const req = new Request(`${BASE_URL}/api/widget?token=${encodeURIComponent(WIDGET_TOKEN)}`);
  req.timeoutInterval = 10;
  return await req.loadJSON();
}
function txt(stack, text, size, color, bold, lines) {
  const t = stack.addText(String(text));
  t.font = bold ? Font.boldSystemFont(size) : Font.systemFont(size);
  t.textColor = color; t.lineLimit = lines || 1;
  return t;
}
function budgetBar(d, width, height) {
  const ctx = new DrawContext(); ctx.size = new Size(width, height); ctx.opaque = false; ctx.respectScreenScale = true;
  const used = d.budget_total ? Math.min(1, Math.max(0, 1 - d.budget_left / d.budget_total)) : 0;
  ctx.setFillColor(CARD); ctx.fillPath(new Path().addRoundedRect(new Rect(0, 0, width, height), height / 2, height / 2));
  if (used > 0.01) {
    ctx.setFillColor(used > 0.9 ? RED : used > 0.7 ? AMBER : LIME);
    const p = new Path(); p.addRoundedRect(new Rect(0, 0, Math.max(height, width * used), height), height / 2, height / 2); ctx.addPath(p); ctx.fillPath();
  }
  return ctx.getImage();
}
function status(d) { return d.paused ? ["⏸", "To'xtatilgan", AMBER] : d.running.length ? ["●", d.running.length + " ta ish ketmoqda", LIME] : ["○", "Hammasi tinch", MUTED]; }

// --- Blokirovka ekrani ---
function lockWidget(d) {
  const w = new ListWidget();
  const [icon, label] = status(d);
  if (fam === "accessoryInline") { w.addText(`${icon} AI: ${d.running.length} ish · ${d.pending} qaror · $${d.today.toFixed(2)}`); return w; }
  if (fam === "accessoryCircular") {
    const s = w.addStack(); s.layoutVertically(); s.centerAlignContent();
    txt(s, d.running.length, 20, WHITE, true); txt(s, d.pending ? "🔐" + d.pending : "ish", 10, WHITE, false);
    return w;
  }
  txt(w, `${icon} AI Jamoa`, 13, WHITE, true);
  txt(w, d.running.length ? d.running[0].request : label, 12, WHITE, false, 2);
  txt(w, `${d.pending ? "🔐" + d.pending + " qaror · " : ""}bugun ${d.done_today} tayyor · $${d.today.toFixed(2)}`, 11, WHITE, false);
  return w;
}

// --- Bosh ekran ---
async function build() {
  let d;
  try { d = await load(); } catch (e) {
    const w = new ListWidget(); w.backgroundColor = BG;
    txt(w, "AI Jamoa", 15, LIME, true); txt(w, "Aloqa yo'q", 13, RED, false);
    w.refreshAfterDate = new Date(Date.now() + 5 * 60 * 1000); return w;
  }
  if (fam.startsWith("accessory")) { const lw = lockWidget(d); lw.url = BASE_URL; lw.refreshAfterDate = new Date(Date.now() + 10 * 60 * 1000); return lw; }

  const w = new ListWidget(); w.backgroundColor = BG; w.url = BASE_URL; w.setPadding(14, 14, 14, 14);
  const [icon, label, col] = status(d);
  const head = w.addStack(); head.centerAlignContent();
  txt(head, icon + " AI Jamoa", 15, LIME, true); head.addSpacer();
  if (d.pending > 0) txt(head, "🔐 " + d.pending + " qaror", 12, AMBER, true);
  w.addSpacer(4);
  txt(w, label, 13, col === MUTED ? WHITE : col, true);
  w.addSpacer(6);

  if (fam === "small") {
    if (d.running[0]) txt(w, d.running[0].request, 11, MUTED, false, 2);
    w.addSpacer();
    txt(w, `Bugun $${d.today.toFixed(2)} · ${d.done_today} tayyor`, 11, WHITE, false);
    w.addImage(budgetBar(d, 130, 6)); 
    txt(w, `Qoldi $${d.budget_left.toFixed(2)}`, 10, MUTED, false);
  } else {
    d.running.slice(0, fam === "large" ? 3 : 1).forEach((t) => txt(w, `⚙️ #${t.id} ${t.request}`, 12, WHITE, false));
    if (!d.running.length && d.last_task) txt(w, `#${d.last_task.id} ${d.last_task.status}: ${d.last_task.request}`, 12, MUTED, false);
    w.addSpacer(6);
    const row = w.addStack();
    const stat = (v, l, c) => { const s = row.addStack(); s.layoutVertically(); txt(s, v, 17, c || WHITE, true); txt(s, l, 10, MUTED, false); row.addSpacer(); };
    stat(d.done_today, "bugun tayyor", LIME); stat(d.failed, "xato", d.failed ? RED : WHITE); stat("$" + d.today.toFixed(2), "bugungi sarf"); stat("$" + d.budget_left.toFixed(2), "qoldi");
    w.addSpacer(6);
    w.addImage(budgetBar(d, fam === "large" ? 300 : 300, 6));
    if (d.next_reminder) { w.addSpacer(6); txt(w, `⏰ ${d.next_reminder.local} — ${d.next_reminder.text}`, 12, AMBER, false); }
    if (fam === "large") {
      w.addSpacer(4);
      if (d.location) txt(w, `📍 Joylashuv: ${d.location.age}`, 11, MUTED, false);
      txt(w, `Faol agentlar: ${d.working.length ? d.working.join(", ") : "yo'q"}`, 11, MUTED, false);
    }
  }
  w.refreshAfterDate = new Date(Date.now() + 5 * 60 * 1000); // iOS o'zi qaror qiladi, odatda 5-15 daqiqa
  return w;
}

const widget = await build();
if (config.runsInWidget) Script.setWidget(widget);
else await widget.presentMedium();
Script.complete();
