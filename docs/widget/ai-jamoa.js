// AI Jamoa — iPhone vidjeti (Scriptable, App Store'da bepul). Versiya 2: jonli jamoa + moliya + mening kunim.
// O'rnatish: Scriptable → "+" → shu kodni qo'ying → nomi "AI Jamoa" → pastdagi ikki qatorni to'ldiring →
// bosh ekran yoki BLOKIROVKA ekraniga Scriptable vidjetini qo'shing → Script: AI Jamoa.
// O'lchamlar: kichik, o'rta, katta va blokirovka ekrani (aylana, to'rtburchak, bir qator). Vidjet o'lchamni o'zi aniqlaydi.
// Ish ketayotganda: kim nima qilyapti, vazifa xarajati va qadamlar. Tinch paytda: oxirgi natijalar, eslatmalar, moliya.
// DIQQAT: WIDGET_TOKEN faqat umumiy holatni o'qiydi (boshqarib bo'lmaydi), lekin baribir maxfiy saqlang.

const BASE_URL = "https://SIZNING-MANZIL";
const WIDGET_TOKEN = "WIDGET_TOKEN_NI_SHU_YERGA";

const LIME = new Color("#cfff1a"), BG = new Color("#0c0e0f"), CARD = new Color("#23272b"), MUTED = new Color("#8b918c");
const WHITE = new Color("#eef2ee"), RED = new Color("#ff5a52"), AMBER = new Color("#ffb03a"), DIM = new Color("#1d2023");
const AVBG = new Color("#1b2a07");
const fam = config.widgetFamily || "large";

async function load() {
  const req = new Request(`${BASE_URL}/api/widget?token=${encodeURIComponent(WIDGET_TOKEN)}`);
  req.timeoutInterval = 10;
  return await req.loadJSON();
}

// ---------- kichik yordamchilar ----------
const money = (v) => "$" + Number(v || 0).toFixed(2);
function txt(stack, text, size, color, bold, lines) {
  const t = stack.addText(String(text == null ? "" : text));
  t.font = bold ? Font.boldSystemFont(size) : Font.systemFont(size);
  t.textColor = color || WHITE; t.lineLimit = lines || 1; t.minimumScaleFactor = 0.75;
  return t;
}
function row(parent) { const r = parent.addStack(); r.layoutHorizontally(); r.centerAlignContent(); return r; }
function col(parent) { const c = parent.addStack(); c.layoutVertically(); return c; }
function section(w, left, right) {
  const r = row(w); txt(r, left.toUpperCase(), 10, MUTED, true); r.addSpacer();
  if (right) txt(r, right, 10, MUTED, false);
  w.addSpacer(3);
}
function bar(ratio, width, height, color) {
  const ctx = new DrawContext(); ctx.size = new Size(width, height); ctx.opaque = false; ctx.respectScreenScale = true;
  ctx.setFillColor(CARD);
  const bg = new Path(); bg.addRoundedRect(new Rect(0, 0, width, height), height / 2, height / 2); ctx.addPath(bg); ctx.fillPath();
  const r = Math.max(0, Math.min(1, ratio || 0));
  if (r > 0.005) {
    ctx.setFillColor(color || (r > 0.9 ? RED : r > 0.75 ? AMBER : LIME));
    const p = new Path(); p.addRoundedRect(new Rect(0, 0, Math.max(height, width * r), height), height / 2, height / 2); ctx.addPath(p); ctx.fillPath();
  }
  return ctx.getImage();
}
function timeline(list, width, height) {
  const n = Math.max(8, list.length), gap = 3, seg = (width - gap * (n - 1)) / n;
  const ctx = new DrawContext(); ctx.size = new Size(width, height); ctx.opaque = false; ctx.respectScreenScale = true;
  for (let i = 0; i < n; i++) {
    const s = list[i];
    ctx.setFillColor(s === "done" ? LIME : s === "running" ? AMBER : s === "failed" ? RED : s ? MUTED : DIM);
    const p = new Path(); p.addRoundedRect(new Rect(i * (seg + gap), 0, seg, height), 3, 3); ctx.addPath(p); ctx.fillPath();
  }
  return ctx.getImage();
}
function avatar(parent, letter, size) {
  const a = parent.addStack(); a.size = new Size(size, size); a.cornerRadius = size / 2; a.backgroundColor = AVBG; a.centerAlignContent();
  txt(a, letter, size * 0.48, LIME, true);
}
function agentLine(w, a, size) {
  const r = row(w); avatar(r, (a.label || a.name || "?")[0].toUpperCase(), size + 6); r.addSpacer(6);
  txt(r, `${a.label}: ${a.act}`, size, WHITE, false); r.addSpacer(4);
  txt(r, money(a.cost), size, LIME, true);
}
function stats(w, items, valSize) {
  const r = row(w);
  items.forEach(([v, label, color], i) => {
    const c = col(r); txt(c, v, valSize, color || WHITE, true); txt(c, label, 9, MUTED, false);
    if (i < items.length - 1) r.addSpacer();
  });
}
function liveHead(t) {
  const steps = t.steps_total ? `${t.steps_done}/${t.steps_total}` : "…";
  return `${steps}` + (t.eta_min ? ` · ~${t.eta_min} daq` : "");
}
function dayCounts(tl) {
  const c = (k) => tl.filter((x) => x === k).length;
  return `${c("done")} tayyor` + (c("running") ? ` · ${c("running")} ishda` : "") + ` · ${c("failed")} xato`;
}
function reminderText(r) { return `⏰ ${r.time} ${r.text}` + (r.call ? " 📞" : ""); }
function headerBadge(d) {
  const h = d.header || {};
  return [h.date, h.weather, h.usd ? "$1=" + h.usd : ""].filter(Boolean).join(" · ");
}

