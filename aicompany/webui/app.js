"use strict";
// AI Jamoa boshqaruv paneli. innerHTML faqat ishonchli o'zgarmas ikonkalar uchun; foydalanuvchi/agent matni doim textContent.
const $ = (id) => document.getElementById(id);
const ICONS = {
  chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.3A8 8 0 1 1 21 12z"/></svg>',
  team: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3.2"/><path d="M3 20a6 6 0 0 1 12 0"/><circle cx="17.5" cy="9" r="2.4"/><path d="M16.5 14.2A5 5 0 0 1 21 19"/></svg>',
  cards: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="5" width="16" height="14" rx="3"/><path d="M8 12l2.6 2.6L16 9.4"/></svg>',
  tasks: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6h11M9 12h11M9 18h11"/><path d="M4 6h.01M4 12h.01M4 18h.01"/></svg>',
  stats: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>',
  send: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3.4 20.4 21 12 3.4 3.6l.1 6.5 10.9 1.9-10.9 1.9z"/></svg>',
};
const TABS = [["chat", "Suhbat"], ["team", "Jamoa"], ["cards", "Kartalar"], ["tasks", "Vazifalar"], ["stats", "Hisob"]];
const STATUS = { done: ["Tayyor", "lime"], running: ["Ishlayapti", "blue"], failed: ["Xato", "red"], stopped: ["To'xtatildi", "amber"],
  paused: ["Pauza", "amber"], interrupted: ["Uzildi", "amber"] };

const S = { token: localStorage.getItem("aij_token") || "", tab: "chat", state: null, lastChat: 0, chat: [], timers: [], busyChat: false };

// ---------- yordamchilar ----------
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}
const svg = (html) => { const s = document.createElement("span"); s.innerHTML = html; return s.firstChild; };
const usd = (n) => "$" + Number(n || 0).toFixed(n >= 10 ? 1 : 2);
function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), 2600);
}
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
  if (s < 60) return "hozir";
  if (s < 3600) return Math.floor(s / 60) + " daq oldin";
  if (s < 86400) return Math.floor(s / 3600) + " soat oldin";
  return Math.floor(s / 86400) + " kun oldin";
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
  if (!(await tryLogin($("token").value.trim()))) $("login-err").textContent = "Kalit noto'g'ri";
  else start();
});

// ---------- umumiy qobiq ----------
function renderTabs() {
  const nav = $("tabs"); nav.replaceChildren();
  for (const [id, label] of TABS) {
    const b = h("button", { class: "tab" + (S.tab === id ? " on" : ""), onclick: () => go(id), "aria-label": label }, svg(ICONS[id]), label);
    if (id === "cards" && S.state && S.state.pending) b.append(h("span", { class: "badge" }, String(S.state.pending)));
    nav.append(b);
  }
}
function renderTop() {
  const st = S.state; if (!st) return;
  const busy = st.working.length > 0;
  $("orb").className = "orb" + (st.paused ? " idle" : busy ? " live" : "");
  $("sub").textContent = st.paused ? "⏸ To'xtatilgan" : busy ? st.working.map((w) => w.agent).join(", ") + " ishlayapti" : "Hammasi tinch";
  const left = st.budgets.filter((b) => b.enabled).reduce((a, b) => a + b.budget - b.spent, 0);
  const sp = $("spend"); sp.replaceChildren(h("b", {}, usd(st.today)), "bugun · qoldi " + usd(left));
}
function go(tab) { S.tab = tab; renderTabs(); render(); }
async function render() {
  const view = $("view"); view.className = S.tab === "chat" ? "chat" : "";
  const fn = { chat: vChat, team: vTeam, cards: vCards, tasks: vTasks, stats: vStats }[S.tab];
  try { await fn(view); } catch (e) { if (e.message !== "auth") view.replaceChildren(h("div", { class: "empty" }, e.message)); }
}

// ---------- sheet ----------
function openSheet(...kids) {
  const b = $("sheet-body"); b.replaceChildren(...kids.filter((k) => k != null && k !== false)); $("sheet").hidden = false;
}
const closeSheet = () => { $("sheet").hidden = true; };
$("sheet-bg").addEventListener("click", closeSheet);

