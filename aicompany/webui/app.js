"use strict";
// AI Jamoa paneli. innerHTML faqat o'zgarmas ikonkalar uchun (svg yordamchisi); agent/foydalanuvchi matni doim textContent.
const $ = (id) => document.getElementById(id);
const IC = {
  team: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3.2"/><path d="M3 20a6 6 0 0 1 12 0"/><circle cx="17.5" cy="9" r="2.4"/><path d="M16.5 14.2A5 5 0 0 1 21 19"/></svg>',
  cards: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="5" width="16" height="14" rx="3.5"/><path d="M8.5 12l2.5 2.5 4.5-5"/></svg>',
  tasks: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/></svg>',
  stats: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 20V11M12 20V4M19 20v-6"/></svg>',
  mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>',
  send: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3.4 20.4 21 12 3.4 3.6l.1 6.5 10.9 1.9-10.9 1.9z"/></svg>',
};
const PANEL_V = "2026.10.08-k";
const PROV = { anthropic: "Claude", gemini: "Gemini", openai: "ChatGPT", auto: "Avto" };
const TABS = [["team", "Jamoa"], ["cards", "Kartalar"], ["tasks", "Vazifalar"], ["stats", "Hisob"]];
const ST = { done: ["Tayyor", ""], running: ["Ishlayapti", "on"], failed: ["Xato", "red"], cancelled: ["Siz to'xtatdingiz", "amber"],
  limit: ["Limit tugadi", "amber"], paused: ["Pauza", "amber"], interrupted: ["Uzildi (dastur qayta yoqilgan)", "amber"], stopped: ["To'xtatilgan", "amber"] };
const WHY = { done: "", running: "", failed: "Vazifa xato bilan tugadi.", cancelled: "Siz uni qo'lda to'xtatdingiz. Bajarilgan qismi saqlangan.",
  limit: "Bitta vazifa uchun ajratilgan pul limiti tugadi. .env dagi MAX_TASK_USD ni oshirishingiz mumkin.", paused: "Hammasi pauzaga qo'yilgan edi.",
  interrupted: "Dastur qayta ishga tushganda vazifa o'rtada uzilgan.", stopped: "" };
const APPR = { approved: ["✓ Ruxsat berdingiz", ""], denied: ["✕ Siz rad etdingiz", "red"], expired: ["⏱ Javob bermadingiz (muddat tugadi)", "amber"], pending: ["Javob kutilmoqda", ""] };
const TEXT_EXT = /\.(txt|md|html?|css|js|mjs|json|py|ts|tsx|jsx|csv|xml|ya?ml|sh|sql|java|c|cpp|h|go|rs|php|rb|svg|toml|ini|log)$/i;
const S = { token: localStorage.getItem("aij_token") || "", tab: "team", state: null, sig: {}, chat: [], lastChat: 0, chatOpen: false, typing: false, skip: 0, archive: false };

// ---------- yordamchilar ----------
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}
const svg = (html) => { const s = document.createElement("span"); s.innerHTML = html; return s.firstChild; };
const usd = (n) => "$" + Number(n || 0).toFixed(Math.abs(n) >= 10 ? 1 : 2);
const initials = (n) => n.split("_").map((p) => p[0] || "").join("").slice(0, 2).toUpperCase();
function toast(msg) { const t = $("toast"); t.textContent = msg; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), 2400); }
async function api(path, opts = {}) {
  const r = await fetch("/api" + path, { ...opts, headers: { Authorization: "Bearer " + S.token, "Content-Type": "application/json" } });
  if (r.status === 401) { showLogin(); throw new Error("auth"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || "Xatolik " + r.status);
  return data;
}
const post = (p, body) => api(p, { method: "POST", body: JSON.stringify(body || {}) });
function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso)) / 1000;
  return s < 60 ? "hozir" : s < 3600 ? Math.floor(s / 60) + " daq" : s < 86400 ? Math.floor(s / 3600) + " soat" : Math.floor(s / 86400) + " kun";
}

// ---------- kirish ----------
function showLogin(msg) { $("login").hidden = false; $("login-err").textContent = msg || ""; }
async function tryLogin(token) {
  S.token = token;
  try { await api("/state"); } catch (e) { if (e.message !== "auth") showLogin(e.message); return false; }
  localStorage.setItem("aij_token", token); $("login").hidden = true; return true;
}
$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!(await tryLogin($("token").value.trim()))) $("login-err").textContent = "Kalit noto'g'ri"; else start();
});

// ---------- oyna ----------
function openSheet(...kids) { $("sheet-body").replaceChildren(...kids.filter((k) => k != null && k !== false)); $("sheet").hidden = false; }
const closeSheet = () => { $("sheet").hidden = true; };
$("sheet-bg").addEventListener("click", closeSheet);

// ---------- pastki menyu ----------
function renderTabs() {
  const nav = $("tabs"); nav.replaceChildren();
  for (const [id, label] of TABS) {
    const b = h("button", { class: "tab" + (S.tab === id ? " on" : ""), onclick: () => go(id), "aria-label": label },
      h("span", { class: "ic" }, svg(IC[id])), label);
    if (id === "cards" && S.state && S.state.pending) b.append(h("span", { class: "badge" }, S.state.pending));
    nav.append(b);
  }
}
function go(tab) { S.tab = tab; S.sig = {}; renderTabs(); refresh(true); $("view").scrollTop = 0; }