// ---------- blokirovka ekrani ----------
function lockWidget(d) {
  const w = new ListWidget();
  const live = (d.live || [])[0];
  if (fam === "accessoryInline") {
    w.addText(live ? `⚙️ #${live.id} ${liveHead(live)} · ${money(live.cost)}` : `✅ ${d.done_today} tayyor · ${money(d.today)}`);
    return w;
  }
  if (fam === "accessoryCircular") {
    const s = w.addStack(); s.layoutVertically(); s.centerAlignContent();
    if (live) { txt(s, live.steps_total ? `${live.steps_done}/${live.steps_total}` : "⚙️", 16, WHITE, true); txt(s, "#" + live.id, 10, WHITE, false); }
    else { txt(s, d.done_today, 18, WHITE, true); txt(s, d.pending ? "🔐" + d.pending : "tayyor", 9, WHITE, false); }
    return w;
  }
  if (live) {
    txt(w, `⚙️ AI Jamoa · #${live.id} · ${liveHead(live)}`, 13, WHITE, true);
    const who = (live.agents || []).map((a) => a.label).join(", ") || live.phase || "ishlanmoqda";
    txt(w, `${who} · ${money(live.cost)}`, 12, WHITE, false);
    if ((live.agents || [])[0]) txt(w, live.agents[0].act, 11, WHITE, false);
    else if (d.pending) txt(w, `🔐 ${d.pending} ta ruxsat kutyapti`, 11, WHITE, false);
  } else {
    txt(w, "✅ AI Jamoa · tinch", 13, WHITE, true);
    txt(w, `bugun ${d.done_today} tayyor · ${money(d.today)} · qoldi ${money(d.budget_left)}`, 12, WHITE, false);
    const r = ((d.day || {}).reminders || [])[0];
    if (r) txt(w, reminderText(r), 11, WHITE, false);
  }
  return w;
}

