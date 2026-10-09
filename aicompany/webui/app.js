"use strict";
// AI Jamoa paneli. innerHTML faqat o'zgarmas ikonkalar uchun (svg yordamchisi); agent/foydalanuvchi matni doim textContent.
const $ = (id) => document.getElementById(id);
const IC = {
  team: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3.2"/><path d="M3 20a6 6 0 0 1 12 0"/><circle cx="17.5" cy="9" r="2.4"/><path d="M16.5 14.2A5 5 0 0 1 21 19"/></svg>',
  cards: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="5" width="16" height="14" rx="3.5"/><path d="M8.5 12l2.5 2.5 4.5-5"/></svg>',
  tasks: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/></svg>',
  plans: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="5" width="16" height="15" rx="3"/><path d="M8 3v4M16 3v4M4 10h16M9 15l2 2 4-4"/></svg>',
  stats: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 20V11M12 20V4M19 20v-6"/></svg>',
  mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>',
  send: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3.4 20.4 21 12 3.4 3.6l.1 6.5 10.9 1.9-10.9 1.9z"/></svg>',
};
const PANEL_V = "2026.10.09-zg";
const PROV = { anthropic: "Claude", gemini: "Gemini", openai: "ChatGPT", auto: "Avto" };
const TABS = [["team", "Jamoa"], ["cards", "Kartalar"], ["tasks", "Vazifalar"], ["plans", "Rejalar"], ["stats", "Hisob"]];
const ST = { done: ["Tayyor", ""], running: ["Ishlayapti", "on"], failed: ["Xato", "red"], cancelled: ["Siz to'xtatdingiz", "amber"],
  limit: ["Limit tugadi", "amber"], paused: ["Pauza", "amber"], interrupted: ["Uzildi (dastur qayta yoqilgan)", "amber"], stopped: ["To'xtatilgan", "amber"] };
const CAN_RESUME = new Set(["interrupted", "paused", "cancelled", "limit", "failed", "stopped"]);  // to'xtagan: «Davom ettirish» tugmasi
const WHY = { done: "", running: "", failed: "Vazifa xato bilan tugadi. «Davom ettirish» bilan qolgan qismini tugatishingiz mumkin.", cancelled: "Siz uni qo'lda to'xtatdingiz. Bajarilgan qismi saqlangan.",
  limit: "Bitta vazifa uchun ajratilgan pul limiti tugadi. .env dagi MAX_TASK_USD ni oshirishingiz mumkin.", paused: "Hammasi pauzaga qo'yilgan edi.",
  interrupted: "Dastur qayta ishga tushganda vazifa o'rtada uzilgan. «Davom ettirish» bilan bajarilgan qismidan davom etadi.", stopped: "" };
const APPR = { approved: ["✓ Ruxsat berdingiz", ""], denied: ["✕ Siz rad etdingiz", "red"], expired: ["⏱ Javob bermadingiz (muddat tugadi)", "amber"], pending: ["Javob kutilmoqda", ""] };
const AGENT_UZ = { ceo: "Rahbar", hr: "HR", qa: "Sifat nazorati", developer: "Dasturchi", marketer: "Marketolog",
  researcher: "Tahlilchi", generalist: "Universal xodim", assistant: "Yordamchi", architect: "Arxitektor", fact_checker: "Fakt-tekshiruvchi", finance_analyst: "Moliya tahlilchisi" };
const agentName = (a) => AGENT_UZ[a] || a.replace(/_/g, " ");

// --- Markdown -> xavfsiz DOM (innerHTML yo'q: matn faqat textNode sifatida) ---
function inline(s) {
  const out = [], re = /(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\[[^\]]+\]\((https?:\/\/[^)\s]+)\)|\*[^*\s][^*]*\*)/g;
  let last = 0, m;
  while ((m = re.exec(s))) {
    if (m.index > last) out.push(s.slice(last, m.index));
    const t = m[0];
    if (t.startsWith("**") || t.startsWith("__")) out.push(h("b", {}, t.slice(2, -2)));
    else if (t.startsWith("`")) out.push(h("code", {}, t.slice(1, -1)));
    else if (t.startsWith("[")) out.push(h("a", { href: m[2], target: "_blank", rel: "noopener noreferrer" }, t.slice(1, t.indexOf("]"))));
    else out.push(h("i", {}, t.slice(1, -1)));
    last = re.lastIndex;
  }
  if (last < s.length) out.push(s.slice(last));
  return out;
}
const LIST_RE = /^\s*([-*+•]|\d+[.)])\s+/, BLOCK_RE = /^(```|#{1,6}\s|\s*([-*+•]|\d+[.)])\s+|\s*\||>|(-{3,}|\*{3,})\s*$)/;
function md(src) {
  const root = h("div", { class: "md" });
  const lines = String(src || "").replace(/\r/g, "").split("\n");
  let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (/^```/.test(l)) {
      const buf = []; i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i++; root.append(h("pre", { class: "pre code" }, buf.join("\n"))); continue;
    }
    if (/^#{1,6}\s/.test(l)) { root.append(h(l.match(/^#+/)[0].length <= 2 ? "h3" : "h4", {}, inline(l.replace(/^#+\s*/, "")))); i++; continue; }
    if (/^(-{3,}|\*{3,})\s*$/.test(l)) { root.append(h("hr")); i++; continue; }
    if (LIST_RE.test(l)) {
      const list = h(/^\s*\d/.test(l) ? "ol" : "ul");
      while (i < lines.length && LIST_RE.test(lines[i])) {
        const item = lines[i++].replace(LIST_RE, "").replace(/^\[([ xX])\]\s*/, (_, x) => (x === " " ? "☐ " : "☑ "));
        list.append(h("li", {}, inline(item)));
      }
      root.append(list); continue;
    }
    if (/^\s*\|/.test(l)) {
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
      const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const body = rows.filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r)).map(cells);
      const [head, ...rest] = body;
      root.append(h("div", { class: "tbl" }, h("table", {}, h("thead", {}, h("tr", {}, (head || []).map((c) => h("th", {}, inline(c))))),
        h("tbody", {}, rest.map((r) => h("tr", {}, r.map((c) => h("td", {}, inline(c)))))))));
      continue;
    }
    if (/^>\s?/.test(l)) {
      const buf = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^>\s?/, ""));
      root.append(h("blockquote", {}, inline(buf.join(" ")))); continue;
    }
    if (!l.trim()) { i++; continue; }
    const buf = [l]; i++;
    while (i < lines.length && lines[i].trim() && !BLOCK_RE.test(lines[i])) buf.push(lines[i++]);
    const p = h("p");
    buf.forEach((b, k) => { if (k) p.append(h("br")); p.append(...inline(b).map((x) => (x.nodeType ? x : document.createTextNode(x)))); });
    root.append(p);
  }
  return root;
}
function tryJson(text) {
  const s = String(text || "").trim().replace(/^```(?:json)?\s*|```$/g, "").trim();
  if (!s.startsWith("{")) return null;
  try { return JSON.parse(s); } catch { return null; }
}
// Jamoa a'zosining ishini odam tushunadigan ko'rinishga keltiradi (JSON reja, QA hukmi, markdown)
function readable(m) {
  const j = tryJson(m.content);
  if (j && Array.isArray(j.steps)) {
    return h("div", { class: "md" }, j.summary ? h("p", {}, "📋 " + j.summary) : null,
      h("ol", {}, j.steps.map((s) => h("li", {}, h("b", {}, agentName(String(s.agent || "?")) + ": "), String(s.task || "")))));
  }
  if (j && "verdict" in j) {
    const issues = (j.issues || []).map(String);
    return h("div", { class: "md" }, h("p", {}, j.verdict === "pass" ? "✅ Tekshiruvdan o'tdi" : "⚠️ Kamchiliklar topildi:"),
      issues.length ? h("ul", {}, issues.map((x) => h("li", {}, x))) : null);
  }
  if (j && j.role) return h("div", { class: "md" }, h("p", {}, "🧑‍💼 Yangi lavozim: " + j.role));
  if (j) return h("div", { class: "md" }, h("ul", {}, Object.entries(j).map(([k, v]) => h("li", {}, h("b", {}, k + ": "), typeof v === "string" ? v : JSON.stringify(v)))));
  if (m.content.includes("===ANSWER===")) return h("div", { class: "md" }, h("p", {}, "🏁 Yakuniy javob va tayyor prompt tayyorlandi."));
  const full = m.content, short = full.length > 1400;
  const box = h("div", {}, md(short ? full.slice(0, 1400) + "…" : full));
  if (short) {
    const more = h("button", { class: "linkbtn" }, "Hammasini ko'rsatish");
    more.addEventListener("click", () => { box.replaceChildren(md(full)); });
    box.append(more);
  }
  return box;
}
const TEXT_EXT = /\.(txt|md|html?|css|js|mjs|json|py|ts|tsx|jsx|csv|xml|ya?ml|sh|sql|java|c|cpp|h|go|rs|php|rb|svg|toml|ini|log)$/i;
// Oxirgi ko'rilgan ma'lumot telefonda saqlanadi: ilova ochilishi bilan darhol ko'rinadi, yangisi orqada keladi
const loadCache = () => { try { return JSON.parse(localStorage.getItem("aij_cache") || "{}"); } catch { return {}; } };
let saveTimer = 0;
function saveCache() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => { try { localStorage.setItem("aij_cache", JSON.stringify({ ...S.cache, _chat: S.chat.slice(-60) })); } catch { /* joy tugasa */ } }, 500);
}
const S = { cache: loadCache(), token: localStorage.getItem("aij_token") || "", tab: "team", state: null, sig: {}, chat: [], lastChat: 0, chatOpen: false, typing: false, skip: 0, view: "active", planTab: "rems", planFilter: "all" };

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
  // Vaqt chegarasi: server uxlab qolsa yoki osilsa, sahifa abadiy qora turib qolmasin
  const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), opts.timeout || 45000);
  let r;
  try { r = await fetch("/api" + path, { ...opts, signal: ctl.signal, headers: { Authorization: "Bearer " + S.token, "Content-Type": "application/json" } }); }
  catch (e) { throw new Error(e.name === "AbortError" ? "Server javob bermadi" : "Aloqa yo'q"); }
  finally { clearTimeout(timer); }
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

// ---------- iPhone'da o'rnatilgan ilova: oyna balandligini to'liq ekranga tenglash ----------
// Ba'zi iOS holatlarida (bosh ekranga qo'shilgan ilova) layout balandligi ekrandan holat paneli qadar kam chiqadi
// va pastda qora yo'l qoladi. Ekranning haqiqiy balandligini o'zimiz o'rnatamiz; oddiy brauzerda hech narsa o'zgarmaydi.
const isStandalone = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
function fitViewport() {
  const root = document.documentElement;
  if (!isStandalone()) { for (const k of ["--app-h", "--app-top"]) root.style.removeProperty(k); root.classList.remove("kb"); return; }
  const long = Math.max(screen.width, screen.height), short = Math.min(screen.width, screen.height);
  const target = window.innerHeight > window.innerWidth ? long : short, full = Math.max(window.innerHeight, target);
  const vv = window.visualViewport;
  // Klaviatura ochiq: ko'rinadigan oyna to'liq balandlikdan ancha kichik -> ilova klaviatura tepasigacha qisqaradi
  const kb = !!vv && vv.height < full - 120;
  root.classList.toggle("kb", kb);
  root.style.setProperty("--app-h", (kb ? vv.height : full) + "px");
  root.style.setProperty("--app-top", (kb ? vv.offsetTop : 0) + "px");
  if (!kb && window.scrollY) window.scrollTo(0, 0);  // klaviatura yopilgach iOS sahifani siljitib qo'yadi
}
fitViewport();
for (const ev of ["resize", "orientationchange", "pageshow", "visibilitychange"]) window.addEventListener(ev, () => setTimeout(fitViewport, ev === "orientationchange" ? 250 : 0));
if (window.visualViewport) for (const ev of ["resize", "scroll"]) window.visualViewport.addEventListener(ev, fitViewport);
document.addEventListener("focusout", () => setTimeout(fitViewport, 150));
function viewportInfo() {  // Hisob sahifasi oxirida: muammo qolsa, shu raqamlar sababini ko'rsatadi
  const probe = h("div", { style: "position:fixed;left:0;top:0;padding-bottom:env(safe-area-inset-bottom);padding-top:env(safe-area-inset-top);visibility:hidden" });
  document.body.append(probe); const cs = getComputedStyle(probe); const sab = cs.paddingBottom, sat = cs.paddingTop; probe.remove();
  return `ekran ${screen.width}×${screen.height} · oyna ${innerWidth}×${innerHeight} · xavfsiz zona ${sat}/${sab}${isStandalone() ? " · ilova rejimi" : ""}`;
}

// ---------- kirish ----------
function showLogin(msg) { $("login").hidden = false; $("login-err").textContent = msg || ""; }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// Ulanish: bepul serverda birinchi so'rov uni uyg'otadi (1 daqiqagacha) — qora ekran o'rniga holatni ko'rsatamiz
async function tryLogin(token) {
  const splash = $("splash"), msg = $("splash-msg"), retry = $("splash-retry");
  splash.hidden = false; retry.hidden = true; msg.textContent = "";
  const t0 = Date.now();
  const tick = setInterval(() => {
    const s = Math.round((Date.now() - t0) / 1000);
    if (s >= 4) msg.textContent = `Server uyg'onmoqda (bepul tarifda 1 daqiqagacha)… ${s} s`;
  }, 1000);
  try {
    S.token = token;
    for (;;) {
      try { await api("/state", { timeout: 25000 }); break; }
      catch (e) {
        if (e.message === "auth") return false;
        if (Date.now() - t0 > 100000) {
          clearInterval(tick);
          msg.textContent = "Server javob bermayapti (" + e.message + "). Render'da xizmat ishlayotganini tekshiring.";
          retry.hidden = false;
          await new Promise((res) => { retry.onclick = res; });
          return tryLogin(token);
        }
        await sleep(3000);
      }
    }
    localStorage.setItem("aij_token", token); $("login").hidden = true; return true;
  } finally { clearInterval(tick); splash.hidden = true; }
}
$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!(await tryLogin($("token").value.trim()))) $("login-err").textContent = "Kalit noto'g'ri"; else start();
});
// PWA: telefon/kompyuterga ilova sifatida o'rnatish
let installEvt = null;
window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installEvt = e; });
function installCard() {
  const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  if (standalone) return h("div", { class: "card" }, h("div", { class: "kv" }, h("span", {}, "📲 Ilova"), h("span", { class: "muted" }, "o'rnatilgan ✓")));
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
  const btn = installEvt ? h("button", { class: "btn lime full", onclick: async () => {
    installEvt.prompt(); try { await installEvt.userChoice; } catch { /* bekor qilindi */ } installEvt = null; refresh(true);
  } }, "📲 Ilovani o'rnatish") : null;
  const tip = ios
    ? "iPhone: brauzerdagi «Ulashish» (□↑) tugmasi → «Bosh ekranga qo'shish» → «Qo'shish». Ilovani birinchi ochganda kalitni bir marta kiritasiz."
    : installEvt ? "Ilova alohida oynada ochiladi, kalit eslab qolinadi." : "Brauzer menyusi (⋮) → «Ilovani o'rnatish» yoki «Bosh ekranga qo'shish».";
  return h("div", { class: "card" }, btn, h("p", { class: "hint" }, tip));
}