// ---------- ko'rinishlar: har biri ma'lumot oladi va chizadi; o'zgarmasa qayta chizilmaydi ----------
const VIEWS = { team: [loadTeam, drawTeam], cards: [loadCards, drawCards], tasks: [loadTasks, drawTasks], stats: [loadStats, drawStats] };
async function refresh(force) {
  const [load, draw] = VIEWS[S.tab];
  try {
    const data = await load(), sig = JSON.stringify(data);
    if (!force && S.sig[S.tab] === sig) return;
    S.sig[S.tab] = sig;
    const v = $("view"), top = v.scrollTop;
    v.replaceChildren(...draw(data).filter((n) => n != null && n !== false)); v.scrollTop = top;
  } catch (e) { if (e.message !== "auth") $("view").replaceChildren(h("div", { class: "empty" }, e.message)); }
}
function head(title, extra) { return h("div", { class: "head" }, h("h1", { class: "title" }, title), extra); }
function liveTag() {
  const st = S.state; if (!st) return null;
  const cls = st.paused ? "pause" : st.working.length ? "on" : "";
  return h("div", { class: "live " + cls }, h("i"), st.paused ? "Pauza" : st.working.length ? "Ishlayapti" : "Aloqada");
}

// --- Jamoa ---
async function loadTeam() {
  const [state, team] = await Promise.all([api("/state"), api("/team")]);
  S.state = state; renderTabs();
  const ceo = [...S.chat].reverse().find((m) => m.role === "ceo");
  return { state, team, ceo: ceo ? ceo.text : "" };
}
function drawTeam({ state, team, ceo }) {
  const sorted = [...team].sort((a, b) => b.busy - a.busy);
  const quote = ceo && !ceo.startsWith("[Vazifa") ? ceo : "Salom! Men Rahbarman. Suhbatlashing yoki «Vazifa berish» tugmasini bosing.";
  return [
    head("Jamoa", liveTag()),
    h("div", { class: "label" }, "Hozir ishda"),
    sorted.some((a) => a.busy)
      ? h("div", { class: "avatars" }, sorted.filter((a) => a.busy).map((a) => h("div", { class: "av busy" }, h("i", {}, initials(a.name)), h("span", { class: "nm" }, a.name))))
      : h("p", { class: "hint" }, "Hozir hamma bo'sh. Vazifa bering."),
    h("div", { class: "card lime" },
      h("div", { class: "row" }, h("div", { class: "ava" }, "R"),
        h("div", {}, h("div", { class: "who" }, "Rahbar"), h("div", { class: "st" }, state.pending ? `onlayn · ${state.pending} ta qaror kutmoqda` : "onlayn"))),
      h("blockquote", {}, quote),
      h("div", { class: "btns" }, h("button", { class: "btn black", onclick: () => openChat(false) }, "Chatni ochish"),
        h("button", { class: "btn outline", onclick: taskSheet }, "Vazifa berish"))),
    h("div", { class: "stats" },
      h("div", { class: "stat" }, h("b", {}, state.done_today), h("span", {}, "bugun bajarildi")),
      h("div", { class: "stat" }, h("b", {}, state.working.length), h("span", {}, "hozir ishlayapti")),
      h("button", { class: "stat" + (state.pending ? " hot" : ""), onclick: () => go("cards") }, h("b", {}, state.pending), h("span", {}, "sizni kutmoqda"))),
    h("div", { class: "label" }, "Xodimlar"),
    ...sorted.map((a) => h("button", { class: "item", onclick: () => agentSheet(a) },
      h("div", { class: "ava" + (a.busy ? " busy" : "") }, initials(a.name)),
      h("div", { class: "grow" }, h("h3", {}, a.name), h("p", {}, a.busy ? "ishlayapti · vazifa #" + a.task_id : a.role)),
      h("span", { class: "dot" + (a.busy ? " on" : "") }))),
    h("div", { class: "label" }),
    h("div", { class: "acts" }, h("button", { class: "btn ghost", onclick: hireSheet }, "+ Xodim yollash"),
      h("button", { class: "btn ghost", onclick: hrReview }, "🧑‍💼 HR tahlili")),
  ];
}
async function hrReview() {
  try {
    const r = await post("/team/review");
    toast(r.fired.length ? "Bo'shatildi: " + r.fired.join(", ") : r.tasks < 10 ? `Tahlil uchun 10 ta vazifa kerak (hozir ${r.tasks})` : "Hamma xodim kerak, hech kim bo'shatilmadi");
    refresh(true);
  } catch (e) { toast(e.message); }
}
function agentSheet(a) {
  const fire = async () => { if (!confirm(a.name + " ishdan bo'shatilsinmi?")) return;
    try { await post("/team/fire", { name: a.name }); closeSheet(); toast("Bo'shatildi"); refresh(true); } catch (e) { toast(e.message); } };
  openSheet(h("h2", {}, a.name), h("p", { class: "muted" }, a.role),
    h("div", { class: "pills" }, h("span", { class: "pill" + (a.busy ? " lime" : "") }, a.busy ? "ishlayapti" : "bo'sh"), h("span", { class: "pill" }, a.tier),
      a.tools.map((t) => h("span", { class: "pill" }, t)), h("span", { class: "pill" }, a.steps + " ish"), h("span", { class: "pill" }, usd(a.cost))),
    a.core ? null : h("div", { class: "label" }), a.core ? null : h("button", { class: "btn red full", onclick: fire }, "Ishdan bo'shatish"));
}
function taskSheet() {
  const text = h("textarea", { class: "big", rows: "5", placeholder: "Nima qilish kerak? Qanchalik aniq bo'lsa, shuncha yaxshi." });
  const picker = h("input", { type: "file", multiple: "multiple", hidden: "hidden" });
  const chips = h("div", { class: "pills" });
  const uploaded = [];
  picker.addEventListener("change", async () => {
    for (const f of picker.files) {
      if (f.size > 10_000_000) { toast(f.name + ": 10MB dan katta"); continue; }
      try {
        const r = await fetch("/api/upload?name=" + encodeURIComponent(f.name), { method: "POST", headers: { Authorization: "Bearer " + S.token }, body: f });
        const d = await r.json(); if (!r.ok) throw new Error(d.error || "Yuklanmadi");
        uploaded.push(d.id); chips.append(h("span", { class: "pill lime" }, d.name));
      } catch (e) { toast(e.message); }
    }
    picker.value = "";
  });
  const btn = h("button", { class: "btn lime full" }, "Topshirish");
  btn.addEventListener("click", async () => {
    if (!text.value.trim()) return toast("Vazifani yozing");
    btn.disabled = true;
    try { await post("/tasks", { text: text.value, files: uploaded }); closeSheet(); toast("Vazifa topshirildi"); if (!S.chatOpen) go("tasks"); }
    catch (e) { toast(e.message); btn.disabled = false; }
  });
  openSheet(h("h2", {}, "Yangi vazifa"), h("p", { class: "muted" }, "Jamoa mustaqil bajaradi va natijani sizga topshiradi."),
    h("div", { class: "label" }), text, h("div", { class: "label" }), chips, picker,
    h("button", { class: "btn ghost full", onclick: () => picker.click() }, "📎 Fayl biriktirish"), h("div", { class: "label" }), btn);
  setTimeout(() => text.focus(), 50);
}
function hireSheet() {
  const name = h("input", { placeholder: "masalan: seo_mutaxassis", autocapitalize: "off" });
  const why = h("textarea", { rows: "3", placeholder: "Nima ish qiladi?" });
  const btn = h("button", { class: "btn lime full" }, "Yollash");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try { const r = await post("/team/hire", { name: name.value, why: why.value }); closeSheet(); toast("Yollandi: " + r.name); refresh(true); }
    catch (e) { toast(e.message); btn.disabled = false; }
  });
  openSheet(h("h2", {}, "Yangi xodim"), h("label", {}, "Nomi"), name, h("label", {}, "Vazifasi"), why, h("div", { class: "label" }), btn);
}