// ---------- bosh ekran ----------
function small(w, d) {
  const live = (d.live || [])[0];
  if (live) {
    txt(w, `● #${live.id} · ${liveHead(live)}`, 13, LIME, true);
    w.addSpacer(6);
    const pct = live.steps_total ? Math.round((live.steps_done / live.steps_total) * 100) : 0;
    txt(w, pct + "%", 30, WHITE, true);
    w.addImage(bar(pct / 100, 130, 6, LIME));
    w.addSpacer(6);
    txt(w, (live.agents || []).map((a) => a.label).join(", ") || live.phase || "ishlanmoqda", 11, MUTED, false, 2);
    w.addSpacer();
    txt(w, `${money(live.cost)} / ${money(live.limit)}`, 12, LIME, true);
  } else {
    txt(w, "○ Tinch", 13, LIME, true);
    w.addSpacer(6);
    txt(w, money(d.today), 30, WHITE, true);
    txt(w, "bugungi sarf", 10, MUTED, false);
    w.addSpacer(6);
    w.addImage(bar(d.budget_total ? 1 - d.budget_left / d.budget_total : 0, 130, 6));
    w.addSpacer();
    txt(w, `qoldi ${money(d.budget_left)} · ${d.done_today} tayyor`, 11, MUTED, false);
  }
}

function medium(w, d) {
  const live = (d.live || [])[0], day = d.day || {}, m = d.money || {};
  if (live) {
    const h = row(w); txt(h, `● #${live.id} · ${liveHead(live)}`, 15, LIME, true); h.addSpacer();
    if (d.pending) txt(h, `🔐 ${d.pending} ruxsat`, 11, AMBER, true);
    else txt(h, live.request, 11, MUTED, false);
    w.addSpacer(6);
    const ag = (live.agents || []).slice(0, 2);
    if (ag.length) ag.forEach((a) => { agentLine(w, a, 12); w.addSpacer(3); });
    else { txt(w, "⚙️ " + (live.phase || "ishlanmoqda"), 12, WHITE, false); w.addSpacer(3); }
    const r = (day.reminders || [])[0];
    if (r) txt(w, reminderText(r), 11, MUTED, false);
    w.addSpacer();
    stats(w, [[money(live.cost), "vazifa / " + money(live.limit), LIME], [money(m.today), "bugun"], [money(m.month), "oy"], [money(d.budget_left), "qoldi"]], 16);
  } else {
    const h = row(w); txt(h, "○ AI Jamoa · tinch", 15, LIME, true); h.addSpacer();
    const hd = d.header || {};
    txt(h, [hd.weather, hd.usd ? "$1=" + hd.usd : ""].filter(Boolean).join(" · "), 11, MUTED, false);
    w.addSpacer(6);
    const last = (d.recent_done || [])[0];
    if (last) { const r = row(w); txt(r, `✅ #${last.id} ${last.request}`, 12, WHITE, false); r.addSpacer(4); txt(r, money(last.cost), 12, LIME, true); w.addSpacer(3); }
    const rem = (day.reminders || [])[0];
    if (rem) { txt(w, reminderText(rem), 12, WHITE, false); w.addSpacer(3); }
    w.addImage(timeline(day.timeline || [], 300, 7));
    w.addSpacer();
    stats(w, [[d.done_today, "bugun tayyor", LIME], [money(m.today), "bugun"], ["~" + money(m.forecast), "oy prognozi", AMBER], [money(d.budget_left), "qoldi"]], 16);
  }
}