// ---------- oyna ----------
function openSheet(...kids) { $("sheet-body").replaceChildren(...kids.filter((k) => k != null && k !== false)); $("sheet").hidden = false; }
const closeSheet = () => { $("sheet").hidden = true; };
$("sheet-bg").addEventListener("click", closeSheet);

// ---------- pastki menyu ----------
function renderTabs() {
  $("view").dataset.tab = S.tab;
  const nav = $("tabs"); nav.replaceChildren();
  for (const [id, label] of TABS) {
    const b = h("button", { class: "tab" + (S.tab === id ? " on" : ""), onclick: () => go(id), "aria-label": label },
      h("span", { class: "ic" }, svg(IC[id])), label);
    if (id === "cards" && S.state && S.state.pending) b.append(h("span", { class: "badge" }, S.state.pending));
    nav.append(b);
  }
}
// Tab darhol almashadi: oxirgi ma'lumot keshdan bir zumda chiziladi, yangisi orqada olinadi (sekin aloqada ham)
function paint(tab, data) {
  const v = $("view");
  v.replaceChildren(...VIEWS[tab][1](data).filter((n) => n != null && n !== false));
  S.sig[tab] = JSON.stringify(data);
}
function go(tab) {
  const v = $("view"); v.classList.remove("swap"); void v.offsetWidth; v.classList.add("swap");  // sahifa almashganda yumshoq paydo bo'lish
  S.tab = tab; renderTabs();
  if (S.cache[tab]) paint(tab, S.cache[tab]);
  else { $("view").replaceChildren(h("div", { class: "empty" }, "Yuklanmoqda…")); S.sig[tab] = null; }
  $("view").scrollTop = 0;
  refresh(false);
}
async function prefetch() {  // boshqa tablarni oldindan yuklab qo'yamiz: birinchi bosishda ham darhol ochiladi
  for (const tab of Object.keys(VIEWS)) {
    if (tab === S.tab || S.cache[tab]) continue;
    try { S.cache[tab] = await VIEWS[tab][0](); } catch { /* keyin bosilganda yuklanadi */ }
  }
}