// ---------- Suhbat ----------
function msgEl(m) {
  if (m.role === "result") {
    let p = {}; try { p = JSON.parse(m.text); } catch { p = { text: m.text }; }
    const [label, color] = STATUS[p.status] || [p.status || "", ""];
    const long = (p.text || "").length > 500;
    return h("div", { class: "msg result" },
      h("div", { class: "row sb" }, h("b", {}, "Vazifa #" + p.task_id), h("span", { class: "chip " + color }, label)),
      h("div", { class: "res-text" + (long ? " fade" : "") }, p.text || "(natija yo'q)"),
      h("div", { class: "chips" }, h("button", { class: "btn sm primary", onclick: () => openTask(p.task_id) }, "To'liq ko'rish")));
  }
  return h("div", { class: "msg " + m.role }, m.text);
}
async function pollChat() {
  const rows = await api("/chat?after=" + S.lastChat);
  if (!rows.length) return false;
  S.lastChat = rows[rows.length - 1].id; S.chat.push(...rows);
  const box = document.querySelector(".msgs");
  if (box) {
    const near = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
    document.querySelectorAll(".typing").forEach((n) => n.remove());
    rows.forEach((m) => box.append(msgEl(m)));
    if (near) box.scrollTop = box.scrollHeight;
  }
  return true;
}
async function vChat(view) {
  const box = h("div", { class: "msgs" });
  const ta = h("textarea", { rows: "1", placeholder: "Rahbarga yozing…", enterkeyhint: "send" });
  const send = async () => {
    const text = ta.value.trim(); if (!text) return;
    ta.value = ""; ta.style.height = "auto";
    try { await post("/chat", { text }); S.busyChat = true; box.append(h("div", { class: "typing" }, "Rahbar o'ylayapti…")); box.scrollTop = box.scrollHeight; await pollChat(); }
    catch (e) { toast(e.message); ta.value = text; }
  };
  ta.addEventListener("input", () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 140) + "px"; });
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !matchMedia("(pointer: coarse)").matches) { e.preventDefault(); send(); } });
  view.replaceChildren(box, h("div", { class: "composer" }, ta, h("button", { class: "send", onclick: send, "aria-label": "Yuborish" }, svg(ICONS.send))));
  if (!S.chat.length) { S.lastChat = 0; await pollChat(); } else S.chat.forEach((m) => box.append(msgEl(m)));
  if (!S.chat.length) box.append(h("div", { class: "empty" }, "Salom! Menga oddiy yozing: gaplashing, savol bering yoki vazifa topshiring."));
  box.scrollTop = box.scrollHeight;
}

// ---------- Jamoa ----------
const initials = (n) => n.split("_").map((p) => p[0]).join("").slice(0, 2).toUpperCase();
async function vTeam(view) {
  const team = await api("/team");
  const av = h("div", { class: "avatars" }, team.map((a) => h("div", { class: "av" + (a.busy ? " busy" : "") }, h("i", {}, initials(a.name)), a.name)));
  const cards = team.map((a) => h("div", { class: "card agent" },
    h("div", { class: "row sb" }, h("div", { class: "row" }, h("span", { class: "dot" + (a.busy ? " on" : "") }), h("h3", {}, a.name)),
      h("span", { class: "chip " + (a.tier === "strong" ? "amber" : a.tier === "mid" ? "blue" : "lime") }, a.tier)),
    h("p", { class: "role" }, a.role),
    h("div", { class: "chips" }, a.busy ? h("span", { class: "chip lime" }, "ishlayapti · #" + a.task_id) : null,
      a.tools.map((t) => h("span", { class: "chip" }, t)), h("span", { class: "chip" }, a.steps + " ish"), h("span", { class: "chip" }, usd(a.cost))),
    a.core ? null : h("div", { class: "actions" }, h("button", { class: "btn sm danger", onclick: () => fire(a.name) }, "Ishdan bo'shatish"))));
  view.replaceChildren(h("div", { class: "sect" }, "Hozir ishda"), av, ...cards,
    h("button", { class: "fab", onclick: hireSheet }, "+ Xodim yollash"));
}
async function fire(name) {
  if (!confirm(name + " ishdan bo'shatilsinmi?")) return;
  try { await post("/team/fire", { name }); toast("Bo'shatildi"); render(); } catch (e) { toast(e.message); }
}
function hireSheet() {
  const name = h("input", { placeholder: "masalan: seo_mutaxassis", autocapitalize: "off" });
  const why = h("textarea", { rows: "3", placeholder: "Nima uchun kerak, qanday ish qiladi?" });
  const btn = h("button", { class: "btn primary" }, "Yollash");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try { const r = await post("/team/hire", { name: name.value, why: why.value }); closeSheet(); toast("Yollandi: " + r.name); render(); }
    catch (e) { toast(e.message); btn.disabled = false; }
  });
  openSheet(h("h2", {}, "Yangi xodim"), h("label", {}, "Nomi"), name, h("label", {}, "Vazifasi"), why, h("div", { class: "sect" }), btn);
}

// ---------- Kartalar (ruxsatlar) ----------
async function vCards(view) {
  const items = await api("/approvals");
  if (!items.length) { view.replaceChildren(h("div", { class: "empty" }, "Hozircha qaror kutayotgan narsa yo'q ✓")); return; }
  view.replaceChildren(h("div", { class: "sect" }, "Sizning qaroringiz kerak"), ...items.map((a) => {
    const decide = async (ok) => { try { await post("/approvals/" + a.id, { approve: ok }); toast(ok ? "Ruxsat berildi" : "Rad etildi"); poll(); render(); } catch (e) { toast(e.message); render(); } };
    return h("div", { class: "card white appr" },
      h("div", { class: "row sb" }, h("span", { class: "muted" }, a.agent + " · vazifa #" + a.task_id), h("span", { class: "chip red" }, "buyruq")),
      h("h3", {}, "Terminal buyrug'ini bajarishga ruxsat?"), h("pre", {}, a.description),
      h("div", { class: "btns" }, h("button", { class: "btn", onclick: () => decide(false) }, "✕ Rad etish"), h("button", { class: "btn yes", onclick: () => decide(true) }, "✓ Ruxsat")));
  }));
}