// --- Kartalar ---
async function loadCards() { const [state, items] = await Promise.all([api("/state"), api("/approvals")]); S.state = state; renderTabs(); return items; }
function drawCards(items) {
  const sub = items.length ? h("p", { class: "sub" }, items.length + " ta qaror kutmoqda") : null;
  if (!items.length) return [head("Kartalar", liveTag()), h("div", { class: "empty" }, h("b", {}, "✓"), "Hozircha qaror kutayotgan narsa yo'q.",
    h("p", { class: "hint" }, "Agent kompyuterda buyruq bajarmoqchi bo'lganda, ruxsat kartasi shu yerda paydo bo'ladi."))];
  const a = items[S.skip % items.length];
  const decide = async (ok) => { try { await post("/approvals/" + a.id, { approve: ok }); toast(ok ? "Ruxsat berildi" : "Rad etildi"); } catch (e) { toast(e.message); } S.skip = 0; refresh(true); };
  const col = (label, circle) => h("div", {}, circle, h("div", { class: "cap" }, label));
  return [h("div", { class: "cardsview" }, head("Kartalar", liveTag()), sub,
    h("div", { class: "paper" },
      h("div", { class: "meta" }, h("span", {}, a.agent + " · vazifa #" + a.task_id), h("span", { class: "tag" }, a.kind === "telegram" ? "Telegram" : "buyruq")),
      h("h2", {}, a.kind === "telegram" ? "Telegramda shu xabarni yuborishga ruxsat?" : "Terminal buyrug'ini bajarishga ruxsat?"), h("pre", {}, a.description)),
    h("div", { class: "actions" },
      col("Rad", h("button", { class: "circle no", onclick: () => decide(false), "aria-label": "Rad etish" }, "✕")),
      items.length > 1 ? col("Keyin", h("button", { class: "circle", onclick: () => { S.skip++; refresh(true); }, "aria-label": "Keyin" }, "›")) : null,
      col("Ruxsat", h("button", { class: "circle yes", onclick: () => decide(true), "aria-label": "Ruxsat berish" }, "✓"))))];
}