// ---------- ko'rinishlar: har biri ma'lumot oladi va chizadi; o'zgarmasa qayta chizilmaydi ----------
const VIEWS = { team: [() => (isDesk() ? loadOverview() : loadTeam()), (d) => (d.overview ? drawOverview(d) : drawTeam(d))], cards: [loadCards, drawCards], tasks: [loadTasks, drawTasks], plans: [loadPlans, drawPlans], stats: [loadStats, drawStats] };
async function refresh(force) {
  const [load, draw] = VIEWS[S.tab];
  try {
    const tab = S.tab, data = await load(), sig = JSON.stringify(data);
    S.cache[tab] = data; saveCache();
    if (tab !== S.tab) return;  // kutilayotganda boshqa tabga o'tildi
    if (!force && S.sig[S.tab] === sig) return;
    S.sig[S.tab] = sig;
    const v = $("view"), top = v.scrollTop;
    v.replaceChildren(...draw(data).filter((n) => n != null && n !== false)); v.scrollTop = top;
  } catch (e) {
    if (e.message === "auth") return;
    S.sig[S.tab] = null;  // keyingi muvaffaqiyatli yuklashda albatta qayta chiziladi (oldin xato ekranda qotib qolardi)
    const v = $("view");
    if (!v.children.length || v.querySelector(".empty.err")) v.replaceChildren(h("div", { class: "empty err" }, "Sekin aloqa: " + e.message + ". Qayta urinilmoqda…"));
    else toast("Sekin aloqa, qayta urinilmoqda…");  // eski ma'lumot ko'rinib turadi
  }
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
function noKeyBanner(state) {
  if (state.providers && state.providers.length) return null;
  return h("div", { class: "warnbox", onclick: () => go("stats") }, h("b", {}, "⚠️ AI kaliti ulanmagan"),
    h("span", {}, "Server hech qaysi AI'ni ko'rmayapti, shuning uchun chat va vazifalar ishlamaydi. Render → Environment ga GEMINI_API_KEY (yoki ANTHROPIC_API_KEY) qo'shing va qayta ishga tushiring."));
}
function drawTeam({ state, team, ceo }) {
  const sorted = [...team].sort((a, b) => b.busy - a.busy);
  const quote = ceo && !ceo.startsWith("[Vazifa") ? ceo : "Salom! Men Rahbarman. Suhbatlashing yoki «Vazifa berish» tugmasini bosing.";
  return [
    head("Jamoa", liveTag()),
    noKeyBanner(state),
    h("div", { class: "label" }, "Hozir ishda"),
    sorted.some((a) => a.busy)
      ? h("div", { class: "avatars" }, sorted.filter((a) => a.busy).map((a) => h("div", { class: "av busy" }, h("i", {}, initials(a.name)), h("span", { class: "nm" }, a.name))))
      : h("p", { class: "hint" }, "Hozir hamma bo'sh. Vazifa bering."),
    h("div", { class: "card lime" },
      h("div", { class: "row" }, h("div", { class: "ava" }, "R"),
        h("div", {}, h("div", { class: "who" }, "Rahbar"), h("div", { class: "st" }, state.pending ? `onlayn · ${state.pending} ta qaror kutmoqda` : "onlayn"))),
      h("blockquote", {}, quote),
      h("div", { class: "btns" }, h("button", { class: "btn black", onclick: () => openChat(false) }, "Chatni ochish"),
        h("button", { class: "btn outline", onclick: () => taskSheet() }, "Vazifa berish"))),
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
    h("div", { class: "acts" }, h("button", { class: "btn lime", onclick: orgSheet }, "🏢 Tuzilma"), h("button", { class: "btn ghost", onclick: hireSheet }, "+ Xodim yollash"),
      h("button", { class: "btn ghost", onclick: hrReview }, "🧑‍💼 HR tahlili")),
  ];
}
// ---------- Kompyuter (keng ekran): "Umumiy ko'rinish" dashboardi. Mobil ko'rinish o'zgarmaydi ----------
const DESK = matchMedia("(min-width: 1100px)");
const isDesk = () => DESK.matches;
DESK.addEventListener("change", () => { delete S.cache.team; S.sig = {}; if (S.tab === "team") refresh(true); });
const LIME = "#cfff1a", AMBER = "#ffb03a";
const SVGNS = "http://www.w3.org/2000/svg";
function sv(tag, attrs, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  kids.forEach((k) => el.append(k));
  return el;
}
function smoothPath(pts) {  // Catmull-Rom -> Bezier: videodagidek silliq to'lqin chiziq
  let d = `M${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6], c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C${c1[0].toFixed(1)},${c1[1].toFixed(1)} ${c2[0].toFixed(1)},${c2[1].toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`;
  }
  return d;
}
let gradN = 0;
function areaChart(values, { w = 600, h = 180, labels = null, color = LIME, fill = true } = {}) {
  const n = values.length, max = Math.max(...values, 0) || 1, bottom = h - (labels ? 22 : 6), top = 10, pad = 6;
  const pts = values.map((v, i) => [pad + (i * (w - 2 * pad)) / Math.max(n - 1, 1), bottom - (v / max) * (bottom - top)]);
  const el = sv("svg", { viewBox: `0 0 ${w} ${h}`, class: "chart", role: "img" });
  const line = smoothPath(pts), id = "ag" + ++gradN;
  if (fill) {
    el.append(sv("defs", {}, sv("linearGradient", { id, x1: 0, y1: 0, x2: 0, y2: 1 },
      sv("stop", { offset: "0%", "stop-color": color, "stop-opacity": ".32" }), sv("stop", { offset: "100%", "stop-color": color, "stop-opacity": "0" }))));
    for (let g = 1; g <= 3; g++) el.append(sv("line", { x1: 0, x2: w, y1: top + ((bottom - top) * g) / 4, y2: top + ((bottom - top) * g) / 4, class: "grid" }));
    el.append(sv("path", { d: `${line} L${pts[n - 1][0]},${bottom} L${pts[0][0]},${bottom} Z`, fill: `url(#${id})` }));
  }
  el.append(sv("path", { d: line, fill: "none", stroke: color, "stroke-width": fill ? 3 : 2.5, "stroke-linecap": "round", class: "glowline" }));
  const last = pts[n - 1];
  el.append(sv("circle", { cx: last[0], cy: last[1], r: fill ? 5 : 3.5, fill: color, class: "pulse" }));
  if (labels) labels.forEach((t, i) => {
    if (i % 2 && i !== n - 1) return;
    el.append(sv("text", { x: pts[i][0], y: h - 4, "text-anchor": i === 0 ? "start" : i === n - 1 ? "end" : "middle", class: "ax" }, t));
  });
  return el;
}
function gauge(pct) {  // videodagi yarim aylana ko'rsatkich
  const len = Math.PI * 34, arc = "M8,44 A34,34 0 0 1 76,44";
  return sv("svg", { viewBox: "0 0 84 50", class: "gauge" },
    sv("path", { d: arc, fill: "none", stroke: "#25282b", "stroke-width": 8, "stroke-linecap": "round" }),
    sv("path", { d: arc, fill: "none", stroke: pct > 85 ? "#ff5a52" : LIME, "stroke-width": 8, "stroke-linecap": "round",
      "stroke-dasharray": `${(len * Math.min(pct, 100)) / 100} ${len}`, class: "glowline" }));
}
function orb(active) {  // jonli "miya": ish bo'lsa yorqinroq va tezroq
  return h("div", { class: "orb" + (active ? " on" : ""), "aria-hidden": "true" },
    h("i", { class: "orbit r1" }), h("i", { class: "orbit r2" }), h("i", { class: "orbit r3" }),
    h("b", { class: "blob b1" }), h("b", { class: "blob b2" }), h("b", { class: "blob b3" }), h("span", { class: "core" }));
}
async function loadOverview() {
  const d = await api("/overview");
  S.state = d.state; renderTabs();
  const ceo = [...S.chat].reverse().find((m) => m.role === "ceo");
  return { ...d, ceo: ceo ? ceo.text : "", overview: true };
}
const TONE = { done: "ok", running: "run", failed: "bad", limit: "warn", cancelled: "warn", paused: "warn", interrupted: "warn", stopped: "warn" };
function drawOverview(d) {
  const st = d.state, busy = d.team.filter((a) => a.busy);
  const now = new Date(), WD = ["Yakshanba", "Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba"];
  const MO = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"];
  const date = `${WD[now.getDay()]}, ${now.getDate()}-${MO[now.getMonth()]}`;  // brauzerlar uz-UZ ni har xil chiqaradi
  const head = h("div", { class: "ov-head" },
    h("div", {}, h("p", { class: "crumb" }, date), h("h1", { class: "title" }, "Umumiy ko'rinish")),
    h("div", { class: "ov-status" }, liveTag(), h("span", { class: "pill-s" }, `${busy.length} faol · ${d.team.length} xodim`)));

  // --- chap ustun: bugun + jonli shar + rahbarga yozish ---
  const running = st.running_tasks.length;
  const headline = running ? `${running} ta vazifa ishlanmoqda` + (st.pending ? `, ${st.pending} ta qaror sizni kutmoqda` : "")
    : st.pending ? `${st.pending} ta qaror sizni kutmoqda` : "Hammasi tinch. Yangi vazifa bering.";
  const quote = d.ceo && !d.ceo.startsWith("[Vazifa") ? d.ceo : "Salom! Men Rahbarman. Savol bering yoki vazifa topshiring.";
  const input = h("input", { placeholder: "Rahbarga yozing…", "aria-label": "Rahbarga xabar" });
  const ask = h("form", { class: "ov-ask", onsubmit: (e) => {
    e.preventDefault(); const t = input.value.trim(); if (!t) return;
    openChat(false); $("chat-input").value = t; sendChat();
  } }, input, h("button", { class: "mic mic-btn", type: "button", "aria-label": "Ovozli xabar",
    onclick: (e) => voiceCapture(e.currentTarget, (text) => { openChat(false); $("chat-input").value = text; sendChat(); }) }, svg(IC.mic)),
  h("button", { class: "send", type: "submit", "aria-label": "Yuborish" }, svg(IC.send)));
  const mini = (n, l, cls, onclick) => h(onclick ? "button" : "div", { class: "m " + (cls || ""), onclick }, h("b", {}, n), h("span", {}, l));
  const left = h("section", { class: "ov-col" },
    h("div", { class: "card ov-today" },
      h("p", { class: "kick" }, "Bugun"), h("h2", {}, headline),
      orb(busy.length > 0 || running > 0),
      h("p", { class: "ov-quote" }, quote.length > 240 ? quote.slice(0, 240) + "…" : quote),
      ask,
      h("div", { class: "ov-mini" }, mini(st.done_today, "bajarildi"), mini(busy.length, "ishlayapti", busy.length ? "on" : ""),
        mini(st.pending, "kutmoqda", st.pending ? "hot" : "", () => go("cards")))),
    h("div", { class: "card" }, h("p", { class: "kick" }, "Eslatmalar"),
      d.reminders.length ? d.reminders.map((r) => h("div", { class: "ov-row" }, h("span", { class: "bul amber" }),
        h("div", { class: "grow" }, h("b", {}, r.text)), h("span", { class: "tag-s warn" }, r.local)))
        : h("p", { class: "muted sm" }, "Eslatma yo'q. Chatda «ertaga 9 da … eslat» deb yozing.")));

  // --- o'rta ustun: ko'rsatkichlar, dinamika, vazifalar ---
  const cost14 = d.daily.map((x) => x.cost), done14 = d.daily.map((x) => x.done);
  const sum = (a) => a.reduce((x, y) => x + y, 0);
  const on = st.budgets.filter((b) => b.enabled), spent = sum(on.map((b) => b.spent)), budget = sum(on.map((b) => b.budget));
  const pct = budget ? Math.round((spent / budget) * 100) : 0;
  const kpi = (label, value, sub, viz, onclick) => h(onclick ? "button" : "div", { class: "card kpi", onclick },
    h("div", { class: "kv-l" }, h("p", { class: "kick" }, label), h("b", { class: "big" }, value), h("p", { class: "muted sm" }, sub)), viz);
  const stack = h("div", { class: "stack" }, d.team.slice(0, 6).map((a) => h("i", { class: a.busy ? "on" : "", title: agentName(a.name) }, agentName(a.name)[0].toUpperCase())));
  const kpis = h("div", { class: "kpis" },
    kpi("Bugungi sarf", usd(st.today), `14 kunda ${usd(sum(cost14))}`, areaChart(cost14, { w: 150, h: 54, fill: false })),
    kpi("Bajarilgan vazifalar", sum(done14), "so'nggi 14 kun", areaChart(done14, { w: 150, h: 54, fill: false, color: AMBER })),
    kpi("Byudjet ishlatildi", pct + "%", `${usd(spent)} / ${usd(budget)}`, gauge(pct), () => go("stats")),
    kpi("Jamoa", d.team.length + " xodim", busy.length ? busy.map((a) => agentName(a.name)).join(", ") + " ishlayapti" : "hamma bo'sh", stack));
  const labels = d.daily.map((x) => x.day.slice(8, 10) + "." + x.day.slice(5, 7));
  let mode = S.ovMode || "cost";
  const chartBox = h("div", { class: "chart-box" });
  const total = h("b", { class: "big" });
  const paintChart = () => {
    const bw = chartBox.clientWidth, bh = chartBox.clientHeight;   // skrolsiz ko'rinishda grafik berilgan joyga sig'adi
    chartBox.replaceChildren(areaChart(mode === "cost" ? cost14 : done14, { w: bw > 200 ? bw : 760, h: bh > 40 ? bh : 230, labels }));
    total.textContent = mode === "cost" ? usd(sum(cost14)) : sum(done14) + " ta vazifa";
  };
  const seg = h("div", { class: "seg sm" }, [["cost", "Sarf"], ["done", "Vazifalar"]].map(([k, l]) => h("button", { class: mode === k ? "on" : "", onclick: (e) => {
    S.ovMode = mode = k; [...seg.children].forEach((b) => b.classList.toggle("on", b === e.currentTarget)); paintChart();
  } }, l)));
  paintChart();
  if (window.ResizeObserver) {
    let last = "";
    const ro = new ResizeObserver(() => { const k = chartBox.clientWidth + "x" + chartBox.clientHeight; if (k !== last && chartBox.clientWidth > 0) { last = k; paintChart(); } });
    ro.observe(chartBox);
  }
  const chips = h("div", { class: "chips" }, h("span", { class: "chip run" }, `${d.counts.running} ishlayapti`),
    h("span", { class: "chip warn" }, `${st.pending} kutmoqda`), h("span", { class: "chip bad" }, `${d.counts.failed} xato`));
  const tgrid = h("div", { class: "tgrid" }, d.tasks.map((t) => {
    const [lab] = ST[t.status] || [t.status], tone = TONE[t.status] || "";
    return h("button", { class: "tcard " + tone, onclick: () => openTask(t.id) },
      h("div", { class: "row-b" }, h("b", {}, "#" + t.id), h("span", { class: "tag-s " + tone }, lab)),
      h("p", { class: "clamp" }, t.request),
      h("div", { class: "row-b muted xs" }, h("span", {}, ago(t.created_at)), h("span", {}, usd(t.cost))));
  }));
  const middle = h("section", { class: "ov-col" },
    h("div", { class: "row-b" }, h("p", { class: "kick" }, "Ko'rsatkichlar"), chips),
    kpis,
    h("div", { class: "card ov-chart" }, h("div", { class: "row-b" }, h("div", {}, h("p", { class: "kick" }, "Dinamika · 14 kun"), total), seg), chartBox),
    h("div", { class: "card ov-tasks" },
      h("div", { class: "row-b" }, h("div", {}, h("p", { class: "kick" }, "Vazifalar"), h("b", { class: "big" }, `${d.tasks.length} ta so'nggi`)),
        h("div", { class: "acts-i" }, h("button", { class: "linkbtn", onclick: () => go("tasks") }, "Hammasi →"),
          h("button", { class: "btn lime sm", onclick: () => taskSheet() }, "+ Vazifa"))),
      d.tasks.length ? tgrid : h("p", { class: "muted sm" }, "Hali vazifa yo'q. «+ Vazifa» bilan boshlang.")));

  // --- o'ng ustun: bildirishnomalar, jamoa harakati ---
  const notes = [
    ...d.approvals.map((a) => ({ tone: "amber", title: (a.kind === "telegram" ? "Telegram xabari · " : "Buyruq · ") + agentName(a.agent), text: a.description, open: () => go("cards") })),
    ...d.tasks.filter((t) => t.status === "failed").slice(0, 3).map((t) => ({ tone: "red", title: `#${t.id} xato bilan tugadi`, text: t.note || t.request, open: () => openTask(t.id) })),
    ...d.tasks.filter((t) => t.status === "running").slice(0, 3).map((t) => ({ tone: "lime", title: `#${t.id} ishlanmoqda`, text: t.request, open: () => openTask(t.id) })),
  ];
  const right = h("section", { class: "ov-col" },
    h("div", { class: "card" }, h("p", { class: "kick" }, "Bildirishnomalar"),
      notes.length ? notes.slice(0, 6).map((n) => h("button", { class: "ov-row", onclick: n.open }, h("span", { class: "bul " + n.tone }),
        h("div", { class: "grow" }, h("b", {}, n.title), h("p", { class: "muted sm clamp" }, n.text))))
        : h("p", { class: "muted sm" }, "✓ Hech narsa sizni kutmayapti.")),
    h("div", { class: "card ov-agents" }, h("div", { class: "row-b" }, h("p", { class: "kick" }, "Jamoa harakati"), h("span", {}, h("button", { class: "linkbtn", onclick: orgSheet }, "🏢 Tuzilma"), " ", h("button", { class: "linkbtn", onclick: hireSheet }, "+ Yollash"))),
      [...d.team].sort((a, b) => b.busy - a.busy || b.steps - a.steps).map((a) => h("button", { class: "ov-agent", onclick: () => agentSheet(a) },
        h("i", { class: "av-c" + (a.busy ? " on" : "") }, agentName(a.name)[0].toUpperCase()),
        h("div", { class: "grow" }, h("b", {}, agentName(a.name)), h("p", { class: "muted sm clamp1" }, a.busy ? `vazifa #${a.task_id} ustida` : a.role)),
        h("div", { class: "r" }, h("span", { class: "tag-s " + (a.busy ? "run" : "") }, a.busy ? "ishlayapti" : "bo'sh"), h("span", { class: "muted xs" }, a.steps + " qadam"))))));
  return [h("div", { class: "ov" }, head, noKeyBanner(st), h("div", { class: "ov-grid" }, left, middle, right))];
}
async function hrReview() {
  try {
    const r = await post("/team/review");
    toast(r.fired.length ? "Bo'shatildi: " + r.fired.join(", ") : r.tasks < 10 ? `Tahlil uchun 10 ta vazifa kerak (hozir ${r.tasks})` : "Hamma xodim kerak, hech kim bo'shatilmadi");
    refresh(true);
  } catch (e) { toast(e.message); }
}
const DEPT_IC = { boshqaruv: "🧭", tech: "🛠", marketing: "📣", tadqiqot: "🔎", aloqa: "💬", sifat: "✅" };
const MODEL_L = { auto: "Avto", gemini: "Gemini", anthropic: "Claude", openai: "ChatGPT", groq: "Groq (bepul)", openrouter: "OpenRouter (bepul)" };
const TIER_L = { cheap: "Arzon", mid: "O'rta", strong: "Kuchli" };
const AUTO_L = { off: "O'chiq", report: "Xabar beradi", auto: "O'zi tuzatadi" };
async function agentSheet(a) {
  let org = null;
  try { org = await api("/org"); a = org.agents.find((x) => x.name === a.name) || a; } catch { /* tarmoq yo'q: faqat ko'rinish */ }
  const fire = async () => { if (!confirm(a.name + " ishdan bo'shatilsinmi?")) return;
    try { await post("/team/fire", { name: a.name }); closeSheet(); toast("Bo'shatildi"); refresh(true); } catch (e) { toast(e.message); } };
  const save = async (d, msg) => { try { await post("/team/meta", { name: a.name, ...d }); toast(msg); refresh(true); agentSheet(a); } catch (e) { toast(e.message); } };
  const select = (opts, cur, onPick) => { const el = h("select", {}, opts.map(([k, l]) => h("option", { value: k, selected: k === cur ? "selected" : null }, l)));
    el.addEventListener("change", () => onPick(el.value)); return el; };
  const tools = a.tools && a.tools.length;
  const edit = org ? [
    h("label", {}, "Bo'lim"), select(org.depts.map((d) => [d.key, (DEPT_IC[d.key] || "") + " " + d.title]), a.dept, (v) => save({ dept: v }, "Bo'lim saqlandi")),
    h("label", {}, "Qaysi AI ishlaydi"), select(org.models.map((m) => [m, MODEL_L[m] || m]), a.model || "auto", (v) => save({ model: v }, "AI: " + (MODEL_L[v] || v))),
    h("p", { class: "hint" }, "Avto: Sozlamalardagi asosiy AI. Tanlangan AI ishlamasa, boshqasi zaxira bo'ladi." +
      (tools ? " Bu xodim asboblar (fayl, internet) ishlatadi: bepul AI'lar asbob qo'llamaydi, shuning uchun ular faqat asbobsiz qismlarda ishlaydi." : "")),
    h("label", {}, "Model darajasi"), select(Object.entries(TIER_L), a.tier, (v) => save({ tier: v }, "Daraja: " + TIER_L[v])),
    h("p", { class: "hint" }, "Arzon: oddiy ishlar. O'rta: asosiy ish. Kuchli: eng qiyin tahlil (qimmat). Reja qadamda boshqasini so'rasa, o'sha ishlatiladi."),
  ] : [];
  openSheet(h("h2", {}, agentName(a.name)), h("p", { class: "muted" }, a.role),
    a.replaces ? h("p", { class: "hint" }, "Nimani almashtiradi: " + a.replaces) : null,
    h("div", { class: "pills" }, h("span", { class: "pill" + (a.busy ? " lime" : "") }, a.busy ? "ishlayapti" : "bo'sh"), h("span", { class: "pill" }, TIER_L[a.tier] || a.tier),
      (a.tools || []).map((t) => h("span", { class: "pill" }, t)), h("span", { class: "pill" }, a.steps + " ish"), h("span", { class: "pill" }, usd(a.cost))),
    ...edit,
    a.core ? null : h("div", { class: "label" }), a.core ? null : h("button", { class: "btn red full", onclick: fire }, "Ishdan bo'shatish"));
}
async function orgSheet() {
  let d;
  try { d = await api("/org"); } catch (e) { return toast(e.message); }
  const card = (a) => h("button", { class: "org-card" + (a.busy ? " busy" : ""), onclick: () => agentSheet(a) },
    h("div", { class: "org-top" }, h("b", {}, agentName(a.name)), h("span", { class: "pill" }, MODEL_L[a.model] || a.model)),
    h("p", { class: "muted sm clamp" }, a.replaces || a.role),
    h("div", { class: "muted xs" }, (a.busy ? "🟢 ishlayapti" : "bo'sh") + " · " + (TIER_L[a.tier] || a.tier) + " · " + a.steps + " ish · " + usd(a.cost)));
  const autoCard = (j) => h("div", { class: "org-card auto" },
    h("div", { class: "org-top" }, h("b", {}, "🤖 " + j.title), h("span", { class: "pill" + (j.mode !== "off" ? " lime" : "") }, j.every)),
    h("p", { class: "muted sm" }, j.replaces + (j.ai ? " · «O'zi tuzatadi» rejimida AI ishlatadi" : " · AI'siz, bepul")),
    h("div", { class: "seg sm" }, j.modes.map((m) => h("button", { class: j.mode === m ? "on" : "", onclick: async () => {
      try { await post("/auto", { name: j.name, mode: m }); toast(j.title + ": " + AUTO_L[m]); orgSheet(); } catch (e) { toast(e.message); } } }, AUTO_L[m]))),
    j.result ? h("p", { class: "hint" }, j.result + (j.last ? " (" + ago(j.last) + ")" : "")) : null,
    h("button", { class: "btn ghost sm", onclick: async (ev) => { ev.target.disabled = true; ev.target.textContent = "Tekshirilmoqda…";
      try { const r = await post("/auto/run", { name: j.name }); toast(r.result || "Tayyor"); orgSheet(); } catch (e) { toast(e.message); ev.target.disabled = false; } } }, "▶ Hozir tekshir"));
  const sections = d.depts.map((dep) => {
    const ag = d.agents.filter((a) => a.dept === dep.key), au = d.auto.filter((j) => j.dept === dep.key);
    if (!ag.length && !au.length) return null;
    return h("div", { class: "org-dept" },
      h("div", { class: "org-dhead" }, h("b", {}, (DEPT_IC[dep.key] || "") + " " + dep.title), dep.lead ? h("span", { class: "muted xs" }, "boshliq: " + agentName(dep.lead)) : null),
      h("div", { class: "org-grid" }, ...ag.map(card), ...au.map(autoCard)));
  });
  const lead = h("div", { class: "seg" },
    h("button", { class: d.dept_leads ? "on" : "", onclick: async () => { try { await post("/dept_leads", { enabled: true }); orgSheet(); } catch (e) { toast(e.message); } } }, "Yoqilgan"),
    h("button", { class: d.dept_leads ? "" : "on", onclick: async () => { try { await post("/dept_leads", { enabled: false }); orgSheet(); } catch (e) { toast(e.message); } } }, "O'chiq"));
  openSheet(h("h2", {}, "🏢 Tuzilma"),
    h("div", { class: "org-root" }, h("span", { class: "org-node" }, "👤 Siz"), h("span", { class: "org-arrow" }, "→"), h("span", { class: "org-node lime" }, "🧭 Rahbar"),
      h("span", { class: "org-arrow" }, "→"), h("span", { class: "org-node" }, d.depts.length + " bo'lim · " + d.agents.length + " xodim · " + d.auto.length + " avtonom")),
    h("p", { class: "hint" }, "Xodim ustiga bosing: bo'limi, qaysi AI va model darajasini o'zgartirasiz. Ishlamay turgan xodim pul sarflamaydi."),
    ...sections,
    h("div", { class: "label" }, "Bo'lim boshliqlari"), lead,
    h("p", { class: "hint" }, "Yoqilsa, murakkab vazifada har bo'lim boshlig'i o'z bo'limiga tushgan qadamlarni ekspert darajasida aniqlashtiradi (talablar, standart, tekshiruv). Sifat oshadi, har vazifaga taxminan +15–30% xarajat."));
}
function taskSheet(basedOn) {
  basedOn = Number.isInteger(basedOn) ? basedOn : null;
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
    try { await post("/tasks", { text: text.value, files: uploaded, based_on: basedOn }); closeSheet(); toast("Vazifa topshirildi"); if (!S.chatOpen) go("tasks"); }
    catch (e) { toast(e.message); btn.disabled = false; }
  });
  openSheet(h("h2", {}, basedOn ? `Vazifa #${basedOn} ustida davom` : "Yangi vazifa"),
    h("p", { class: "muted" }, basedOn ? "Jamoa avvalgi natija va fayllardan boshlaydi. Nimani o'zgartirish yoki qo'shish kerakligini yozing." : "Jamoa mustaqil bajaradi va natijani sizga topshiradi."),
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
  const [state, tasks] = await Promise.all([api("/state"), api("/tasks?view=" + S.view)]);
  S.state = state; renderTabs(); return { tasks, view: S.view };
}
function drawTasks({ tasks, view }) {
  const seg = h("div", { class: "seg" }, [["active", "Faol"], ["done", "Tugatilgan"], ["archive", "Arxiv"]].map(([v, l]) =>
    h("button", { class: view === v ? "on" : "", onclick: () => { S.view = v; refresh(true); } }, l)));
  const EMPTY = { active: "Hali vazifa yo'q. «Vazifa berish» tugmasini bosing.", done: "Tugatilgan vazifa hali yo'q.", archive: "Arxiv bo'sh (bu yerga rad etilgan va arxivga olingan vazifalar tushadi)" };
  if (!tasks.length) return [head("Vazifalar", liveTag()), seg, h("div", { class: "empty" }, EMPTY[view])];
  return [head("Vazifalar", liveTag()), seg, h("div", { class: "tlist" }, tasks.map((t) => {
    const [label, tone] = ST[t.status] || [t.status, ""];
    const barTone = t.status === "running" ? "run" : t.status === "failed" ? "bad" : t.status === "done" ? "" : "warn";
    const why = t.status !== "done" && t.status !== "running" && t.note ? t.note : "";
    return h("button", { class: "item", onclick: () => openTask(t.id), "aria-label": "Vazifa " + t.id },
      h("div", { class: "grow" }, h("h3", {}, t.request), h("div", { class: "bar " + barTone }, h("i")),
        h("p", {}, `${label} · ${usd(t.cost)} · ${ago(t.created_at)}${t.based_on ? ` · #${t.based_on} ustida` : ""}`), why ? h("p", { class: "note" + (t.status === "failed" ? " red" : "") }, why) : null));
  }))];
}
async function openTask(id) {
  openSheet(h("h2", {}, "Vazifa #" + id), h("p", { class: "muted" }, "Yuklanmoqda…"));  // oyna darhol ochiladi
  let t;
  try { t = await api("/tasks/" + id); } catch (e) { return toast(e.message); }
  const [label] = ST[t.status] || [t.status];
  const MAIN = { "NATIJA.md": ["📄 To'liq natija", "Barcha so'ralgan narsa bitta faylda"], "PROMPT.md": ["🤖 Tayyor prompt", "Boshqa AI'ga bersangiz, shu ishni to'liq qayta bajaradi"] };
  const main = t.files.filter((f) => MAIN[f]).map((f) => h("div", { class: "card mainfile" },
    h("div", {}, h("b", {}, MAIN[f][0]), h("p", { class: "hint" }, MAIN[f][1])),
    h("div", { class: "acts" }, h("button", { class: "btn", onclick: () => openFile(t.id, f) }, "Ochish"),
      f === "PROMPT.md" ? h("button", { class: "btn lime", onclick: () => copyFile(t.id, f) }, "Nusxalash") : h("button", { class: "btn ghost", onclick: () => download(t.id, f) }, "Yuklab olish"))));
  const other = t.files.filter((f) => !MAIN[f]).map((f) => h("button", { class: "btn ghost", onclick: () => openFile(t.id, f) }, "📎 " + f));
  const team = t.messages.filter((m) => m.agent !== "hr" && !(tryJson(m.content) || {}).facts);
  const act = (path, msg, method) => async () => {
    try { await api(path, { method: method || "POST" }); closeSheet(); toast(msg); refresh(true); } catch (e) { toast(e.message); }
  };
  const acts = t.status === "running"
    ? [h("button", { class: "btn red", onclick: act(`/tasks/${t.id}/stop`, "To'xtatilmoqda…") }, "⏹ To'xtatish")]
    : t.archived || t.status === "cancelled"
      ? [t.archived ? h("button", { class: "btn", onclick: act(`/tasks/${t.id}/restore`, "Qaytarildi") }, "↩ Qaytarish")
           : h("button", { class: "btn lime", onclick: () => taskSheet(t.id) }, "✏️ Qayta ishlatish"),
         h("button", { class: "btn red", onclick: () => { if (confirm("Vazifa va uning fayllari butunlay o'chiriladi. Davom etasizmi?")) act(`/tasks/${t.id}`, "O'chirildi", "DELETE")(); } }, "🗑 Butunlay o'chirish")]
      : [CAN_RESUME.has(t.status) ? h("button", { class: "btn lime", onclick: async () => {  // bir bosishda: bajarilgan qism saqlanadi, qolgani tugatiladi
            try { await post("/tasks", { text: t.request, based_on: t.id }); closeSheet(); toast("▶️ Davom ettirilmoqda…"); go("tasks"); } catch (e) { toast(e.message); }
          } }, "▶️ Davom ettirish") : null,
         h("button", { class: CAN_RESUME.has(t.status) ? "btn" : "btn lime", onclick: () => taskSheet(t.id) }, "✏️ O'zgartirish / davom"),
         h("button", { class: "btn", onclick: act(`/tasks/${t.id}/archive`, "Arxivga olindi") }, "🗄 Arxivga olish")];
  const why = WHY[t.status] || "";
  openSheet(h("h2", {}, "Vazifa #" + t.id), h("p", { class: "muted" }, `${label} · ${usd(t.cost)}${t.archived ? " · arxivda" : ""}`),
    why || t.note ? h("p", { class: "note" + (t.status === "failed" ? " red" : "") }, [why, t.note].filter(Boolean).join(" ")) : null, h("p", {}, t.request),
    h("div", { class: "acts" }, acts),
    t.approvals && t.approvals.length ? h("div", { class: "label" }, "Ruxsat so'rovlari") : null,
    ...(t.approvals || []).map((a) => { const [txt, tone] = APPR[a.status] || [a.status, ""];
      return h("div", { class: "step" }, h("b", {}, a.agent + " · " + txt), h("div", {}, a.command.slice(0, 200))); }),
    h("div", { class: "label" }, "Natija"), h("div", { class: "card result" }, t.result ? md(t.result) : h("p", { class: "muted" }, "(hali natija yo'q)")),
    main.length ? h("div", { class: "label" }, "Tayyor natija") : null, ...main,
    other.length ? h("div", { class: "label" }, main.length ? "Ish fayllari" : "Fayllar") : null, other.length ? h("div", { class: "pills" }, other) : null,
    team.length ? h("details", { class: "team" }, h("summary", {}, `Jamoa ishi (${team.length} qadam)`),
      ...team.map((m) => h("div", { class: "step" }, h("b", {}, agentName(m.agent)), readable(m)))) : null);
}
async function openFile(id, path) {
  if (!TEXT_EXT.test(path)) return download(id, path);
  try {
    const r = await fetch("/api/tasks/" + id + "/files/" + path.split("/").map(encodeURIComponent).join("/"), { headers: { Authorization: "Bearer " + S.token } });
    if (!r.ok) throw new Error("Ochib bo'lmadi");
    let text = (await r.text()).slice(0, 80000);
    if (/\.json$/i.test(path)) { try { text = JSON.stringify(JSON.parse(text), null, 2); } catch { /* xom holicha */ } }
    const isMd = /\.md$/i.test(path);
    const view = h("div", {}, isMd ? h("div", { class: "card result" }, md(text)) : h("div", { class: "pre code" }, text));
    const toggle = isMd ? h("button", { class: "btn ghost" }, "Matn ko'rinishi") : null;
    if (toggle) {
      let raw = false;
      toggle.addEventListener("click", () => {
        raw = !raw; toggle.textContent = raw ? "Chiroyli ko'rinish" : "Matn ko'rinishi";
        view.replaceChildren(raw ? h("div", { class: "pre code" }, text) : h("div", { class: "card result" }, md(text)));
      });
    }
    openSheet(h("button", { class: "backlink", onclick: () => openTask(id) }, "‹ Vazifaga qaytish"), h("h2", {}, path), view,
      h("div", { class: "acts" }, h("button", { class: "btn lime", onclick: () => copyText(text) }, "Nusxalash"), toggle,
        h("button", { class: "btn", onclick: () => download(id, path) }, "Yuklab olish")));
  } catch (e) { toast(e.message); }
}
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast("Nusxalandi"); }
  catch { const ta = h("textarea", {}); ta.value = text; document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove(); toast("Nusxalandi"); }
}
async function copyFile(id, path) {
  try {
    const r = await fetch("/api/tasks/" + id + "/files/" + path.split("/").map(encodeURIComponent).join("/"), { headers: { Authorization: "Bearer " + S.token } });
    if (!r.ok) throw new Error("Ochib bo'lmadi");
    await copyText(await r.text());
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
// --- Eslatmalar va rejalar (egasining o'z rejalari; AI vazifa rejalari bu yerga tushmaydi, ular vazifaning ichida) ---
const PERIODS = [["day", "Kunlik"], ["week", "Haftalik"], ["month", "Oylik"], ["year", "Yillik"], ["other", "Boshqa"]];
async function loadPlans() {
  const [state, rems, plans, watch] = await Promise.all([api("/state"), api("/reminders?all=1"), api("/plans"), api("/watches")]);
  S.state = state; renderTabs(); return { rems, plans, watch, smart: !!state.watch_smart };
}
function planSheet(plan) {
  let period = plan ? plan.period : "day";
  const title = h("input", { placeholder: "Reja nomi (masalan: Bugungi ishlar)", value: plan ? plan.title : "" });
  const target = h("input", { type: "date", value: plan && plan.target ? plan.target : "" });
  const items = h("textarea", { rows: "7", placeholder: "Har qatorga bitta band:\nBozorga borish\nAliga qo'ng'iroq\nHisobotni yozish" }, plan ? plan.items.map((i) => i.text).join("\n") : "");
  const seg = h("div", { class: "seg" });
  const drawSeg = () => seg.replaceChildren(...PERIODS.map(([k, l]) => h("button", { type: "button", class: period === k ? "on" : "", onclick: () => { period = k; drawSeg(); } }, l)));
  drawSeg();
  const btn = h("button", { class: "btn lime full" }, plan ? "Saqlash" : "Reja qo'shish");
  btn.addEventListener("click", async () => {
    const old = new Map((plan ? plan.items : []).map((i) => [i.text, i.done]));
    const list = items.value.split("\n").map((t) => t.trim()).filter(Boolean).map((t) => ({ text: t, done: old.get(t) || false }));
    try { await post(plan ? "/plans/" + plan.id : "/plans", { period, title: title.value, items: list, target: target.value }); closeSheet(); toast("Saqlandi"); refresh(true); } catch (e) { toast(e.message); }
  });
  openSheet(h("h2", {}, plan ? "Rejani tahrirlash" : "Yangi reja"), h("label", {}, "Davr"), seg, h("label", {}, "Nomi"), title,
    h("label", {}, "Sana (ixtiyoriy)"), target, h("label", {}, "Bandlar"), items, h("div", { class: "label" }), btn);
}
function watchSheet(w) {
  let kind = w ? w.kind : "tg";
  const target = h("input", { value: w ? w.target : "", autocapitalize: "off" });
  const title = h("input", { value: w ? w.title : "", placeholder: "Nomi (ixtiyoriy)" });
  const kws = h("input", { value: w ? w.keywords : "", placeholder: "frontend, vakansiya, remote" });
  const desc = h("textarea", { rows: "3", placeholder: "Masalan: Toshkentdagi junior frontend ish e'lonlari, maosh ko'rsatilgan" }, w ? w.description : "");
  const price = h("input", { inputmode: "decimal", value: w && w.target_price ? w.target_price : "", placeholder: "masalan 9000000" });
  const box = h("div");
  const seg = h("div", { class: "seg" });
  const draw = () => {
    seg.replaceChildren(...[["tg", "📡 Telegram kanal"], ["price", "🏷 Narx"]].map(([k, l]) => h("button", { type: "button", class: kind === k ? "on" : "", onclick: () => { kind = k; draw(); } }, l)));
    target.placeholder = kind === "tg" ? "@kanal_nomi yoki t.me/kanal" : "https://... mahsulot sahifasi";
    box.replaceChildren(...(kind === "tg"
      ? [h("label", {}, "Kalit so'zlar (vergul bilan) — AI'siz, bepul"), kws,
         h("label", {}, "Nimani izlayapsiz (aqlli kuzatuv uchun)"), desc,
         h("p", { class: "hint" }, "Kalit so'z bo'lsa, faqat shu so'z bor postlar keladi. «Aqlli kuzatuv» yoqilgan bo'lsa, ular AI bilan qo'shimcha tekshiriladi (kalit so'zsiz ham ishlaydi).")]
      : [h("label", {}, "Kutilgan narx (ixtiyoriy)"), price,
         h("p", { class: "hint" }, "Narx tushsa yoki shu narxdan pastga tushsa xabar keladi. Har 6 soatda tekshiriladi. Ba'zi saytlar avtomatik o'qishga ruxsat bermaydi.")]));
  };
  draw();
  const btn = h("button", { class: "btn lime full" }, w ? "Saqlash" : "Kuzatuvni qo'shish");
  btn.addEventListener("click", async () => {
    const d = { kind, target: target.value, title: title.value || target.value, keywords: kws.value, description: desc.value, target_price: price.value };
    try { const r = await post(w ? "/watches/" + w.id : "/watches", d); closeSheet(); toast("Saqlandi. Tekshirilmoqda…");
      try { await post("/watches/" + r.id + "/check"); } catch (_) { /* xato kartada ko'rinadi */ } refresh(true); } catch (e) { toast(e.message); }
  });
  openSheet(h("h2", {}, w ? "Kuzatuvni tahrirlash" : "Yangi kuzatuv"), seg, h("label", {}, "Manba"), target, h("label", {}, "Nomi"), title, box, h("div", { class: "label" }), btn);
}
function watchSection({ watch, smart }, on) {
  const ago_ = (iso) => (iso ? ago(iso) : "hali tekshirilmagan");
  const card = (w) => h("div", { class: "card plan" + (w.enabled ? "" : " fin") },
    h("div", { class: "row-b" }, h("div", { class: "grow" }, h("p", { class: "kick" }, (w.kind === "tg" ? "📡 Telegram" : "🏷 Narx") + " · " + ago_(w.last_check)),
      h("b", { class: "ptitle" }, w.title)),
      h("div", { class: "acts-i" },
        h("button", { class: "linkbtn", title: "Hozir tekshirish", onclick: async () => { try { const r = await post("/watches/" + w.id + "/check"); toast(r.hits.length ? r.hits.length + " ta topildi" : r.watch.error ? "⚠️ " + r.watch.error : "Yangi narsa yo'q"); refresh(true); } catch (e) { toast(e.message); } } }, "🔄"),
        h("button", { class: "linkbtn", onclick: () => watchSheet(w) }, "✏️"),
        h("button", { class: "linkbtn", onclick: async () => { if (!confirm("Kuzatuv o'chirilsinmi?")) return; try { await api("/watches/" + w.id, { method: "DELETE" }); refresh(true); } catch (e) { toast(e.message); } } }, "🗑"))),
    h("p", { class: "muted sm" }, w.kind === "tg" ? (w.keywords ? "Kalit so'zlar: " + w.keywords : "Kalit so'z yo'q") + (w.description ? " · AI: " + w.description : "")
      : (w.price ? "Hozirgi narx: " + w.price.toLocaleString("ru-RU") + (w.min && w.min < w.price ? " · eng past: " + w.min.toLocaleString("ru-RU") : "") : "Narx hali olinmagan") + (w.target_price ? " · kutilgan: " + w.target_price.toLocaleString("ru-RU") : "")),
    w.error ? h("p", { class: "note red" }, "⚠️ " + w.error) : null,
    h("div", { class: "row-b" }, h("span", { class: "tag-s " + (w.enabled ? "ok" : "") }, w.enabled ? "● Kuzatilyapti" : "To'xtatilgan"),
      h("button", { class: "btn ghost sm", onclick: async () => { try { await post("/watches/" + w.id, { enabled: !w.enabled }); refresh(true); } catch (e) { toast(e.message); } } },
        w.enabled ? "⏸ To'xtatish" : "▶ Yoqish")));
  return h("section", { class: "plans-sec watch-sec" + (on ? " on" : "") },
    h("div", { class: "row-b" }, h("p", { class: "kick" }, "Kuzatuv"), h("button", { class: "btn lime sm", onclick: () => watchSheet() }, "+ Kuzatuv")),
    ...[["tg", "📡 Telegram kanal kuzatuvi"], ["price", "🏷 Narx kuzatuvi"]].filter(([k]) => S.state && S.state.watch_on && S.state.watch_on[k] === false)
      .map(([, l]) => h("p", { class: "note" }, "⏸ " + l + " Sozlamalarda butunlay o'chirilgan.")),
    h("p", { class: "hint" }, smart ? "🧠 Aqlli kuzatuv yoqilgan (AI mos postlarni tekshiradi). O'chirish: Hisob → Sozlamalar." : "Kalit so'z rejimi (AI'siz, bepul). Aqlli kuzatuvni Hisob → Sozlamalar da yoqasiz."),
    watch.watches.length ? watch.watches.map(card) : h("p", { class: "muted sm" }, "Kuzatuv yo'q. Telegram kanal (ish e'lonlari, narxlar, yangiliklar) yoki mahsulot narxini qo'shing. Chatda «@kanalni frontend so'zi bo'yicha kuzat» deb yozsangiz ham bo'ladi."),
    watch.hits.length ? h("p", { class: "kick sub" }, "Oxirgi topilganlar") : null,
    watch.hits.slice(0, 10).map((x) => h("div", { class: "prow" }, h("span", { class: "bul amber" }),
      h("div", { class: "grow" }, h("b", {}, x.watch), h("p", { class: "muted sm clampx" }, x.text)),
      x.url ? h("a", { class: "linkbtn", href: x.url, target: "_blank", rel: "noopener" }, "↗") : null)));
}
function drawPlans({ rems, plans, watch, smart }) {
  const tab = S.planTab, filter = S.planFilter;
  const seg = h("div", { class: "seg plans-seg" }, [["rems", "Eslatmalar"], ["plans", "Rejalar"], ["watch", "Kuzatuv"]].map(([k, l]) =>
    h("button", { class: tab === k ? "on" : "", onclick: () => { S.planTab = k; refresh(true); } }, l)));
  // --- eslatmalar ---
  const pending = rems.filter((r) => r.status === "pending"), past = rems.filter((r) => r.status !== "pending");
  const STAT = { sent: "yuborildi", cancelled: "bekor", failed: "xato" };
  const remRow = (r, isPast) => h("div", { class: "prow" + (isPast ? " past" : "") }, h("span", { class: "bul " + (isPast ? "" : "amber") }),
    h("div", { class: "grow" }, h("b", {}, r.text), r.call && !isPast ? h("p", { class: "muted xs" }, "📞 qo'ng'iroq bilan") : null),
    h("span", { class: "tag-s " + (isPast ? "" : "warn") }, isPast ? (STAT[r.status] || r.status) + " · " + r.local : r.local),
    isPast ? null : h("button", { class: "x", "aria-label": "Bekor qilish", onclick: async () => { try { await api("/reminders/" + r.id, { method: "DELETE" }); toast("Bekor qilindi"); refresh(true); } catch (e) { toast(e.message); } } }, "✕"));
  const remSec = h("section", { class: "plans-sec" + (tab === "rems" ? " on" : "") },
    h("div", { class: "row-b" }, h("p", { class: "kick" }, "Eslatmalar"), h("button", { class: "btn lime sm", onclick: reminderAdd }, "+ Eslatma")),
    pending.length ? pending.map((r) => remRow(r, false)) : h("p", { class: "muted sm" }, "Kutilayotgan eslatma yo'q. Chatda «ertaga 9 da … eslat» yoki «tel qilib eslat» deb yozing."),
    past.length ? h("p", { class: "kick sub" }, "Oldingilar") : null, past.slice(0, 10).map((r) => remRow(r, true)));
  // --- rejalar ---
  const chips = h("div", { class: "seg sm plans-filter" }, [["all", "Hammasi"], ...PERIODS].map(([k, l]) =>
    h("button", { class: filter === k ? "on" : "", onclick: () => { S.planFilter = k; refresh(true); } }, l)));
  const shown = plans.filter((p) => filter === "all" || p.period === filter);
  const toggle = async (p, idx) => {
    const items = p.items.map((it, i) => (i === idx ? { ...it, done: !it.done } : it));
    try { await post("/plans/" + p.id, { items }); refresh(true); } catch (e) { toast(e.message); }
  };
  const card = (p) => {
    const pct = p.total ? Math.round((p.done / p.total) * 100) : 0, fill = h("i"); fill.style.width = pct + "%";
    const per = (PERIODS.find((x) => x[0] === p.period) || [0, "Boshqa"])[1];
    return h("div", { class: "card plan" + (p.total && p.done === p.total ? " fin" : "") },
      h("div", { class: "row-b" }, h("div", { class: "grow" }, h("p", { class: "kick" }, per + (p.target ? " · " + p.target : "")), h("b", { class: "ptitle" }, p.title)),
        h("div", { class: "acts-i" }, h("button", { class: "linkbtn", onclick: () => planSheet(p) }, "✏️"),
          h("button", { class: "linkbtn", onclick: async () => { if (!confirm("Reja o'chirilsinmi?")) return; try { await api("/plans/" + p.id, { method: "DELETE" }); toast("O'chirildi"); refresh(true); } catch (e) { toast(e.message); } } }, "🗑"))),
      p.total ? h("div", { class: "bar" }, fill) : null,
      p.total ? h("p", { class: "muted xs" }, `${p.done}/${p.total} bajarildi`) : h("p", { class: "muted sm" }, "Bandlar yo'q. ✏️ bilan qo'shing."),
      p.items.map((it, i) => h("button", { class: "chk" + (it.done ? " done" : ""), onclick: () => toggle(p, i) }, h("i", {}, it.done ? "✓" : ""), h("span", {}, it.text))));
  };
  const planSec = h("section", { class: "plans-sec" + (tab === "plans" ? " on" : "") },
    h("div", { class: "row-b" }, h("p", { class: "kick" }, "Rejalar"), h("button", { class: "btn lime sm", onclick: () => planSheet() }, "+ Reja")), chips,
    shown.length ? shown.map(card) : h("p", { class: "muted sm" }, "Reja yo'q. «+ Reja» bilan qo'shing yoki chatda «bugungi rejam: …» deb yozing. Bu sizning shaxsiy rejalaringiz; AI vazifalarining rejasi esa vazifaning ichida ko'rinadi."));
  return [head("Eslatma va rejalar", liveTag()), seg, h("div", { class: "plans-wrap" }, remSec, planSec, watch ? watchSection({ watch, smart }, tab === "watch") : null)];
}
async function loadStats() {
  const [state, spend, mem, integ, loc] = await Promise.all([api("/state"), api("/spend"), api("/memory"), api("/integrations"), api("/location")]);
  const rems = [];
  S.state = state; renderTabs(); return { state, spend, mem, integ, loc, rems };
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
      h("p", { class: "muted" }, "Vidjet ish ketayotganda kim nima qilyapti, vazifa xarajati va qadamlarni; tinch paytda oxirgi natijalar, eslatmalar va moliyani ko'rsatadi (faqat o'qiydi). Kichik, o'rta, katta va qulf ekrani."),
      w.configured ? h("button", { class: "btn lime full", onclick: async () => {
        try { const r = await fetch("/api/widget-script", { headers: { Authorization: "Bearer " + S.token } }); if (!r.ok) throw new Error("Xatolik " + r.status); await copy(await r.text()); }
        catch (e) { toast(e.message); } } }, "📋 Tayyor skriptni nusxalash") : null,
      h("p", { class: "hint" }, "Scriptable → «AI Jamoa» skriptini oching → hammasini o'chirib, nusxalanganini qo'ying → saqlang. Manzil va kalit ichida tayyor. Qo'lda kiritish uchun pastdagilar:"),
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
// --- Yangilash: kuchli yangilash, yangi versiya aniqlash, pastga tortib yangilash ---
async function hardRefresh() {
  toast("Yangilanmoqda…");
  try { const regs = await navigator.serviceWorker.getRegistrations(); await Promise.all(regs.map((r) => r.unregister())); } catch (_) { /* SW yo'q */ }
  try { const ks = await caches.keys(); await Promise.all(ks.map((k) => caches.delete(k))); } catch (_) { /* kesh yo'q */ }
  location.replace(location.pathname + "?r=" + Date.now());   // kirish kaliti (localStorage) saqlanib qoladi
}
function showUpdate() {
  if (document.getElementById("updbar")) return;
  document.body.append(h("button", { id: "updbar", class: "updbar", onclick: hardRefresh }, "🔄 Yangi versiya bor. Yangilash"));
}
let lastUpdCheck = 0;
async function checkUpdate() {
  if (Date.now() - lastUpdCheck < 120000) return;
  lastUpdCheck = Date.now();
  try {
    const t = await (await fetch("/app.js", { cache: "no-store" })).text();
    const m = t.match(/PANEL_V = "([^"]+)"/);
    if (m && m[1] !== PANEL_V) showUpdate();
  } catch (_) { /* aloqa yo'q */ }
}
function setupPullToRefresh() {
  const v = $("view"); if (!v || !matchMedia("(pointer: coarse)").matches) return;
  const ind = h("div", { class: "ptr" }, "↓ Yangilash uchun torting"); document.body.append(ind);
  let y0 = 0, dy = 0, on = false;
  v.addEventListener("touchstart", (e) => { on = v.scrollTop <= 0 && !S.chatOpen && $("sheet").hidden; if (on) y0 = e.touches[0].clientY; }, { passive: true });
  v.addEventListener("touchmove", (e) => {
    if (!on) return; dy = e.touches[0].clientY - y0;
    if (dy > 12) { ind.style.opacity = Math.min(1, dy / 90); ind.style.transform = `translate(-50%, ${Math.min(dy, 120) / 2}px)`; ind.textContent = dy > 90 ? "↻ Qo'yib yuboring" : "↓ Yangilash uchun torting"; }
  }, { passive: true });
  v.addEventListener("touchend", () => { const go_ = on && dy > 90; on = false; dy = 0; ind.style.opacity = 0; if (go_) hardRefresh(); });
}

// --- Ertalabki xulosa: ob-havo, valyuta, bugungi eslatma va rejalar ---
function morningBlock(state) {
  const M = state.morning || { on: true, time: "08:00" };
  const save = async (d) => { try { await post("/morning", d); toast("Saqlandi"); refresh(true); } catch (e) { toast(e.message); } };
  const time = h("input", { type: "time", value: M.time, style: "max-width:140px" });
  time.addEventListener("change", () => save({ time: time.value }));
  const test = h("button", { class: "btn ghost" }, "☀️ Hozir yuborish (sinov)");
  test.onclick = async () => { test.disabled = true; try { await post("/morning/test"); toast("Yuborildi: chat va bildirishnomani ko'ring"); } catch (e) { toast(e.message); } test.disabled = false; };
  return [h("div", { class: "seg" },
      h("button", { class: M.on ? "on" : "", onclick: () => save({ on: true }) }, "Yoqilgan"),
      h("button", { class: M.on ? "" : "on", onclick: () => save({ on: false }) }, "O'chiq")),
    h("div", { class: "kv" }, h("span", {}, "Vaqti"), time),
    h("p", { class: "hint" }, "Har kuni shu vaqtda bildirishnoma keladi: ob-havo (uyingiz yoki oxirgi joylashuv bo'yicha), dollar/yevro/rubl kursi (Markaziy bank), bugungi eslatmalar va rejalar. AI ishlatilmaydi, pul sarflanmaydi."),
    h("div", { class: "acts" }, test)];
}

// --- Ovozni tanish zanjiri ---
function sttBlock(st) {
  if (!st) return [];
  const opts = [["auto", "Avto"], ["google", "Google"], ["azure", "Azure"], ["groq", "Groq"], ["whisper", "Whisper"], ["gemini", "Gemini"]];
  const save = async (m) => { try { await post("/stt", { mode: m }); toast("Ovozni tanish: " + opts.find((o) => o[0] === m)[1]); refresh(true); } catch (e) { toast(e.message); } };
  const rows = Object.entries(st.services).map(([k, v]) => h("div", { class: "kv" }, h("span", {}, (v.ready ? "✅ " : "⚪️ ") + v.name),
    h("span", { class: "muted" }, !v.ready ? (k === "google" ? "GOOGLE_STT_KEY yo'q" : k === "azure" ? "AZURE_SPEECH_KEY/REGION yo'q" : k === "groq" ? "GROQ_API_KEY yo'q yoki Sozlamalarda Groq o'chiq" : k === "whisper" ? "o'rnatilmagan" : "kalit yo'q")
      : v.free_min ? `${v.used_min} / ${v.free_min} daq (bu oy, bepul)` : v.used_min ? `${v.used_min} daq (bu oy)` : "tayyor")));
  const why = (k) => k === "google" ? "GOOGLE_STT_KEY yo'q" : k === "azure" ? "AZURE_SPEECH_KEY/REGION yo'q" : k === "groq" ? "GROQ_API_KEY yo'q yoki Sozlamalarda Groq o'chiq" : k === "whisper" ? "serverda o'rnatilmagan" : "kalit yo'q";
  const usable = (k) => k === "auto" || (st.services[k] || {}).ready;
  return [h("div", { class: "seg sm plans-filter" }, opts.map(([k, l]) => h("button", { class: (st.mode === k ? "on " : "") + (usable(k) ? "" : "off"),
      onclick: () => (usable(k) ? save(k) : toast(l + ": " + why(k))) }, l))),
    h("div", { class: "card" }, ...rows),
    h("p", { class: "hint" }, st.mode === "auto"
      ? "Avto: avval Google, bepul limitiga 1 daqiqa qolsa Azure, keyin serverdagi Whisper. Natijaning ishonch darajasi past bo'lsa (gap tushunilmagan bo'lishi mumkin), keyingisi, oxirida Gemini ma'noni tushunib yozadi."
      : "Tanlangan xizmat birinchi ishlatiladi; u ishlamasa yoki limit tugasa, qolganlari avto tartibda.")];
}

// --- Push-bildirishnomalar ---
const PUSH_KINDS = [["done", "✅ Vazifa tayyor bo'lganda"], ["failed", "⚠️ Vazifa bajarilmaganda"], ["approval", "🔐 Ruxsat so'ralganda"], ["reminder", "⏰ Eslatma vaqtida"], ["morning", "☀️ Ertalabki xulosa"], ["watch", "🔔 Kuzatuv topganda"], ["auto", "🤖 Avtonom agent ogohlantirsa"], ["progress", "⚙️ Vazifa jarayoni (qulf ekranida, bitta yangilanib turadi)"]];
let pushOn = null;  // shu qurilmada obuna bormi (null = hali bilmaymiz)
const b64u = (s) => Uint8Array.from(atob((s + "=".repeat((4 - s.length % 4) % 4)).replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
async function pushSub() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return null;
  const reg = await navigator.serviceWorker.ready;
  return reg.pushManager.getSubscription();
}
async function pushEnable() {
  if (!("Notification" in window) || !("PushManager" in window)) throw new Error("Bu brauzer bildirishnomani qo'llamaydi. iPhone'da avval ilovani «Bosh ekranga qo'shish» qiling.");
  const perm = await Notification.requestPermission();
  if (perm !== "granted") throw new Error("Ruxsat berilmadi. Sozlamalardan bildirishnomaga ruxsat bering.");
  const { key } = await api("/push/key");
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64u(key) });
  await post("/push/subscribe", { subscription: sub.toJSON() });
}
async function pushDisable() {
  const sub = await pushSub();
  if (sub) { try { await post("/push/unsubscribe", { endpoint: sub.endpoint }); } catch (_) { /* server o'zi tozalaydi */ } await sub.unsubscribe(); }
}
function awakeSwitch() {
  let on = true; try { on = localStorage.getItem("awake") !== "0"; } catch { /* saqlash yopiq */ }
  const set = (v) => { try { localStorage.setItem("awake", v ? "1" : "0"); } catch { /* */ } if (!v) keepAwake(false); refresh(true); };
  return h("div", { class: "seg" }, h("button", { class: on ? "on" : "", onclick: () => set(true) }, "Yoqilgan"), h("button", { class: on ? "" : "on", onclick: () => set(false) }, "O'chiq"));
}
function pushBlock() {
  const P = (S.state && S.state.push) || {};
  if (!P.available) return [h("p", { class: "hint" }, "Serverda bildirishnoma moduli o'rnatilmagan (pywebpush). Serverni yangilang.")];
  if (pushOn === null) pushSub().then((s) => { pushOn = !!s; if (S.tab === "stats") refresh(true); }).catch(() => { pushOn = false; });
  const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  const ios = /iPhone|iPad/.test(navigator.userAgent);
  const act = async (fn, msg) => { try { await fn(); pushOn = null; toast(msg); refresh(true); } catch (e) { toast(e.message); } };
  const out = [];
  if (ios && !standalone) out.push(h("p", { class: "hint" }, "iPhone'da bildirishnoma faqat bosh ekranga o'rnatilgan ilovada ishlaydi: Ulashish → «Bosh ekranga qo'shish», keyin ilovani shu yerdan oching."));
  out.push(h("div", { class: "seg" },
    h("button", { class: pushOn ? "on" : "", onclick: () => act(pushEnable, "Bildirishnoma yoqildi") }, "Yoqilgan"),
    h("button", { class: pushOn ? "" : "on", onclick: () => act(pushDisable, "Bildirishnoma o'chirildi") }, "O'chiq")));
  if (pushOn) {
    out.push(h("div", { class: "card" }, PUSH_KINDS.map(([k, l]) => h("div", { class: "kv" }, h("span", {}, l),
      h("button", { class: "btn ghost", style: "padding:6px 14px", onclick: async () => { try { await post("/push/prefs", { [k]: !(P.prefs || {})[k] }); refresh(true); } catch (e) { toast(e.message); } } }, (P.prefs || {})[k] === false ? "O'chiq" : "Yoqilgan")))));
    out.push(h("div", { class: "acts" }, h("button", { class: "btn ghost", onclick: async () => { try { await post("/push/test"); toast("Sinov yuborildi"); } catch (e) { toast(e.message); } } }, "🔔 Sinov bildirishnomasi")));
  } else out.push(h("p", { class: "hint" }, "Yoqsangiz, ilova yopiq bo'lsa ham telefonga xabar keladi: vazifa tayyor, ruxsat kerak, eslatma."));
  return out;
}

const VOICES = [["jarvis", "🤵 Jarvis (tabiiy, yengil)"], ["jarvis2", "🤵 Jarvis 2 (yumshoq)"], ["sadaltager", "Sadaltager (bilimdon)"], ["algenib", "Algenib (xirillagan)"], ["orus", "Orus (aniq)"], ["kore", "Kore (ayol)"]];
function voiceSelect(cur) {
  const sel = h("select", {}, VOICES.map(([k, l]) => h("option", { value: k, selected: k === cur ? "selected" : null }, l)));
  sel.addEventListener("change", async () => { try { await post("/tts/voice", { voice: sel.value }); toast("Ovoz: " + sel.selectedOptions[0].textContent); } catch (e) { toast(e.message); } });
  const play = h("button", { class: "btn ghost sm", type: "button" }, "🔊 Eshitib ko'rish");
  play.onclick = async () => {
    play.disabled = true; play.textContent = "Tayyorlanmoqda…";
    try {
      const r = await fetch("/api/tts/preview", { method: "POST", headers: { Authorization: "Bearer " + S.token, "Content-Type": "application/json" }, body: JSON.stringify({ voice: sel.value }) });
      if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.error || "Xatolik " + r.status); }
      const a = new Audio(URL.createObjectURL(await r.blob())); await a.play();
    } catch (e) { toast(e.message); }
    play.disabled = false; play.textContent = "🔊 Eshitib ko'rish";
  };
  return h("div", { class: "acts" }, sel, play);
}
const TTS_MODES = [["auto", "Avto: Gemini, xato bo'lsa Edge"], ["edge", "Edge (bepul, tez), xato bo'lsa Gemini"], ["gemini", "Faqat Gemini"]];
function ttsModeSelect(T) {
  T = T || { mode: "auto", engines: {} };
  const E0 = T.engines || {};
  const okMode = (k) => k === "auto" ? (E0.gemini || {}).ready || (E0.edge || {}).ready : (E0[k] || {}).ready;
  const sel = h("select", {}, TTS_MODES.map(([k, l]) => h("option", { value: k, selected: k === T.mode ? "selected" : null, disabled: okMode(k) ? null : "disabled" }, l + (okMode(k) ? "" : " (mavjud emas)"))));
  sel.addEventListener("change", async () => { try { await post("/tts/mode", { mode: sel.value }); toast("Saqlandi"); setTimeout(tgSheet, 600); } catch (e) { toast(e.message); } });
  const E = T.engines || {}, name = { gemini: "Gemini", edge: "Edge" };
  const st = ["gemini", "edge"].map((k) => name[k] + ": " + (!(E[k] || {}).ready ? "yo'q" : E[k].cooldown_s ? "dam olyapti (" + E[k].cooldown_s + " s)" : "ishlayapti")).join(" · ");
  return h("div", {}, sel, h("p", { class: "hint" }, st + (T.last ? " · oxirgi: " + name[T.last] : "") + ". Xizmat xato bersa, tizim keyingisiga o'tadi va uni bir necha daqiqa chetda ushlaydi."));
}
function callBlock(t) {
  const C = (t && t.calls) || { available: false };
  const save = async (d) => { try { await post("/tg/calls", d); toast("Saqlandi"); setTimeout(tgSheet, 800); } catch (e) { toast(e.message); } };
  if (!C.available) return [h("div", { class: "label" }, "Ovozli qo'ng'iroq"), h("p", { class: "hint" }, "Serverda qo'ng'iroq moduli (py-tgcalls) o'rnatilmagan. Render'da qayta deploy qiling.")];
  const sw = (on, key) => h("div", { class: "seg" },
    h("button", { class: on ? "on" : "", onclick: () => save({ [key]: true }) }, "Yoqilgan"),
    h("button", { class: on ? "" : "on", onclick: () => save({ [key]: false }) }, "O'chiq"));
  const test = h("button", { class: "btn ghost" }, "📞 Hozir menga qo'ng'iroq qil");
  test.onclick = async () => { test.disabled = true; test.textContent = "Qo'ng'iroq qilinmoqda…"; try { await post("/tg/call_test", {}); toast("Qo'ng'iroq ketdi"); } catch (e) { toast(e.message); } test.disabled = false; test.textContent = "📞 Hozir menga qo'ng'iroq qil"; };
  return [h("div", { class: "label" }, "Ovozli qo'ng'iroq"), sw(C.enabled, "enabled"),
    h("p", { class: "hint" }, C.error ? "⚠️ " + C.error : !C.enabled ? "O'chiq." : C.ready ? "✅ Tayyor: agent akkauntiga qo'ng'iroq qilsangiz ko'taradi, hisobot va savollarga ovoz bilan javob beradi." : "Ishga tushmoqda…"),
    ...(C.timing && C.timing.stt != null ? [h("p", { class: "hint" }, "⏱ Oxirgi gap: eshitish " + C.timing.stt + " s · o'ylash " + C.timing.think + " s · ovoz " + (C.timing.tts || 0) + " s. Uzun bo'lsa: Ovoz manbai → Edge, Sozlamalar → Groq yoqing.")] : []),
    h("label", {}, "Ovoz"), voiceSelect(C.voice || "jarvis"),
    h("label", {}, "Ovoz manbai"), ttsModeSelect(C.tts),
    h("label", {}, "Vazifa tugaganda menga qo'ng'iroq qilib natijani aytsin"), sw(C.notify, "notify"),
    h("div", { class: "acts" }, test)];
}