// ---------- Vazifalar ----------
async function vTasks(view) {
  const tasks = await api("/tasks");
  if (!tasks.length) { view.replaceChildren(h("div", { class: "empty" }, "Hali vazifa yo'q")); return; }
  view.replaceChildren(...tasks.map((t) => {
    const [label, color] = STATUS[t.status] || [t.status, ""];
    return h("button", { class: "card task", onclick: () => openTask(t.id), "aria-label": "Vazifa " + t.id, "data-id": t.id },
      h("div", { class: "row sb" }, h("span", { class: "muted" }, "#" + t.id + " · " + ago(t.created_at)), h("span", { class: "chip " + color }, label)),
      h("p", { class: "req" }, t.request), h("p", { class: "muted" }, usd(t.cost)));
  }));
}
async function openTask(id) {
  const t = await api("/tasks/" + id);
  const [label, color] = STATUS[t.status] || [t.status, ""];
  const files = t.files.map((f) => h("button", { class: "btn sm", onclick: () => download(t.id, f) }, "📎 " + f));
  openSheet(h("div", { class: "row sb" }, h("h2", {}, "Vazifa #" + t.id), h("span", { class: "chip " + color }, label)),
    h("p", { class: "muted" }, t.request), h("div", { class: "sect" }, "Natija · " + usd(t.cost)),
    h("div", { class: "pre" }, t.result || "(hali natija yo'q)"),
    files.length ? h("div", { class: "sect" }, "Fayllar") : null, h("div", { class: "chips" }, files),
    h("div", { class: "sect" }, "Jamoa ishi"),
    ...t.messages.filter((m) => !["qa", "hr"].includes(m.agent)).map((m) => h("div", { class: "step" }, h("b", {}, m.agent), h("div", { class: "muted" }, m.content.slice(0, 600)))));
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

// ---------- Hisob ----------
async function vStats(view) {
  const [st, spend, mem] = await Promise.all([api("/state"), api("/spend"), api("/memory")]);
  S.state = st; renderTop();
  const budgets = st.budgets.map((b) => {
    const pct = b.budget ? Math.min(100, (b.spent / b.budget) * 100) : 0;
    const fill = h("i", { class: pct > 90 ? "crit" : pct > 70 ? "warn" : "" }); fill.style.width = pct + "%";
    return h("div", { class: "card" }, h("div", { class: "row sb" }, h("b", {}, b.provider), h("span", { class: "chip " + (b.enabled ? "lime" : "") }, b.enabled ? "ulangan" : "kalit yo'q")),
      h("div", { class: "bar" }, fill), h("div", { class: "row sb muted" }, h("span", {}, usd(b.spent) + " sarflandi"), h("span", {}, "limit " + usd(b.budget))));
  });
  const pause = h("button", { class: "btn toggle " + (st.paused ? "primary" : "danger"), onclick: async () => { await post(st.paused ? "/resume" : "/pause"); poll(); render(); } },
    st.paused ? "▶ Davom ettirish" : "⏸ Hammasini to'xtatish");
  view.replaceChildren(h("div", { class: "sect" }, "Byudjet"), ...budgets,
    h("div", { class: "sect" }, "Agentlar bo'yicha sarf"), h("div", { class: "card" }, spend.length ? spend.map((r) => h("div", { class: "kv" }, h("span", {}, r.agent), h("b", {}, usd(r.cost)))) : h("p", { class: "muted" }, "Hali sarf yo'q")),
    h("div", { class: "sect" }, "Xotira"), h("div", { class: "card" }, mem.length ? mem.map((m) => h("div", { class: "kv" }, h("span", {}, m.text))) : h("p", { class: "muted" }, "Hozircha bo'sh")),
    h("div", { class: "sect" }, "Boshqaruv"), pause,
    h("div", { class: "sect" }), h("button", { class: "btn toggle", onclick: () => { localStorage.removeItem("aij_token"); S.token = ""; location.reload(); } }, "Chiqish"));
}

// ---------- so'rov va ishga tushirish ----------
async function poll() {
  try {
    S.state = await api("/state"); renderTop(); renderTabs();
    if (S.tab === "chat") { const got = await pollChat(); if (got && !S.state.working.length && !S.state.running_tasks.length) document.querySelectorAll(".typing").forEach((n) => n.remove()); }
    else if (S.tab === "cards" || S.tab === "team") render();
  } catch (e) { if (e.message !== "auth") $("sub").textContent = "Aloqa yo'q…"; }
}
function start() {
  renderTabs(); poll().then(render);
  S.timers.forEach(clearInterval); S.timers = [setInterval(poll, 3000)];
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
}
window.addEventListener("hashchange", () => location.reload());
(async function init() {
  const m = location.hash.match(/token=([^&]+)/);
  if (m) { history.replaceState(null, "", location.pathname); S.token = decodeURIComponent(m[1]); }
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  if (S.token && (await tryLogin(S.token))) start(); else showLogin();
})();