// --- Vazifalar ---
async function loadTasks() {
  const [state, tasks] = await Promise.all([api("/state"), api("/tasks" + (S.archive ? "?archived=1" : ""))]);
  S.state = state; renderTabs(); return { tasks, archive: S.archive };
}
function drawTasks({ tasks, archive }) {
  const seg = h("div", { class: "seg" },
    h("button", { class: archive ? "" : "on", onclick: () => { S.archive = false; refresh(true); } }, "Faol"),
    h("button", { class: archive ? "on" : "", onclick: () => { S.archive = true; refresh(true); } }, "Arxiv"));
  if (!tasks.length) return [head("Vazifalar", liveTag()), seg, h("div", { class: "empty" }, archive ? "Arxiv bo'sh" : "Hali vazifa yo'q. «Vazifa berish» tugmasini bosing.")];
  return [head("Vazifalar", liveTag()), seg, ...tasks.map((t) => {
    const [label, tone] = ST[t.status] || [t.status, ""];
    const barTone = t.status === "running" ? "run" : t.status === "failed" ? "bad" : t.status === "done" ? "" : "warn";
    const why = t.status !== "done" && t.status !== "running" && t.note ? t.note : "";
    return h("button", { class: "item", onclick: () => openTask(t.id), "aria-label": "Vazifa " + t.id },
      h("div", { class: "grow" }, h("h3", {}, t.request), h("div", { class: "bar " + barTone }, h("i")),
        h("p", {}, `${label} · ${usd(t.cost)} · ${ago(t.created_at)}`), why ? h("p", { class: "note" + (t.status === "failed" ? " red" : "") }, why) : null));
  })];
}
async function openTask(id) {
  const t = await api("/tasks/" + id);
  const [label] = ST[t.status] || [t.status];
  const files = t.files.map((f) => h("button", { class: "btn ghost", onclick: () => openFile(t.id, f) }, "📎 " + f));
  const act = (path, msg, method) => async () => {
    try { await api(path, { method: method || "POST" }); closeSheet(); toast(msg); refresh(true); } catch (e) { toast(e.message); }
  };
  const acts = t.status === "running"
    ? [h("button", { class: "btn red", onclick: act(`/tasks/${t.id}/stop`, "To'xtatilmoqda…") }, "⏹ To'xtatish")]
    : t.archived
      ? [h("button", { class: "btn", onclick: act(`/tasks/${t.id}/restore`, "Qaytarildi") }, "↩ Qaytarish"),
         h("button", { class: "btn red", onclick: () => { if (confirm("Vazifa va uning fayllari butunlay o'chiriladi. Davom etasizmi?")) act(`/tasks/${t.id}`, "O'chirildi", "DELETE")(); } }, "🗑 Butunlay o'chirish")]
      : [h("button", { class: "btn", onclick: act(`/tasks/${t.id}/archive`, "Arxivga olindi") }, "🗄 Arxivga olish")];
  const why = WHY[t.status] || "";
  openSheet(h("h2", {}, "Vazifa #" + t.id), h("p", { class: "muted" }, `${label} · ${usd(t.cost)}${t.archived ? " · arxivda" : ""}`),
    why || t.note ? h("p", { class: "note" + (t.status === "failed" ? " red" : "") }, [why, t.note].filter(Boolean).join(" ")) : null, h("p", {}, t.request),
    h("div", { class: "acts" }, acts),
    t.approvals && t.approvals.length ? h("div", { class: "label" }, "Ruxsat so'rovlari") : null,
    ...(t.approvals || []).map((a) => { const [txt, tone] = APPR[a.status] || [a.status, ""];
      return h("div", { class: "step" }, h("b", {}, a.agent + " · " + txt), h("div", {}, a.command.slice(0, 200))); }),
    h("div", { class: "label" }, "Natija"), h("div", { class: "pre" }, t.result || "(hali natija yo'q)"),
    files.length ? h("div", { class: "label" }, "Fayllar") : null, files.length ? h("div", { class: "pills" }, files) : null,
    h("div", { class: "label" }, "Jamoa ishi"),
    ...t.messages.filter((m) => !["qa", "hr"].includes(m.agent)).map((m) => h("div", { class: "step" }, h("b", {}, m.agent), h("div", {}, m.content.slice(0, 500)))));
}
async function openFile(id, path) {
  if (!TEXT_EXT.test(path)) return download(id, path);
  try {
    const r = await fetch("/api/tasks/" + id + "/files/" + path.split("/").map(encodeURIComponent).join("/"), { headers: { Authorization: "Bearer " + S.token } });
    if (!r.ok) throw new Error("Ochib bo'lmadi");
    const text = (await r.text()).slice(0, 80000);
    const copy = async () => {
      try { await navigator.clipboard.writeText(text); toast("Nusxalandi"); }
      catch { const ta = h("textarea", {}); ta.value = text; document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove(); toast("Nusxalandi"); }
    };
    openSheet(h("button", { class: "backlink", onclick: () => openTask(id) }, "‹ Vazifaga qaytish"), h("h2", {}, path),
      h("div", { class: "pre code" }, text),
      h("div", { class: "acts" }, h("button", { class: "btn lime", onclick: copy }, "Nusxalash"), h("button", { class: "btn", onclick: () => download(id, path) }, "Yuklab olish")));
  } catch (e) { toast(e.message); }
}
async function download(id, path) {
  try {
    const r = await fetch("/api/tasks/" + id + "/files/" + path.split("/").map(encodeURIComponent).join("/"), { headers: { Authorization: "Bearer " + S.token } });
    if (!r.ok) throw new Error("Yuklab bo'lmadi");
    const url = URL.createObjectURL(await r.blob());
    const a = h("a", { href: url, download: path.split("/").pop() }); document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  } catch (e) { toast(e.message); }
}