async function tgSheet() {
  let t;
  try { t = (await api("/integrations")).telegram_account; } catch (e) { return toast(e.message); }
  const again = () => tgSheet();
  const field = (label, attrs) => { const i = h("input", attrs); return [h("label", {}, label), i, i]; };
  const run = (btn, fn, busy) => btn.addEventListener("click", async () => {
    const label = btn.textContent;
    btn.disabled = true; if (busy) btn.textContent = busy;
    try { await fn(); } catch (e) { toast(e.message); } finally { btn.disabled = false; btn.textContent = label; }
  });
  if (t.pending && t.login && t.login.qr) return tgQrSheet(false);
  if (t.pending) return tgCodeStep(again, run, field, t.login || {});
  if (t.configured) {
    const out = h("button", { class: "btn red full" }, "Akkauntni uzish");
    run(out, async () => { if (!confirm("Telegram akkaunt uziladi. Davom etasizmi?")) return; await post("/tg/logout"); closeSheet(); toast("Uzildi"); refresh(true); });
    const who = h("b", {}, t.me || "aniqlanmoqda...");
    api("/tg/me").then((r) => {
      if (r.stale) { toast("Akkaunt aslida ulanmagan edi: telefon raqami bilan kiring"); refresh(true); return again(); }
      who.textContent = r.me || "noma'lum";
    }).catch((e) => { who.textContent = "aniqlab bo'lmadi (" + e.message + ")"; });
    const MODES = [["read", "Faqat o'qish", "Chatlarni o'qiydi, qidiradi. Hech narsa yubormaydi."],
                   ["ask", "Tasdiq bilan", "Har bir xabar va amalni (guruh yaratish, o'chirish...) avval sizdan so'raydi (Kartalar/bot). Soatiga limit va ruxsat ro'yxati amal qiladi."],
                   ["full", "Cheklovsiz", "Tasdiqsiz, limitsiz: xabar/fayl yuborish, guruh/kanal yaratish, qo'shilish/chiqish, forward, o'chirish. Hamma amal jurnalga yoziladi."]];
    const setMode = async (m) => {
      if (m === "full" && !confirm("Cheklovsiz rejim: agent so'ralganda xabarlarni sizdan so'ramasdan yuboradi. Chatda kimdir yozgan matn orqali agentni aldab xabar yubortirishi ehtimoli bor. Yoqilsinmi?")) return;
      try { await post("/tg/access", { mode: m }); toast("Rejim: " + MODES.find((x) => x[0] === m)[1]); tgSheet(); refresh(true); } catch (e) { toast(e.message); }
    };
    const seg = h("div", { class: "seg" }, MODES.map(([k, name]) => h("button", { class: t.mode === k ? "on" : "", onclick: () => setMode(k) }, name)));
    const note = MODES.find((x) => x[0] === t.mode) || MODES[0];
    const L = t.listen || { enabled: false, owners: [], active: false, error: "" };
    const owners = h("input", { value: (L.owners || []).join(", "), placeholder: "masalan 8207311790", inputmode: "numeric", autocomplete: "off" });
    const saveL = async (data) => {
      try { await post("/tg/listen", data); toast("Saqlandi"); setTimeout(tgSheet, 1500); } catch (e) { toast(e.message); }
    };
    const lseg = h("div", { class: "seg" },
      h("button", { class: L.enabled ? "on" : "", onclick: () => saveL({ enabled: true }) }, "Yoqilgan"),
      h("button", { class: L.enabled ? "" : "on", onclick: () => saveL({ enabled: false }) }, "O'chiq"));
    const lstate = !L.enabled ? "O'chiq: agent akkaunti hech kimga javob bermaydi." :
      L.error ? "⚠️ " + L.error : L.active ? "✅ Ishlayapti: shu ID'lardan yozsangiz, bot kabi javob beradi." : "Ishga tushmoqda… (30 soniyagacha)";
    return openSheet(h("h2", {}, "Telegram akkaunt"), h("p", { class: "muted" }, "✅ Ulangan: ", who),
      h("div", { class: "label" }, "Bot o'rnida javob berish"), lseg, h("p", { class: "hint" }, lstate),
      h("label", {}, "Faqat shu chat ID'lardan yozilsa javob beradi"), owners,
      h("div", { class: "acts" }, h("button", { class: "btn ghost", onclick: () => saveL({ owners: owners.value }) }, "ID'larni saqlash")),
      h("p", { class: "hint" }, "Boshqa odam yoki guruh yozsa, umuman javob bermaydi. Bo'sh qoldirsangiz .env dagi OWNER_TELEGRAM_ID (sizning asosiy akkauntingiz) ishlatiladi."),
      ...callBlock(t),
      h("div", { class: "label" }, "Agentga ruxsat"), seg, h("p", { class: "hint" }, note[2]),
      h("p", { class: "hint" }, "Kirishni to'xtatish: Telegram → Sozlamalar → Qurilmalar."), h("div", { class: "label" }), out);
  }
  if (!t.keys) {
    const [l1, id] = field("api_id", { placeholder: "masalan 1234567", inputmode: "numeric" });
    const [l2, hash] = field("api_hash", { placeholder: "32 belgili kod", autocomplete: "off" });
    const go = h("button", { class: "btn lime full" }, "Saqlash");
    run(go, async () => { await post("/tg/keys", { api_id: id.value, api_hash: hash.value }); again(); });
    return openSheet(h("h2", {}, "Telegram akkaunt: 1/3"),
      h("p", { class: "muted" }, "Agent uchun ajratilgan (ortiqcha) akkauntni ulaymiz. Avval Telegramning rasmiy kalitlari kerak:"),
      h("p", { class: "hint" }, "1) my.telegram.org ga ortiqcha raqam bilan kiring → API development tools → ilova yarating. 2) Chiqqan api_id va api_hash ni shu yerga yozing."),
      l1, id, l2, hash, h("div", { class: "label" }), go);
  }
  if (!t.pending) {
    const [l, phone] = field("Telefon raqami", { placeholder: "+998901234567", inputmode: "tel", autocomplete: "off" });
    const [lx, proxy] = field("Proksi (ixtiyoriy)", { placeholder: t.proxy ? "saqlangan proksi ishlatiladi (o'chirish: bo'sh joy qoldiring)" : "tg://proxy?server=...&port=...&secret=...  yoki  socks5://host:port", autocomplete: "off" });
    const err = h("p", { class: "hint" });
    const go = h("button", { class: "btn lime full" }, "Kod yuborish");
    run(go, async () => {
      err.textContent = "";
      const data = { phone: phone.value };
      if (proxy.value.trim()) data.proxy = proxy.value.trim();
      else if (proxy.value) data.proxy = "";            // faqat bo'sh joy = saqlangan proksini o'chirish
      try { await post("/tg/code", data); } catch (e) { err.textContent = "⚠️ " + e.message; throw e; }
      again();
    }, "Telegramga ulanmoqda… (30 soniyagacha)");
    const qrBtn = h("button", { class: "btn outline full", onclick: () => tgQrSheet(true) }, "📷 QR-kod bilan kirish (kod/SMS shart emas)");
    return openSheet(h("h2", {}, "Telegram akkaunt: 2/3"),
      h("p", { class: "muted" }, "Eng oson yo'l: QR-kod. Ortiqcha akkaunt ochiq turgan telefon bilan skanerlaysiz."), qrBtn,
      h("div", { class: "label" }, "yoki telefon raqami + kod"),
      h("p", { class: "muted" }, "Ortiqcha akkaunt raqamini yozing. Telegram shu akkauntning Telegram ilovasiga (yoki SMS bilan) kod yuboradi."), l, phone,
      lx, proxy, h("p", { class: "hint" }, "Odatda bo'sh qoldiring. Faqat \"ulanib bo'lmadi\" xatosi chiqsa: Telegram ilovangiz Sozlamalar → Ma'lumotlar va xotira → Proksi dagi havolani shu yerga qo'ying."),
      err, h("div", { class: "label" }), go);
  }
  return tgCodeStep(again, run, field, t.login || {});
}
function tgCodeStep(again, run, field, info) {
  const [lc, code] = field("Telegramdan kelgan kod", { placeholder: "12345", inputmode: "numeric", autocomplete: "one-time-code" });
  const [lp, pw] = field("Ikki bosqichli parol (agar qo'ygan bo'lsangiz)", { type: "password", autocomplete: "off" });
  const go = h("button", { class: "btn lime full" }, "Kirish");
  run(go, async () => {
    const r = await post("/tg/verify", { code: code.value, password: pw.value });
    if (r.status === "password") { toast("Ikki bosqichli parolni kiriting"); return pw.focus(); }
    closeSheet(); toast("✅ Ulandi: " + r.me); refresh(true);
  }, "Tekshirilmoqda…");
  const resend = h("button", { class: "btn ghost" }, "✉️ Boshqa yo'l bilan yuborish" + (info.next ? " (" + info.next + ")" : ""));
  run(resend, async () => { const r = await post("/tg/resend"); toast("Qayta yuborildi: " + r.via); again(); }, "Yuborilmoqda…");
  openSheet(h("h2", {}, "Telegram akkaunt: 3/3"),
    h("p", { class: "muted" }, "📨 Kod yuborildi: ", h("b", {}, info.via || "Telegram ilovasiga")),
    h("p", { class: "hint" }, "Telegram ilovasiga desa: kod ortiqcha akkauntning o'zida, \"Telegram\" (ko'k belgili rasmiy) chatida bo'ladi. U akkaunt biror telefonda ochiq bo'lishi kerak. Kodni hech kimga bermang."),
    lc, code, lp, pw, h("div", { class: "label" }), go, info.next ? h("div", { class: "acts" }, resend) : null,
    h("div", { class: "acts" }, h("button", { class: "btn outline", onclick: () => tgQrSheet(true) }, "📷 Kod kelmayaptimi? QR bilan kiring")),
    h("div", { class: "acts" }, h("button", { class: "btn ghost", onclick: async () => { try { await post("/tg/logout"); again(); } catch (e) { toast(e.message); } } }, "↺ Boshqa raqam / qayta")));
}
let qrPoll = 0;
async function tgQrSheet(start) {
  const my = ++qrPoll;                                   // eski kuzatuvchi to'xtaydi
  const img = h("img", { alt: "QR", class: "qr" });
  const status = h("p", { class: "muted" }, "QR tayyorlanmoqda…");
  const pw = h("input", { type: "password", placeholder: "Ikki bosqichli parol", autocomplete: "off", hidden: true });
  const pwBtn = h("button", { class: "btn lime full", hidden: true }, "Kirish");
  const retry = h("button", { class: "btn outline full", hidden: true, onclick: () => tgQrSheet(true) }, "↺ Yangi QR");
  pwBtn.addEventListener("click", async () => {
    pwBtn.disabled = true;
    try { const r = await post("/tg/verify", { password: pw.value }); closeSheet(); toast("✅ Ulandi: " + r.me); refresh(true); }
    catch (e) { toast(e.message); } finally { pwBtn.disabled = false; }
  });
  openSheet(h("h2", {}, "QR bilan kirish"),
    h("p", { class: "hint" }, "Ortiqcha akkaunt ochiq turgan telefonda: Telegram → Sozlamalar → Qurilmalar → «Kompyuterni ulash» (Link Desktop Device) → shu QR-kodni skanerlang."),
    img, status, pw, pwBtn, retry);
  const show = (st) => {
    if (st.svg) { img.src = st.svg; img.hidden = false; } else img.hidden = true;
    if (st.status === "waiting") status.textContent = "Skanerlashni kutyapman… (QR o'zi yangilanib turadi)";
    else if (st.status === "password") { status.textContent = "Skanerlandi ✓. Akkauntda ikki bosqichli parol bor: kiriting."; pw.hidden = pwBtn.hidden = false; }
    else if (st.status === "expired") { status.textContent = "Vaqt tugadi (5 daqiqa)."; retry.hidden = false; }
    else if (st.status === "error") { status.textContent = "⚠️ " + (st.error || "xato"); retry.hidden = false; }
  };
  let st;
  try { st = start ? await post("/tg/qr") : await api("/tg/qr"); } catch (e) { status.textContent = "⚠️ " + e.message; retry.hidden = false; return; }
  show(st);
  while (my === qrPoll && !$("sheet").hidden && st.status === "waiting") {
    await new Promise((r) => setTimeout(r, 2000));
    if (my !== qrPoll || $("sheet").hidden) return;
    try { st = await api("/tg/qr"); } catch (e) { continue; }
    if (st.status === "ok") { closeSheet(); toast("✅ Ulandi: " + (st.me || "")); refresh(true); return; }
    show(st);
  }
}
function reminderAdd() {
  const text = h("input", { placeholder: "Nimani eslatay? (masalan: Aliga qo'ng'iroq qilish)" });
  const when = h("input", { placeholder: "Qachon: 09:00 · ertaga 9:00 · 30 daq · 2026-10-09 18:30" });
  const btn = h("button", { class: "btn lime full" }, "Eslatma qo'yish");
  btn.addEventListener("click", async () => {
    try { const r = await post("/reminders", { text: text.value, when: when.value }); closeSheet(); toast("⏰ " + r.local); refresh(true); } catch (e) { toast(e.message); }
  });
  openSheet(h("h2", {}, "Eslatma"), h("p", { class: "muted" }, "Vaqti kelganda Telegram va panelga xabar keladi. Chatda «ertaga 9 da ... eslat» deb yozsangiz ham bo'ladi."),
    h("label", {}, "Matn"), text, h("label", {}, "Vaqt"), when, h("div", { class: "label" }), btn);
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
function drawStats({ state, spend, mem, integ, loc, rems }) {
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
  const FREE_AI = [["groq", "Groq", "juda tez; fon ishlari va ovozni tanish (Whisper)"], ["openrouter", "OpenRouter", "bepul modellar; faqat fon ishlari"]];
  const setFree = async (name, v) => { try { await post("/free_ai", { name, enabled: v }); toast((name === "groq" ? "Groq" : "OpenRouter") + (v ? " yoqildi" : " o'chirildi")); refresh(true); } catch (e) { toast(e.message); } };
  const freeRows = FREE_AI.map(([k, label, what]) => {
    const F = (integ.free_ai || {})[k] || {};
    return [h("div", { class: "label" }, "Bepul AI: " + label),
      h("div", { class: "seg" }, h("button", { class: (F.enabled ? "on " : "") + (F.ready ? "" : "off"), onclick: () => (F.ready ? setFree(k, true) : toast(label + ": " + k.toUpperCase() + "_API_KEY .env da yo'q")) }, "Yoqilgan"),
        h("button", { class: F.enabled ? "" : "on", onclick: () => setFree(k, false) }, "O'chiq")),
      h("p", { class: "hint" }, !F.ready ? "Kalit yo'q: .env ga " + k.toUpperCase() + "_API_KEY qo'ying. "
        : (F.enabled ? (F.cooldown_s ? "⏸ Limit/xato: " + F.cooldown_s + " s dam oladi, hozir keyingisi ishlaydi. " : "✅ Ishlayapti. ") : "O'chiq. ") + "Model: " + F.model + " (" + what + ").")];
  }).flat();
  const free = h("div", {}, ...freeRows,
    h("p", { class: "hint" }, "Bepul AI faqat oddiy, maxfiy bo'lmagan fon ishlari uchun (narx/kanal kuzatuvlari): Gemini/Claude byudjeti tejaladi. Suhbat, qo'ng'iroq, maxfiy yozishmalar va agent ishlariga ishlatilmaydi. Limit tugasa yoki xato bersa, o'zi keyingisiga (oxirida pulliga) o'tadi."));
  const ta = integ.telegram_account;
  const links = h("div", { class: "card" },
    ...[["Telegram bot", integ.telegram_bot, ""],
        ["Telegram akkaunt", ta.configured, ta.configured ? (ta.me ? ta.me + " · " : "") + ({ ask: "o'qish + yuborish (tasdiq bilan)", full: "CHEKLOVSIZ", read: "faqat o'qish" }[ta.mode] || "faqat o'qish") : "ulanmagan"],
        ["Xarita", true, integ.maps === "google" ? "Google (tirbandlik bilan)" : "OpenStreetMap (bepul, tirbandliksiz)"],
        ["Ovozni tushunish", integ.voice, integ.voice ? "Gemini" : "GEMINI_API_KEY kerak"],
        ["Veb-qidiruv", true, integ.search === "brave" ? "Brave" : "DuckDuckGo"],
        ["Maxfiy chat uchun AI", ta.private_providers.length > 0, ta.private_providers.length ? ta.private_providers.join(", ") : "cheklanmagan (PRIVATE_PROVIDERS)"],
       ].map(([name, ok, note]) => h("div", { class: "kv" }, h("span", {}, `${tick(ok)} ${name}`), h("span", { class: "muted" }, note))),
    h("div", { class: "acts" }, h("button", { class: "btn", onclick: () => tgSheet() }, ta.configured ? "📨 Telegram akkaunt" : "📨 Telegram akkauntni ulash")));
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
  const tab = S.statTab || "report";
  const seg = h("div", { class: "seg" }, [["report", "📊 Hisobot"], ["settings", "⚙️ Sozlamalar"]].map(([k, l]) =>
    h("button", { class: tab === k ? "on" : "", onclick: () => { S.statTab = k; $("view").scrollTop = 0; refresh(true); } }, l)));
  const ver = h("p", { class: "hint ver" }, "Versiya: " + (state.version || "?") + " · panel " + PANEL_V);
  if (tab === "report") {
    return [head("Hisob", liveTag()), seg,
      h("div", { class: "money" }, h("b", {}, usd(left)), h("span", {}, `qolgan byudjet · bugun ${usd(state.today)}`)), ...provs,
      h("div", { class: "acts" }, h("button", { class: "btn", onclick: showReport }, "📊 Kunlik hisobot"), h("button", { class: "btn", onclick: showAudit }, "🧾 Jurnal")),
      spend.length ? h("div", { class: "label" }, "Agentlar sarfi") : null,
      ...spend.slice(0, 8).map((r) => h("div", { class: "item" }, h("div", { class: "grow" }, h("h3", {}, r.agent)), h("span", { class: "muted" }, usd(r.cost)))),
      h("div", { class: "label" }, "Xotira"),
      ...mem.slice(0, 8).map((m) => h("div", { class: "item" }, h("div", { class: "grow" }, h("p", {}, (m.source === "owner-pref" ? "📌 " : "") + m.text)),
        h("button", { class: "btn red sm", onclick: () => delMem(m.id), "aria-label": "O'chirish" }, "✕"))),
      mem.length ? null : h("p", { class: "hint" }, "Hozircha bo'sh."),
      h("button", { class: "btn ghost full", onclick: memoryAdd }, "+ Xotiraga qo'shish"),
      h("p", { class: "hint" }, "📌 — doimiy qoidalaringiz. Eslatmalar endi «Rejalar» sahifasida."),
      h("div", { class: "label" }, "Boshqaruv"), pause, ver];
  }
  const checkOut = h("div", { class: "card checks" }, h("p", { class: "muted sm" }, "Hammasi bir bosishda tekshiriladi: baza, AI kalitlari, byudjet, Telegram, qo'ng'iroq, bildirishnoma, zaxira nusxa, disk. AI ishlatilmaydi."));
  const runCheck = async (btn) => {
    btn.disabled = true; btn.textContent = "Tekshirilmoqda…";
    try {
      const rows = await api("/selfcheck");
      checkOut.replaceChildren(...rows.map((r) => h("div", { class: "kv" },
        h("span", {}, (r.ok === true ? "✅ " : r.ok === false ? "❌ " : "⚪️ ") + r.name), h("span", { class: "muted" }, r.note))));
    } catch (e) { toast(e.message); }
    btn.disabled = false; btn.textContent = "🩺 Tizimni tekshirish";
  };
  const checkBtn = h("button", { class: "btn lime" }, "🩺 Tizimni tekshirish"); checkBtn.onclick = () => runCheck(checkBtn);
  return [head("Hisob", liveTag()), seg,
    h("div", { class: "label" }, "Tekshiruvlar"),
    h("div", { class: "acts" }, checkBtn, h("button", { class: "btn", onclick: showModels }, "🔎 Modellarni tekshirish")), checkOut,
    primary,
    free,
    h("div", { class: "label" }, "Xarajat"),
    h("div", { class: "seg" },
      [[true, "💰 Tejamkor"], [false, "💎 Sifat"]].map(([v, l]) => h("button", { class: (state.eco !== false) === v ? "on" : "", onclick: async () => {
        try { await post("/eco", { enabled: v }); toast(v ? "Tejamkor rejim" : "Sifat rejimi"); refresh(true); } catch (e) { toast(e.message); } } }, l))),
    h("p", { class: "hint" }, state.eco !== false
      ? "Reja va yakuniy qadoqlash arzon modelda, eng qimmat daraja ishlatilmaydi, bitta qadamli ishda qayta yozish yo'q. Odatda ~2 barobar arzon."
      : "Sifat rejimi: rahbar o'rta/kuchli modeldan foydalanadi, QA e'tirozida eng kuchli model qayta yozadi. Murakkab ishlar uchun."),
    h("div", { class: "label" }, "QA e'tirozlarini tuzatish"),
    h("div", { class: "seg" }, [1, 2, 3, 4, 6].map((n) => h("button", { class: (integ.limits && integ.limits.revisions) === n ? "on" : "",
      onclick: async () => { try { await post("/qa_rounds", { rounds: n }); toast("QA: " + n + " martagacha tuzatadi"); refresh(true); } catch (e) { toast(e.message); } } }, n + "x"))),
    h("p", { class: "hint" }, "QA e'tiroz bildirsa, ishni qilgan agent fayllarni tuzatadi, rahbar javobni qayta yig'adi va QA qayta tekshiradi: e'tiroz qolmaguncha (shu songacha). Bir xil e'tiroz takrorlansa (tuzatib bo'lmasa) erta to'xtaydi. Ko'proq = sifatliroq, lekin qimmatroq."),
    h("div", { class: "label" }, "Ovozli javob"),
    h("div", { class: "seg" }, [["mirror", "Ovozga ovoz bilan"], ["always", "Har doim"], ["off", "O'chiq"]].map(([k, l]) =>
      h("button", { class: (integ.voice_reply || "mirror") === k ? "on" : "", onclick: async () => { try { await post("/voice_reply", { mode: k }); toast("Ovozli javob: " + l); refresh(true); } catch (e) { toast(e.message); } } }, l))),
    h("p", { class: "hint" }, "Ovozli xabar yuborsangiz (Telegram yoki chatdagi mikrofon), javob matn bilan birga ovozli xabar bo'lib ham keladi. «Ovozli javob ber» deb yozsangiz ham. Ovoz qo'ng'iroqdagi bilan bir xil. Chatdagi har javob yonidagi 🔊 bilan istalganini tinglash mumkin."),
    h("div", { class: "label" }, "Ovozni tanish (ovoz → matn)"), ...sttBlock(integ.stt),
    h("div", { class: "label" }, "Bildirishnomalar (telefonga)"), ...pushBlock(),
    h("div", { class: "label" }, "Ish paytida ekran o'chmasin (shu qurilma)"), awakeSwitch(),
    h("p", { class: "hint" }, "Vazifa ishlayotganda va panel ochiq turganda ekran o'chib qolmaydi, tugagach odatdagidek o'chadi. Qulf ekranida jarayonni ko'rish uchun: Bildirishnomalar → «⚙️ Vazifa jarayoni» ni yoqing va Scriptable vidjetini qulf ekraniga qo'shing."),
    h("div", { class: "label" }, "Ertalabki xulosa"), ...morningBlock(state),
    h("div", { class: "label" }, "Kuzatuvlar"),
    ...[["tg", "📡 Telegram kanal kuzatuvi"], ["price", "🏷 Narx kuzatuvi"]].map(([k, l]) => {
      const onK = !state.watch_on || state.watch_on[k] !== false;
      return h("div", { class: "kv" }, h("span", {}, l), h("div", { class: "seg sm", style: "margin:0" },
        [[true, "Yoqilgan"], [false, "O'chiq"]].map(([v, t]) => h("button", { class: onK === v ? "on" : "", onclick: async () => {
          try { await post("/watch_kind", { kind: k, enabled: v }); toast(l + ": " + t); refresh(true); } catch (e) { toast(e.message); } } }, t))));
    }),
    h("p", { class: "hint" }, "O'chiq bo'lsa, shu turdagi birorta kuzatuv tekshirilmaydi va xabar kelmaydi. Ro'yxat saqlanib qoladi, qayta yoqsangiz davom etadi."),
    h("div", { class: "label" }, "Aqlli kuzatuv (AI)"),
    h("div", { class: "seg" }, [[true, "🧠 Yoqilgan"], [false, "O'chiq"]].map(([v, l]) => h("button", { class: !!state.watch_smart === v ? "on" : "", onclick: async () => {
      try { await post("/watch_smart", { enabled: v }); toast(v ? "Aqlli kuzatuv yoqildi" : "Faqat kalit so'z rejimi"); refresh(true); } catch (e) { toast(e.message); } } }, l))),
    h("p", { class: "hint" }, state.watch_smart
      ? "Kalit so'zga mos postlar (yoki kalit so'z berilmagan kuzatuvda yangi postlar) arzon AI bilan tekshiriladi. Narx tuzilgan ma'lumotda bo'lmasa, AI bir marta topadi. Taxminan oyiga $0.3–1."
      : "Faqat kalit so'z va sahifadagi tuzilgan narx: AI ishlatilmaydi, bepul."),
    h("div", { class: "label" }, "Bot xabarlari"),
    h("div", { class: "seg" },
      [["all", "Hammasi"], ["result", "Faqat natija"], ["off", "O'chiq"]].map(([v, l]) => h("button", { class: (state.bot_push || "all") === v ? "on" : "", onclick: async () => {
        try { await post("/bot_push", { mode: v }); toast("Bot: " + l); refresh(true); } catch (e) { toast(e.message); } } }, l))),
    h("p", { class: "hint" }, { all: "Bot jarayon xabarlari va natijani yuboradi.", result: "Bot faqat tugagan natijani yuboradi, jarayon xabarlari yo'q.", off: "Bot natijani o'zi yubormaydi, natija panelda. Unga yozib so'rasangiz javob beradi. Tasdiqlar va eslatmalar baribir keladi." }[state.bot_push || "all"]),
    h("div", { class: "label" }, "Ulanishlar"), links,
    h("div", { class: "label" }, "Joylashuv"), where,
    h("div", { class: "label" }, "Limitlar"), limits,
    h("div", { class: "label" }, "Ilova"),
    h("div", { class: "acts" }, h("button", { class: "btn", onclick: showWidget }, "📱 iPhone vidjeti"), h("button", { class: "btn ghost", onclick: hardRefresh }, "🔄 Kuchli yangilash")),
    h("p", { class: "hint" }, "Yangi versiya chiqqanda eski nusxa qolib ketsa, «Kuchli yangilash» ni bosing: kesh tozalanadi, kirish saqlanadi. Telefonda ekranni tepadan pastga tortsangiz ham yangilanadi."),
    h("div", { class: "label" }, "Telefonga o'rnatish"), installCard(),
    h("div", { class: "label" }),
    h("button", { class: "btn ghost full", onclick: () => { localStorage.removeItem("aij_token"); localStorage.removeItem("aij_cache"); S.token = ""; location.reload(); } }, "Chiqish"),
    ver, h("p", { class: "hint ver" }, viewportInfo())];
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
      try { await post("/tasks", { text: p.task, based_on: Number.isInteger(p.based_on) ? p.based_on : null }); btn.textContent = "Topshirildi ✓"; toast("Vazifa topshirildi"); }
      catch (e) { toast(e.message); btn.disabled = false; }
    });
    return h("div", { class: "msg proposal" }, h("div", { class: "q" }, "Taklif qilingan vazifa" + (p.based_on ? ` (#${p.based_on} ustida)` : "")), h("div", {}, p.task), btn);
  }
  if (m.role === "ceo") {
    const play = h("button", { class: "say-btn", type: "button", "aria-label": "Tinglash", title: "Ovozda tinglash" }, "🔊");
    play.onclick = () => sayText(m.text, play);
    return h("div", { class: "msg ceo" }, m.text, play);
  }
  return h("div", { class: "msg " + m.role }, m.text);
}
let sayAudio = null;
async function sayText(text, btn) {
  if (sayAudio) { sayAudio.pause(); sayAudio = null; }
  if (btn) { btn.disabled = true; btn.textContent = "…"; }
  try {
    const r = await fetch("/api/tts/say", { method: "POST", headers: { Authorization: "Bearer " + S.token, "Content-Type": "application/json" }, body: JSON.stringify({ text: text.slice(0, 4000) }) });
    if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.error || "Xatolik " + r.status); }
    sayAudio = new Audio(URL.createObjectURL(await r.blob()));
    await sayAudio.play();
  } catch (e) { if (btn) toast(e.message); }
  if (btn) { btn.disabled = false; btn.textContent = "🔊"; }
}
async function pollChat() {
  const rows = await api("/chat?after=" + S.lastChat);
  if (!rows.length) return;
  S.lastChat = rows[rows.length - 1].id; S.chat.push(...rows);
  S.typing = false;
  const reply = rows.find((m) => m.role === "ceo");
  if (S.speakNext && reply) { S.speakNext = false; sayText(reply.text); }   // ovoz bilan so'ralgan: javob o'zi o'qiladi
  if (S.chatOpen) {
    const box = $("msgs"), near = box.scrollHeight - box.scrollTop - box.clientHeight < 140;
    box.querySelectorAll(".typing").forEach((n) => n.remove());
    rows.forEach((m) => box.append(msgEl(m)));
    if (near) box.scrollTop = box.scrollHeight;
  }
}
function openChat(focus) {
  S.chatOpen = true; $("chat").hidden = false; $("chat-bg").hidden = false;
  const box = $("msgs"); box.replaceChildren();
  if (!S.chat.length) box.append(h("div", { class: "empty" }, "Rahbar bilan oddiy suhbat: savol bering, maslahatlashing. Ish topshirish uchun «+ Vazifa» tugmasi."));
  S.chat.forEach((m) => box.append(msgEl(m)));
  if (S.typing) box.append(h("div", { class: "typing" }, "Rahbar o'ylayapti…"));
  box.scrollTop = box.scrollHeight;
  if (focus) $("chat-input").focus();
}
function closeChat() { S.chatOpen = false; $("chat").hidden = true; $("chat-bg").hidden = true; refresh(true); }
$("chat-bg").addEventListener("click", closeChat);
document.addEventListener("keydown", (e) => {  // Esc: avval oyna, keyin chat yopiladi (kompyuterda qulay)
  if (e.key !== "Escape") return;
  if (!$("sheet").hidden) closeSheet(); else if (S.chatOpen) closeChat();
});
async function sendChat() {
  const ta = $("chat-input"), text = ta.value.trim(); if (!text) return;
  ta.value = ""; ta.style.height = "auto";
  try {
    const r = await post("/chat", { text, voice: !!S.dictated }); S.dictated = false; S.speakNext = !!(r && r.voice); S.typing = true;
    const box = $("msgs"); box.querySelector(".empty")?.remove(); box.append(h("div", { class: "typing" }, "Rahbar o'ylayapti…")); box.scrollTop = box.scrollHeight;
    await pollChat();
  } catch (e) { toast(e.message); ta.value = text; }
}
let rec = null;
function micState(on) { document.querySelectorAll(".mic-btn").forEach((b) => { b.classList.toggle("rec", on); b.setAttribute("aria-label", on ? "To'xtatish" : "Ovozli xabar"); }); }
// Ovoz yozadi, serverda matnga aylantiradi va onText(text) ni chaqiradi. Ikkinchi bosish yozuvni to'xtatadi.
async function voiceCapture(btn, onText) {
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
    btn.disabled = true; toast("Matnga aylantirilmoqda…");
    try {
      const r = await fetch("/api/voice", { method: "POST", headers: { Authorization: "Bearer " + S.token, "Content-Type": type }, body: new Blob(chunks, { type }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || "Xatolik " + r.status);
      onText(d.text);
    } catch (e) { toast(e.message); } finally { btn.disabled = false; }
  };
  rec.start(); micState(true);
}
function toggleMic() {
  voiceCapture($("chat-mic"), (text) => { S.dictated = true; const ta = $("chat-input"); ta.value = (ta.value ? ta.value + " " : "") + text; ta.dispatchEvent(new Event("input")); ta.focus(); });
}
$("chat-mic").classList.add("mic-btn");
$("chat-mic").append(svg(IC.mic));
$("chat-mic").addEventListener("click", toggleMic);
$("chat-back").addEventListener("click", closeChat);
$("chat-task").addEventListener("click", () => taskSheet());
$("chat-clear").addEventListener("click", async () => {
  if (!confirm("Suhbat tarixi tozalansinmi? (Vazifalar va natijalar o'chmaydi)")) return;
  try { await post("/chat/clear"); S.chat = []; S.lastChat = 0; openChat(false); toast("Suhbat tozalandi"); } catch (e) { toast(e.message); }
});
$("chat-send").append(svg(IC.send));
$("chat-send").addEventListener("click", sendChat);
$("chat-input").addEventListener("input", (e) => { const t = e.target; t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 140) + "px"; });
$("chat-input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !matchMedia("(pointer: coarse)").matches) { e.preventDefault(); sendChat(); } });

// ---------- so'rov va ishga tushirish ----------
let polling = false;
async function poll() {
  if (polling) return;  // oldingi so'rov tugamagan bo'lsa, ustiga yangisini yubormaymiz (sekin aloqada navbat to'planardi)
  polling = true;
  try {
    const chat = pollChat();  // chat va sahifa parallel yangilanadi (ketma-ket emas)
    if (S.chatOpen) { await chat; S.state = await api("/state"); $("chat-sub").textContent = S.state.paused ? "pauza" : S.state.working.length ? S.state.working.map((w) => w.agent).join(", ") + " ishlayapti" : "onlayn"; if (!S.state.working.length && !S.state.running_tasks.length) document.querySelectorAll(".typing").forEach((n) => n.remove()); }
    else await Promise.all([chat, refresh(false)]);
    saveCache();
    if (S.state && S.state.version) { if (!S.ver) S.ver = S.state.version; else if (S.ver !== S.state.version) showUpdate(); }
    checkUpdate();
    keepAwake(!!(S.state && (S.state.running_tasks.length || S.state.working.length)));
  } catch (e) { if (e.message !== "auth") toast("Aloqa yo'q…"); }
  finally { polling = false; }
}
// Vazifa ishlayotganda ekran o'chib qolmasin (panel ochiq turganda): Screen Wake Lock. Ish tugasa o'zi bo'shatiladi.
// iOS'da bosh ekranga qo'shilgan ilovada iOS 18.4+ da ishlaydi; qo'llamaydigan qurilmada jim o'tadi.
let wakeLock = null, wantAwake = false;
async function keepAwake(on) {
  wantAwake = on;
  try {
    if (on && !wakeLock && !document.hidden && "wakeLock" in navigator && localStorage.getItem("awake") !== "0") {
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener("release", () => { wakeLock = null; });
    } else if (!on && wakeLock) { await wakeLock.release(); wakeLock = null; }
  } catch { wakeLock = null; }
}
document.addEventListener("visibilitychange", () => { if (!document.hidden && wantAwake) keepAwake(true); });
function nextDelay() {  // ish bor yoki chat ochiq: tez; bo'sh turganda: sekinroq (server va bazaga yuk kamayadi)
  if (document.hidden) return 30000;
  const st = S.state;
  return S.chatOpen || S.typing || (st && (st.working.length || st.running_tasks.length || st.pending)) ? 3000 : 8000;
}
function start() {
  setupPullToRefresh();
  renderTabs(); poll().then(() => refresh(true)).then(prefetch);
  (async function loop() { for (;;) { await sleep(nextDelay()); await poll(); } })();
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
}
window.addEventListener("hashchange", () => location.reload());
function openDeepLink(url) {  // bildirishnomadan kelganda: kerakli vazifa yoki sahifa ochiladi
  try {
    const q = new URL(url, location.origin).searchParams;
    if (q.get("task")) { go("tasks"); openTask(Number(q.get("task"))); } else if (q.get("tab")) go(q.get("tab"));
  } catch (_) { /* noto'g'ri havola e'tiborsiz */ }
}
(async function init() {
  const m = location.hash.match(/token=([^&]+)/);
  if (m) { history.replaceState(null, "", location.pathname); S.token = decodeURIComponent(m[1]); }
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
    navigator.serviceWorker.addEventListener("message", (e) => { if (e.data && e.data.type === "open") openDeepLink(e.data.url); });
  }
  const cached = S.cache[S.tab];
  if (S.token && cached) {  // oldin kirilgan: kutmasdan oxirgi holat bilan ochamiz, server orqada tekshiriladi
    if (S.cache._chat) { S.chat = S.cache._chat; S.lastChat = S.chat.length ? S.chat[S.chat.length - 1].id : 0; }
    paint(S.tab, cached); start();
    if (location.search) setTimeout(() => { openDeepLink(location.href); history.replaceState(null, "", location.pathname); }, 600);
    return;
  }
  if (S.token && (await tryLogin(S.token))) start(); else showLogin();
  if (location.search) setTimeout(() => { openDeepLink(location.href); history.replaceState(null, "", location.pathname); }, 600);
})();