function large(w, d) {
  const lives = d.live || [], live = lives[0], day = d.day || {}, m = d.money || {};
  const h = row(w);
  txt(h, live ? "● AI Jamoa" : "○ AI Jamoa · tinch", 16, LIME, true); h.addSpacer();
  txt(h, headerBadge(d), 10, MUTED, false);
  w.addSpacer(8);

  if (live) {
    section(w, `Hozir · #${live.id} ${live.request}`, liveHead(live));
    const ag = (live.agents || []).slice(0, 3);
    if (ag.length) ag.forEach((a) => { agentLine(w, a, 12); w.addSpacer(4); });
    else { txt(w, "⚙️ " + (live.phase || "ishlanmoqda"), 12, WHITE, false); w.addSpacer(4); }
    if (live.phase && ag.length) { txt(w, live.phase, 10, MUTED, false); w.addSpacer(3); }
    w.addImage(bar(live.limit ? live.cost / live.limit : 0, 310, 6));
    const r = row(w); txt(r, `vazifa: ${money(live.cost)} / ${money(live.limit)} limit`, 10, MUTED, false); r.addSpacer();
    if (d.pending) txt(r, `🔐 ${d.pending} ruxsat kutyapti`, 10, AMBER, true);
    if (lives[1]) { w.addSpacer(3); txt(w, `⚙️ #${lives[1].id} ${lives[1].request} · ${liveHead(lives[1])} · ${money(lives[1].cost)}`, 11, MUTED, false); }
  } else {
    section(w, "Oxirgi natijalar", "");
    (d.recent_done || []).forEach((t) => { const r = row(w); txt(r, `✅ #${t.id} ${t.request}`, 12, WHITE, false); r.addSpacer(4); txt(r, money(t.cost), 12, LIME, true); w.addSpacer(3); });
    if (!(d.recent_done || []).length) txt(w, "Hali tayyor vazifa yo'q", 12, MUTED, false);
    if (day.auto) txt(w, "🤖 " + day.auto, 11, MUTED, false);
  }

  w.addSpacer(10);
  section(w, "Moliya", "");
  stats(w, [[money(m.today), "bugun", LIME], [money(m.month), "shu oy"], ["~" + money(m.forecast), "oy prognozi", AMBER], [money(d.budget_left), "qoldi"]], 17);
  w.addSpacer(5);
  (m.providers || []).slice(0, 2).forEach((p) => {
    const r = row(w); const n = r.addStack(); n.size = new Size(52, 0); txt(n, p.name, 10, WHITE, false);
    r.addImage(bar(p.budget ? p.spent / p.budget : 0, 170, 5)); r.addSpacer();
    txt(r, `${money(p.spent)}/${money(p.budget).replace(".00", "")}`, 10, MUTED, false);
    w.addSpacer(3);
  });

  w.addSpacer(8);
  section(w, "Mening kunim", dayCounts(day.timeline || []));
  w.addImage(timeline(day.timeline || [], 310, 8));
  w.addSpacer(5);
  (day.reminders || []).slice(0, live ? 1 : 2).forEach((r) => { txt(w, reminderText(r), 12, WHITE, false); w.addSpacer(3); });
  if (day.watch_hit) txt(w, "🔔 " + day.watch_hit.text, 11, AMBER, false);
  else if (day.plan) txt(w, `☑️ Reja: ${day.plan.title} · ${day.plan.done}/${day.plan.total}`, 11, WHITE, false);
}

async function build() {
  let d;
  try { d = await load(); } catch (e) {
    const w = new ListWidget(); w.backgroundColor = BG;
    txt(w, "AI Jamoa", 15, LIME, true); txt(w, "Aloqa yo'q", 13, RED, false);
    w.refreshAfterDate = new Date(Date.now() + 5 * 60 * 1000); return w;
  }
  const running = (d.live || []).length > 0;
  // ish ketayotganda tezroq yangilashni so'raymiz (iOS baribir o'zi hal qiladi: odatda 2-15 daqiqa)
  const next = new Date(Date.now() + (running ? 2 : 10) * 60 * 1000);
  if (fam.startsWith("accessory")) { const lw = lockWidget(d); lw.url = BASE_URL; lw.refreshAfterDate = next; return lw; }

  const w = new ListWidget(); w.backgroundColor = BG; w.url = BASE_URL;
  w.setPadding(fam === "small" ? 14 : 14, 16, 14, 16);
  if (!d.v) {   // server eski versiyada: yangilash kerak
    txt(w, "AI Jamoa", 15, LIME, true); w.addSpacer(4);
    txt(w, "Serverni yangilang (git pull && update.sh)", 12, AMBER, false, 2);
    w.addSpacer(4); txt(w, `bugun ${d.done_today} tayyor · ${money(d.today)}`, 12, WHITE, false);
  } else if (fam === "small") small(w, d);
  else if (fam === "medium") medium(w, d);
  else large(w, d);
  w.refreshAfterDate = next;
  return w;
}

const widget = await build();
if (config.runsInWidget) Script.setWidget(widget);
else if (fam === "small") await widget.presentSmall();
else if (fam === "medium") await widget.presentMedium();
else await widget.presentLarge();
Script.complete();