// --- Hisob ---
async function loadStats() {
  const [state, spend, mem, integ, loc] = await Promise.all([api("/state"), api("/spend"), api("/memory"), api("/integrations"), api("/location")]);
  S.state = state; renderTabs(); return { state, spend, mem, integ, loc };
}
const tick = (ok) => (ok ? "✓" : "✕");
function shareLocation() {
  if (!navigator.geolocation) return toast("Brauzer joylashuvni qo'llamaydi");
  navigator.geolocation.getCurrentPosition(
    async (p) => { try { await post("/location", { lat: p.coords.latitude, lon: p.coords.longitude }); toast("Joylashuv saqlandi"); refresh(true); } catch (e) { toast(e.message); } },
    () => toast("Joylashuv ruxsati yo'q (HTTPS kerak). Telegramdan yuborishingiz mumkin"), { enableHighAccuracy: true, timeout: 10000 });
}
function placeSheet(loc) {
  const addr = h("input", { placeholder: "Manzil yoki «lat,lon» (masalan: Chilonzor 9, Toshkent)", autocapitalize: "off" });
  const save = async (address) => {
    try { const r = await post("/place", { name: "home", address }); closeSheet(); toast(r.message); refresh(true); } catch (e) { toast(e.message); }
  };
  openSheet(h("h2", {}, "Uy manzili"), h("p", { class: "muted" }, loc.places.home ? "Hozir: " + loc.places.home.label : "Hali saqlanmagan"),
    h("label", {}, "Manzil"), addr, h("div", { class: "acts" },
      h("button", { class: "btn lime", onclick: () => addr.value.trim() ? save(addr.value.trim()) : toast("Manzilni yozing") }, "Saqlash"),
      h("button", { class: "btn", onclick: () => save("me") }, "Hozirgi joylashuvim")));
}
async function showModels() {
  toast("Tekshirilmoqda…");
  try {
    const data = await api("/models");
    $("toast").hidden = true;
    const blocks = data.map((p) => h("div", {}, h("div", { class: "label" }, (PROV[p.provider] || p.provider) + (p.enabled ? ` · ${p.count || 0} model` : " · kalit yo'q")),
      p.error ? h("p", { class: "note red" }, p.error) : null,
      ...p.tiers.map((t) => h("div", { class: "kv" }, h("span", {}, `${t.found ? "✓" : "✕"} ${t.tier}`),
        h("span", { class: "muted" }, t.found ? t.id : t.fixed ? `${t.id} → ${t.using} (avto-tuzatilgan)` : t.suggestion ? `${t.id} → taklif: ${t.suggestion}` : `${t.id} (topilmadi)`)))));
    openSheet(h("h2", {}, "Modellar"), h("p", { class: "muted" }, "models.yaml dagi nomlar provayderdagi haqiqiy modellarga mosligi. ✕ bo'lsa bot yaqin nomni o'zi tanlaydi, lekin models.yaml ni tuzatgan ma'qul."), ...blocks);
  } catch (e) { toast(e.message); }
}
async function showWidget() {
  try {
    const w = await api("/widget-link");
    const copy = async (t) => { try { await navigator.clipboard.writeText(t); toast("Nusxalandi"); } catch { toast("Nusxalab bo'lmadi (HTTPS kerak)"); } };
    openSheet(h("h2", {}, "iPhone vidjeti"),
      h("p", { class: "muted" }, "Bepul Scriptable ilovasiga " + w.script + " skriptini qo'ying, ichidagi ikkita qiymatni almashtiring. Vidjet ishlayotgan agentlar, kutayotgan qarorlar va byudjetni ko'rsatadi (faqat o'qiydi)."),
      h("div", { class: "label" }, "BASE_URL"), h("div", { class: "pre" }, w.base_url),
      h("button", { class: "btn ghost full", onclick: () => copy(w.base_url) }, "Nusxalash"),
      h("div", { class: "label" }, "WIDGET_TOKEN"), h("div", { class: "pre" }, w.configured ? w.token : "yaratilmagan: `python -m aicompany run` ni qayta ishga tushiring"),
      w.configured ? h("button", { class: "btn ghost full", onclick: () => copy(w.token) }, "Nusxalash") : null,
      h("p", { class: "hint" }, "Telefon ulana olishi uchun BASE_URL telefondan ochiladigan manzil bo'lishi kerak (WEB_HOST=0.0.0.0 yoki Tailscale/Tunnel)."));
  } catch (e) { toast(e.message); }
}
async function showReport() {
  try { const r = await api("/report"); openSheet(h("h2", {}, "Hisobot"), h("div", { class: "pre" }, r.text)); } catch (e) { toast(e.message); }
}
async function showAudit() {
  try {
    const rows = await api("/audit");
    openSheet(h("h2", {}, "Jurnal"), h("p", { class: "muted" }, "Agentlar va siz qilgan muhim amallar (so'nggi 60 ta)"),
      ...rows.map((r) => h("div", { class: "step" }, h("b", {}, `${r.actor} · ${r.action}`), h("div", {}, `${ago(r.ts)} oldin · ${r.detail}`))));
  } catch (e) { toast(e.message); }
}
function memoryAdd() {
  const ta = h("textarea", { rows: "3", placeholder: "Masalan: Men Toshkentda kofexona ochmoqchiman. Narxlarni so'mda yoz." });
  const btn = h("button", { class: "btn lime full" }, "Saqlash");
  btn.addEventListener("click", async () => {
    if (!ta.value.trim()) return toast("Matn yozing");
    try { await post("/memory", { text: ta.value }); closeSheet(); toast("Xotiraga saqlandi"); refresh(true); } catch (e) { toast(e.message); }
  });
  openSheet(h("h2", {}, "Xotiraga qo'shish"), h("p", { class: "muted" }, "Agentlar bu ma'lumotni keyingi vazifalarda eslab qoladi."), h("div", { class: "label" }), ta, h("div", { class: "label" }), btn);
}
function drawStats({ state, spend, mem, integ, loc }) {
  const on = state.budgets.filter((b) => b.enabled);
  const left = on.reduce((a, b) => a + b.budget - b.spent, 0);
  const provs = on.map((b) => {
    const pct = b.budget ? Math.min(100, (b.spent / b.budget) * 100) : 0;
    const fill = h("i"); fill.style.width = pct + "%";
    return h("div", { class: "prov" }, h("div", { class: "row" }, h("span", {}, PROV[b.provider] || b.provider), h("small", {}, `${usd(b.spent)} / ${usd(b.budget)}`)),
      h("div", { class: "bar " + (pct > 90 ? "bad" : pct > 70 ? "warn" : "") }, fill));
  });
  // Tanlov doim ko'rinadi; kaliti yo'q AI xira va bosilganda tushuntiradi
  const known = ["anthropic", "gemini"];
  const have = new Set(state.providers);
  const setPrimary = async (v) => { try { await post("/provider", { primary: v }); toast("Asosiy AI: " + PROV[v]); refresh(true); } catch (e) { toast(e.message); } };
  const opt = (c) => {
    const enabled = c === "auto" || have.has(c), active = (state.primary || "auto") === c;
    return h("button", { class: (active ? "on " : "") + (enabled ? "" : "off"), onclick: () => (enabled ? setPrimary(c) : toast(PROV[c] + " kaliti yo'q: .env ga qo'shing")) }, PROV[c] + (enabled ? "" : " ✕"));
  };
  const connected = known.filter((k) => have.has(k)).map((k) => PROV[k]);
  const primary = h("div", {}, h("div", { class: "label" }, "Asosiy AI"),
    h("div", { class: "seg" }, ["auto", ...known].map(opt)),
    h("p", { class: "hint" }, (connected.length ? "Ulangan: " + connected.join(", ") + ". " : "Hech qanday AI ulanmagan. ") +
      (connected.length < known.length ? "Ikkinchisini qo'shish uchun .env ga kalit qo'ying. " : "") + "Avto = eng arzonidan boshlaydi; tanlangani birinchi, boshqasi zaxira."));
  const ta = integ.telegram_account;
  const links = h("div", { class: "card" },
    ...[["Telegram bot", integ.telegram_bot, ""],
        ["Telegram akkaunt", ta.configured, ta.configured ? (ta.mode === "write" ? "o'qish + yuborish (tasdiq bilan)" : "faqat o'qish") : "ulash: python -m aicompany tglogin"],
        ["Xarita", true, integ.maps === "google" ? "Google (tirbandlik bilan)" : "OpenStreetMap (bepul, tirbandliksiz)"],
        ["Ovozni tushunish", integ.voice, integ.voice ? "Gemini" : "GEMINI_API_KEY kerak"],
        ["Veb-qidiruv", true, integ.search === "brave" ? "Brave" : "DuckDuckGo"],
        ["Maxfiy chat uchun AI", ta.private_providers.length > 0, ta.private_providers.length ? ta.private_providers.join(", ") : "cheklanmagan (PRIVATE_PROVIDERS)"],
       ].map(([name, ok, note]) => h("div", { class: "kv" }, h("span", {}, `${tick(ok)} ${name}`), h("span", { class: "muted" }, note))));
  const L = integ.limits;
  const limits = h("div", { class: "card" }, ...[["Bitta vazifa limiti", usd(L.task_usd) + " (MAX_TASK_USD)"], ["Jamoa hajmi", L.agents + " xodimgacha"],
    ["Bir vaqtda vazifa", L.parallel], ["QA qayta ishlash", L.revisions + " marta"], ["Agent asbob chaqiruvi", L.tool_turns + " ta"],
    ["Buyruq vaqti", L.command_s + " s"], ["Telegram yuborish", L.tg_sends_per_hour + " ta/soat"], ["Kunlik hisobot", L.report]]
    .map(([k, v]) => h("div", { class: "kv" }, h("span", {}, k), h("span", { class: "muted" }, String(v)))),
    h("p", { class: "hint" }, "O'zgartirish: .env faylida (kalitlar va limitlar xavfsizlik uchun panelda tahrirlanmaydi)."));
  const home = loc.places.home;
  const where = h("div", { class: "card" },
    h("div", { class: "kv" }, h("span", {}, "📍 Joylashuv"), h("span", { class: "muted" }, loc.last ? `${loc.last.age}${loc.last.live ? " (jonli)" : ""}` : "yuborilmagan")),
    h("div", { class: "kv" }, h("span", {}, "🏠 Uy"), h("span", { class: "muted" }, home ? home.label : "saqlanmagan")),
    h("div", { class: "acts" }, h("button", { class: "btn", onclick: shareLocation }, "📍 Joylashuvimni yuborish"), h("button", { class: "btn", onclick: () => placeSheet(loc) }, "🏠 Uyni belgilash")));
  const pause = h("button", { class: "btn full " + (state.paused ? "lime" : "red"), onclick: async () => { await post(state.paused ? "/resume" : "/pause"); refresh(true); } },
    state.paused ? "▶ Davom ettirish" : "⏸ Hammasini to'xtatish");
  const delMem = async (id) => { try { await api("/memory/" + id, { method: "DELETE" }); toast("O'chirildi"); refresh(true); } catch (e) { toast(e.message); } };
  return [head("Hisob", liveTag()),
    h("div", { class: "money" }, h("b", {}, usd(left)), h("span", {}, `qolgan byudjet · bugun ${usd(state.today)}`)), ...provs,
    primary,
    h("div", { class: "label" }, "Ulanishlar"), links,
    h("div", { class: "label" }, "Limitlar"), limits,
    h("div", { class: "label" }, "Joylashuv"), where,
    spend.length ? h("div", { class: "label" }, "Agentlar sarfi") : null,
    ...spend.slice(0, 6).map((r) => h("div", { class: "item" }, h("div", { class: "grow" }, h("h3", {}, r.agent)), h("span", { class: "muted" }, usd(r.cost)))),
    h("div", { class: "label" }, "Xotira"),
    ...mem.slice(0, 8).map((m) => h("div", { class: "item" }, h("div", { class: "grow" }, h("p", {}, m.text)),
      h("button", { class: "btn red sm", onclick: () => delMem(m.id), "aria-label": "O'chirish" }, "✕"))),
    mem.length ? null : h("p", { class: "hint" }, "Hozircha bo'sh."),
    h("button", { class: "btn ghost full", onclick: memoryAdd }, "+ Xotiraga qo'shish"),
    h("div", { class: "label" }, "Boshqaruv"),
    h("div", { class: "acts" }, h("button", { class: "btn", onclick: showReport }, "📊 Hisobot"), h("button", { class: "btn", onclick: showAudit }, "🧾 Jurnal")),
    h("div", { class: "acts" }, h("button", { class: "btn", onclick: showModels }, "🔎 Modellarni tekshirish"), h("button", { class: "btn", onclick: showWidget }, "📱 iPhone vidjeti")),
    pause, h("div", { class: "label" }),
    h("button", { class: "btn ghost full", onclick: () => { localStorage.removeItem("aij_token"); S.token = ""; location.reload(); } }, "Chiqish"),
    h("p", { class: "hint ver" }, "Versiya: " + (state.version || "?") + " · panel " + PANEL_V)];
}

// ---------- Chat (to'liq ekran) ----------
function parseResult(m) {
  try { return JSON.parse(m.text); } catch { /* eski, kesilgan yozuvlar uchun */ }
  const id = m.text.match(/"task_id":\s*(\d+)/), st = m.text.match(/"status":\s*"(\w+)"/);
  const body = m.text.match(/"text":\s*"((?:[^"\\]|\\.)*)/);
  return { task_id: id ? +id[1] : null, status: st ? st[1] : "", text: body ? body[1].replace(/\\n/g, "\n").replace(/\\"/g, '"').slice(0, 500) : "", files: [] };
}
function msgEl(m) {
  if (m.role === "result") {
    const p = parseResult(m);
    const [label] = ST[p.status] || [p.status || ""];
    const long = (p.text || "").length > 300;
    const files = (p.files || []).slice(0, 4).map((f) => h("button", { class: "btn ghost", onclick: () => openFile(p.task_id, f) }, "📎 " + f.split("/").pop()));
    return h("div", { class: "msg result" }, h("b", {}, p.task_id ? `Vazifa #${p.task_id} · ${label}` : "Vazifa natijasi"),
      h("div", { class: "t" + (long ? " fade" : "") }, p.text || "(natija yo'q)"),
      files.length ? h("div", { class: "files" }, files, (p.files || []).length > 4 ? h("span", { class: "muted" }, "+" + (p.files.length - 4)) : null) : null,
      p.task_id ? h("button", { class: "btn lime", onclick: () => openTask(p.task_id) }, "To'liq ko'rish") : null);
  }
  if (m.role === "proposal") {
    let p = {}; try { p = JSON.parse(m.text); } catch { p = { task: m.text }; }
    const btn = h("button", { class: "btn lime" }, "Vazifa qilib topshirish");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try { await post("/tasks", { text: p.task }); btn.textContent = "Topshirildi ✓"; toast("Vazifa topshirildi"); }
      catch (e) { toast(e.message); btn.disabled = false; }
    });
    return h("div", { class: "msg proposal" }, h("div", { class: "q" }, "Taklif qilingan vazifa"), h("div", {}, p.task), btn);
  }
  return h("div", { class: "msg " + m.role }, m.text);
}
async function pollChat() {
  const rows = await api("/chat?after=" + S.lastChat);
  if (!rows.length) return;
  S.lastChat = rows[rows.length - 1].id; S.chat.push(...rows);
  S.typing = false;
  if (S.chatOpen) {
    const box = $("msgs"), near = box.scrollHeight - box.scrollTop - box.clientHeight < 140;
    box.querySelectorAll(".typing").forEach((n) => n.remove());
    rows.forEach((m) => box.append(msgEl(m)));
    if (near) box.scrollTop = box.scrollHeight;
  }
}
function openChat(focus) {
  S.chatOpen = true; $("chat").hidden = false;
  const box = $("msgs"); box.replaceChildren();
  if (!S.chat.length) box.append(h("div", { class: "empty" }, "Rahbar bilan oddiy suhbat: savol bering, maslahatlashing. Ish topshirish uchun «+ Vazifa» tugmasi."));
  S.chat.forEach((m) => box.append(msgEl(m)));
  if (S.typing) box.append(h("div", { class: "typing" }, "Rahbar o'ylayapti…"));
  box.scrollTop = box.scrollHeight;
  if (focus) $("chat-input").focus();
}
function closeChat() { S.chatOpen = false; $("chat").hidden = true; refresh(true); }
async function sendChat() {
  const ta = $("chat-input"), text = ta.value.trim(); if (!text) return;
  ta.value = ""; ta.style.height = "auto";
  try {
    await post("/chat", { text }); S.typing = true;
    const box = $("msgs"); box.querySelector(".empty")?.remove(); box.append(h("div", { class: "typing" }, "Rahbar o'ylayapti…")); box.scrollTop = box.scrollHeight;
    await pollChat();
  } catch (e) { toast(e.message); ta.value = text; }
}
let rec = null;
function micState(on) { const b = $("chat-mic"); b.classList.toggle("rec", on); b.setAttribute("aria-label", on ? "To'xtatish" : "Ovozli xabar"); }
async function toggleMic() {
  if (rec) { rec.stop(); return; }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) return toast("Mikrofon uchun HTTPS kerak (Tailscale yoki Tunnel)");
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch { return toast("Mikrofonga ruxsat berilmadi"); }
  const mime = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"].find((m) => MediaRecorder.isTypeSupported(m)) || "";
  const chunks = [];
  rec = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
  rec.ondataavailable = (e) => chunks.push(e.data);
  rec.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    const type = (rec && rec.mimeType) || mime || "audio/webm"; rec = null; micState(false);
    const btn = $("chat-mic"); btn.disabled = true; toast("Matnga aylantirilmoqda…");
    try {
      const r = await fetch("/api/voice", { method: "POST", headers: { Authorization: "Bearer " + S.token, "Content-Type": type }, body: new Blob(chunks, { type }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || "Xatolik " + r.status);
      const ta = $("chat-input"); ta.value = (ta.value ? ta.value + " " : "") + d.text; ta.dispatchEvent(new Event("input")); ta.focus();
    } catch (e) { toast(e.message); } finally { btn.disabled = false; }
  };
  rec.start(); micState(true);
}
$("chat-mic").append(svg(IC.mic));
$("chat-mic").addEventListener("click", toggleMic);
$("chat-back").addEventListener("click", closeChat);
$("chat-task").addEventListener("click", taskSheet);
$("chat-clear").addEventListener("click", async () => {
  if (!confirm("Suhbat tarixi tozalansinmi? (Vazifalar va natijalar o'chmaydi)")) return;
  try { await post("/chat/clear"); S.chat = []; S.lastChat = 0; openChat(false); toast("Suhbat tozalandi"); } catch (e) { toast(e.message); }
});
$("chat-send").append(svg(IC.send));
$("chat-send").addEventListener("click", sendChat);
$("chat-input").addEventListener("input", (e) => { const t = e.target; t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 140) + "px"; });
$("chat-input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !matchMedia("(pointer: coarse)").matches) { e.preventDefault(); sendChat(); } });

// ---------- so'rov va ishga tushirish ----------
async function poll() {
  try {
    await pollChat();
    if (S.chatOpen) { S.state = await api("/state"); $("chat-sub").textContent = S.state.paused ? "pauza" : S.state.working.length ? S.state.working.map((w) => w.agent).join(", ") + " ishlayapti" : "onlayn"; if (!S.state.working.length && !S.state.running_tasks.length) document.querySelectorAll(".typing").forEach((n) => n.remove()); }
    else await refresh(false);
  } catch (e) { if (e.message !== "auth") toast("Aloqa yo'q…"); }
}
function start() {
  renderTabs(); poll().then(() => refresh(true));
  setInterval(poll, 3000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
}
window.addEventListener("hashchange", () => location.reload());
(async function init() {
  const m = location.hash.match(/token=([^&]+)/);
  if (m) { history.replaceState(null, "", location.pathname); S.token = decodeURIComponent(m[1]); }
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  if (S.token && (await tryLogin(S.token))) start(); else showLogin();
})();
