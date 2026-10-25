"use strict";

const $ = (s, el = document) => el.querySelector(s);
const state = {
  status: null,
  holdings: [],
  stocks: {},        // symbol -> stock payload, or {error}
  pending: {},       // symbol -> promise
  ideas: null,       // [{symbol, because}]
  earnings: null,    // {upcoming, recent, errors}
  earnBySym: {},
};

// ------------------------------------------------------------------ utils

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
// numbers inside Hebrew sentences: isolate each one so it keeps its order
function bidiText(s) {
  const re = /(?<![A-Za-z\d])[-+]?\$?\d[\d.,]*%?[BM]?(?:-\d{2}-\d{2})?/g;
  let out = "", last = 0;
  for (const m of String(s ?? "").matchAll(re)) {
    out += esc(s.slice(last, m.index)) + `<bdi class="num">${esc(m[0])}</bdi>`;
    last = m.index + m[0].length;
  }
  return out + esc(String(s ?? "").slice(last));
}
const nf = (d = 2) => new Intl.NumberFormat("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
function fmt(v, d = 2) { return v == null || isNaN(v) ? "—" : nf(d).format(v); }
function money(v, cur = "USD", d = 2) {
  if (v == null || isNaN(v)) return "—";
  const sym = { USD: "$", ILS: "₪", ILA: "", EUR: "€", GBP: "£" }[cur] ?? "";
  return `<bdi class="num">${v < 0 ? "-" : ""}${sym}${nf(d).format(Math.abs(v))}${cur === "ILA" ? " אג'" : ""}</bdi>`;
}
function bn(v) {
  if (v == null || isNaN(v)) return "—";
  const a = Math.abs(v), [d, u] = a >= 1e9 ? [1e9, "B"] : a >= 1e6 ? [1e6, "M"] : [1, ""];
  return `<bdi class="num">${v < 0 ? "-" : ""}$${nf(2).format(a / d)}${u}</bdi>`;
}
function pct(v, d = 1, arrow = true) {
  if (v == null || isNaN(v)) return `<span class="muted">—</span>`;
  const cls = v > 0 ? "up" : v < 0 ? "down" : "";
  const a = !arrow ? "" : v > 0 ? "▲ " : v < 0 ? "▼ " : "";
  return `<bdi class="num ${cls}">${a}${v > 0 ? "+" : ""}${nf(d).format(v)}%</bdi>`;
}
function scoreHtml(sc, withLabel = false) {
  if (!sc || sc.value == null) return `<span class="muted">—</span>`;
  const cls = sc.value >= 70 ? "high" : sc.value >= 50 ? "mid" : "low";
  return `<span class="score ${cls}" title="${esc(sc.label)}"><bdi class="num">${sc.value}</bdi>
    <span class="bar"><i style="width:${sc.value}%"></i></span>${withLabel ? `<span class="lbl">${esc(sc.label)}</span>` : ""}</span>`;
}
const HE_DAYS = ["א'", "ב'", "ג'", "ד'", "ה'", "ו'", "ש'"];
function dayLabel(iso) {
  const d = new Date(iso + "T12:00:00");
  return `יום ${HE_DAYS[d.getDay()]} ${d.getDate()}.${d.getMonth() + 1}`;
}
function inDays(n) {
  if (n === 0) return "היום";
  if (n === 1) return "מחר";
  if (n < 0) return `לפני ${-n} ימים`;
  return `בעוד ${n} ימים`;
}
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), 3200);
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({ error: "תשובה לא תקינה מהשרת" }));
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body;
}

// at most 4 stock requests at a time
const queue = [];
let running = 0;
function limited(fn) {
  return new Promise((resolve, reject) => {
    queue.push({ fn, resolve, reject });
    pump();
  });
}
function pump() {
  while (running < 4 && queue.length) {
    const { fn, resolve, reject } = queue.shift();
    running++;
    fn().then(resolve, reject).finally(() => { running--; pump(); });
  }
}

function loadStock(sym) {
  if (state.stocks[sym]) return Promise.resolve(state.stocks[sym]);
  if (!state.pending[sym]) {
    state.pending[sym] = limited(() => api(`/api/stock?symbol=${encodeURIComponent(sym)}`))
      .catch(e => ({ symbol: sym, error: e.message }))
      .then(s => { state.stocks[sym] = s; delete state.pending[sym]; return s; });
  }
  return state.pending[sym];
}

// ------------------------------------------------------------------ sparkline

function sparkline(closes, w = 96, h = 28) {
  if (!closes || closes.length < 2) return "";
  const tail = closes.slice(-63);
  const lo = Math.min(...tail), hi = Math.max(...tail), span = hi - lo || 1;
  const pts = tail.map((c, i) => `${(i / (tail.length - 1)) * w},${h - 3 - ((c - lo) / span) * (h - 6)}`).join(" ");
  const up = tail[tail.length - 1] >= tail[0];
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true" style="direction:ltr">
    <polyline points="${pts}" fill="none" stroke="${up ? "var(--good)" : "var(--critical)"}" stroke-width="1.6"
      stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}

// ------------------------------------------------------------------ portfolio

async function loadPortfolio() {
  state.holdings = (await api("/api/portfolio")).holdings;
  renderPortfolio();
  await Promise.all(state.holdings.map(h => loadStock(h.symbol).then(renderPortfolio)));
}

async function savePortfolio() {
  state.holdings = (await api("/api/portfolio", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ holdings: state.holdings }),
  })).holdings;
  state.ideas = null;
  state.earnings = null;
  renderPortfolio();
}

function renderPortfolio() {
  const rows = $("#rows");
  $("#empty").hidden = state.holdings.length > 0;
  let value = 0, cost = 0, dayChange = 0, priced = 0;
  rows.innerHTML = state.holdings.map(h => {
    const s = state.stocks[h.symbol];
    const q = s && s.quote;
    const e = state.earnBySym[h.symbol];
    if (s && s.error) {
      return `<tr data-sym="${esc(h.symbol)}"><td class="stock-cell"><span class="s">${esc(h.symbol)}</span>
        <span class="n">${esc(h.name)}</span></td><td colspan="8" class="down">${esc(s.error)}</td>
        <td><button class="del" data-del="${esc(h.symbol)}" title="הסר">✕</button></td></tr>`;
    }
    const sk = `<span class="skel"></span>`;
    let val = null, pl = null;
    if (q) {
      if (h.shares) {
        val = h.shares * q.price;
        value += val; priced++;
        if (q.change_pct != null) dayChange += val - val / (1 + q.change_pct / 100);
        if (h.cost) { pl = (q.price / h.cost - 1) * 100; cost += h.shares * h.cost; }
      }
    }
    const earn = e ? `<span class="chip ${e.days <= 14 ? "soon" : ""} ${e.estimated ? "est" : ""}"
      title="${esc(e.source)}${e.estimated ? " (הערכה)" : ""}">${dayLabel(e.date)} · ${inDays(e.days)}</span>`
      : state.earnings ? `<span class="muted">—</span>` : sk;
    return `<tr data-sym="${esc(h.symbol)}">
      <td class="stock-cell"><span class="s">${esc(h.symbol)}</span><span class="n">${esc(q ? q.name : h.name)}</span></td>
      <td>${q ? money(q.price, q.currency) : sk}</td>
      <td>${q ? pct(q.change_pct, 2) : sk}</td>
      <td class="hide-sm">${s ? sparkline(s.series.closes) : sk}</td>
      <td><input class="qty-input" type="number" min="0" step="any" value="${h.shares ?? ""}" data-qty="${esc(h.symbol)}" aria-label="כמות"></td>
      <td>${val != null ? money(val, q.currency, 0) : q ? `<span class="muted">—</span>` : sk}</td>
      <td>${pl != null ? pct(pl) : `<span class="muted">—</span>`}</td>
      <td>${s ? scoreHtml(s.score) : sk}</td>
      <td class="hide-sm">${earn}</td>
      <td><button class="del" data-del="${esc(h.symbol)}" title="הסר מהתיק">✕</button></td>
    </tr>`;
  }).join("");
  renderTiles(value, cost, dayChange, priced);
}

function renderTiles(value, cost, dayChange, priced) {
  const soon = (state.earnings?.upcoming || []).filter(e => e.held && e.days <= 14);
  const cur = mainCurrency();
  const pl = cost ? value - costOfPriced() : null;
  $("#tiles").innerHTML = `
    <div class="tile"><div class="k">שווי התיק</div><div class="v">${priced ? money(value, cur, 0) : "—"}</div>
      <div class="d">${state.holdings.length} מניות</div></div>
    <div class="tile"><div class="k">שינוי יומי</div><div class="v">${priced ? money(dayChange, cur, 0) : "—"}</div>
      <div class="d">${priced && value ? pct(dayChange / (value - dayChange) * 100, 2) : ""}</div></div>
    <div class="tile"><div class="k">רווח/הפסד</div><div class="v">${pl != null ? money(pl, cur, 0) : "—"}</div>
      <div class="d">${pl != null ? pct(pl / costOfPriced() * 100) : "הזן מחיר קנייה כדי לראות"}</div></div>
    <div class="tile"><div class="k">דוחות ב-14 הימים הקרובים</div><div class="v"><bdi class="num">${state.earnings ? soon.length : "…"}</bdi></div>
      <div class="d">${soon.slice(0, 4).map(e => `<bdi class="num">${esc(e.symbol)}</bdi>`).join(" · ")}</div></div>`;
}

function costOfPriced() {
  return state.holdings.reduce((a, h) => {
    const q = state.stocks[h.symbol]?.quote;
    return a + (q && h.shares && h.cost ? h.shares * h.cost : 0);
  }, 0);
}
function mainCurrency() {
  const c = state.holdings.map(h => state.stocks[h.symbol]?.quote?.currency).filter(Boolean);
  return c.length && c.every(x => x === c[0]) ? c[0] : "USD";
}

$("#rows").addEventListener("click", e => {
  const del = e.target.closest("[data-del]");
  if (del) {
    e.stopPropagation();
    const sym = del.dataset.del;
    state.holdings = state.holdings.filter(h => h.symbol !== sym);
    savePortfolio().then(() => toast(`${sym} הוסרה מהתיק`));
    return;
  }
  if (e.target.closest("input")) return;
  const tr = e.target.closest("tr[data-sym]");
  if (tr) openDrawer(tr.dataset.sym);
});
$("#rows").addEventListener("change", e => {
  const inp = e.target.closest("[data-qty]");
  if (!inp) return;
  const h = state.holdings.find(x => x.symbol === inp.dataset.qty);
  if (h) { h.shares = inp.value === "" ? null : Number(inp.value); savePortfolio(); }
});

// ------------------------------------------------------------------ search / add

let picked = null, searchSeq = 0, activeIdx = -1, lastResults = [];
const searchEl = $("#search"), resultsEl = $("#results");

searchEl.addEventListener("input", () => {
  clearTimeout(searchEl._t);
  const q = searchEl.value.trim();
  if (!q) { resultsEl.hidden = true; return; }
  searchEl._t = setTimeout(() => runSearch(q), 220);
});
searchEl.addEventListener("keydown", e => {
  if (resultsEl.hidden) return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    activeIdx = (activeIdx + (e.key === "ArrowDown" ? 1 : -1) + lastResults.length) % lastResults.length;
    [...resultsEl.children].forEach((li, i) => li.setAttribute("aria-selected", i === activeIdx));
  } else if (e.key === "Enter") {
    e.preventDefault();
    pick(lastResults[Math.max(activeIdx, 0)]);
  } else if (e.key === "Escape") {
    resultsEl.hidden = true;
  }
});
document.addEventListener("click", e => { if (!e.target.closest(".search-wrap")) resultsEl.hidden = true; });

async function runSearch(q) {
  const seq = ++searchSeq;
  let res;
  try { res = (await api(`/api/search?q=${encodeURIComponent(q)}`)).results; }
  catch (e) { res = []; }
  if (seq !== searchSeq) return;
  // a typed symbol nobody listed can still be added as is
  if (/^[A-Za-z0-9.\-^=]{1,12}$/.test(q) && !res.some(r => r.symbol.toUpperCase() === q.toUpperCase())) {
    res.push({ symbol: q.toUpperCase(), name: "הוסף לפי סימול", exchange: "" });
  }
  lastResults = res;
  activeIdx = -1;
  resultsEl.innerHTML = res.length ? res.map((r, i) => `<li role="option" data-i="${i}">
      <span class="sym">${esc(r.symbol)}</span><span class="nm">${esc(r.name)}</span><span class="ex">${esc(r.exchange)}</span></li>`).join("")
    : `<li class="muted">לא נמצאו תוצאות</li>`;
  resultsEl.hidden = false;
}
resultsEl.addEventListener("click", e => {
  const li = e.target.closest("li[data-i]");
  if (li) pick(lastResults[+li.dataset.i]);
});

function pick(r) {
  if (!r) return;
  if (state.holdings.some(h => h.symbol === r.symbol.toUpperCase())) {
    toast(`${r.symbol} כבר בתיק`);
    resultsEl.hidden = true;
    return;
  }
  picked = r;
  resultsEl.hidden = true;
  $("#pick-sym").textContent = r.symbol;
  $("#pick-name").textContent = r.name === "הוסף לפי סימול" ? "" : r.name;
  $("#add-form").hidden = false;
  $("#pick-shares").focus();
}
$("#pick-cancel").addEventListener("click", () => { $("#add-form").hidden = true; picked = null; });
$("#add-form").addEventListener("submit", async e => {
  e.preventDefault();
  if (!picked) return;
  const sym = picked.symbol.toUpperCase();
  const s = await loadStock(sym);
  if (s.error) { delete state.stocks[sym]; toast(`לא נמצאו נתונים ל-${sym}: ${s.error}`); return; }
  state.holdings.push({
    symbol: sym, name: s.quote.name || picked.name,
    shares: $("#pick-shares").value === "" ? null : Number($("#pick-shares").value),
    cost: $("#pick-cost").value === "" ? null : Number($("#pick-cost").value),
  });
  await savePortfolio();
  toast(`${sym} נוספה לתיק`);
  $("#add-form").hidden = true;
  $("#add-form").reset();
  searchEl.value = "";
  picked = null;
  loadEarnings();
});

async function addToPortfolio(sym) {
  if (state.holdings.some(h => h.symbol === sym)) return;
  const s = await loadStock(sym);
  state.holdings.push({ symbol: sym, name: s.quote?.name || sym, shares: null, cost: null });
  await savePortfolio();
  toast(`${sym} נוספה לתיק. אפשר לעדכן כמות בטבלה`);
  loadEarnings();
}

// ------------------------------------------------------------------ ideas

let ideasPromise = null;
function loadIdeas() {
  if (state.ideas) { renderIdeas(); return Promise.resolve(); }
  if (!ideasPromise) {
    $("#ideas").innerHTML = `<p class="muted">מחפש מניות דומות…</p>`;
    ideasPromise = api("/api/ideas").then(r => {
      state.ideas = r.ideas;
      renderIdeas();
      return Promise.all(state.ideas.map(i => loadStock(i.symbol).then(renderIdeas)));
    }).finally(() => { ideasPromise = null; });
  }
  return ideasPromise;
}

function renderIdeas() {
  const el = $("#ideas");
  if (!state.ideas) return;
  if (!state.ideas.length) {
    el.innerHTML = `<p class="muted">${state.holdings.length ? "לא נמצאו מניות דומות." : "הוסף מניות לתיק כדי לקבל רעיונות."}</p>`;
    return;
  }
  const sort = $("#idea-sort").value, onlyHigh = $("#idea-high").checked;
  let list = state.ideas.map(i => ({ ...i, s: state.stocks[i.symbol] })).filter(i => !(i.s && i.s.error));
  if (onlyHigh) list = list.filter(i => i.s?.score?.value >= 70);
  const key = {
    score: i => i.s?.score?.value ?? -1,
    votes: i => i.because.length * 1000 + (i.s?.score?.value ?? 0),
    momentum: i => i.s?.quote?.ret_3m ?? -999,
  }[sort];
  list.sort((a, b) => key(b) - key(a));
  el.innerHTML = list.map(i => {
    const q = i.s?.quote, sc = i.s?.score, e = state.earnBySym[i.symbol];
    return `<article class="idea" data-sym="${esc(i.symbol)}" tabindex="0">
      <div class="row">
        <div style="min-width:0"><div class="s">${esc(i.symbol)}</div><div class="n">${esc(q?.name || "")}</div></div>
        <div class="price">${q ? money(q.price, q.currency) : `<span class="skel"></span>`}<div>${q ? pct(q.change_pct, 2) : ""}</div></div>
      </div>
      <div class="row" style="align-items:center">${i.s ? sparkline(i.s.series.closes, 140, 34) : ""}${scoreHtml(sc, true)}</div>
      <div class="tags">${(sc?.tags || []).map(t => `<span class="chip">${esc(t)}</span>`).join("")}
        ${e ? `<span class="chip soon">דוח ${inDays(e.days)}</span>` : ""}</div>
      <div class="because">דומה ל: <bdi class="num">${i.because.map(esc).join(", ")}</bdi>
        ${q ? ` · 3 חודשים ${pct(q.ret_3m, 1, false)}` : ""}</div>
    </article>`;
  }).join("") || `<p class="muted">אין רעיונות שעומדים בסינון.</p>`;
}
$("#ideas").addEventListener("click", e => {
  const c = e.target.closest(".idea");
  if (c) openDrawer(c.dataset.sym);
});
$("#ideas").addEventListener("keydown", e => {
  const c = e.target.closest(".idea");
  if (c && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openDrawer(c.dataset.sym); }
});
$("#idea-sort").addEventListener("change", renderIdeas);
$("#idea-high").addEventListener("change", renderIdeas);

// ------------------------------------------------------------------ earnings

async function loadEarnings() {
  const extra = (state.ideas || []).map(i => i.symbol).join(",");
  try {
    state.earnings = await api(`/api/earnings?extra=${encodeURIComponent(extra)}`);
  } catch (e) {
    state.earnings = { upcoming: [], recent: [], errors: [e.message] };
  }
  state.earnBySym = {};
  for (const r of state.earnings.upcoming) state.earnBySym[r.symbol] = r;
  renderEarnings();
  renderPortfolio();
  renderIdeas();
}

function renderEarnings() {
  const E = state.earnings;
  if (!E) { $("#upcoming").innerHTML = `<p class="muted">טוען לוח דוחות…</p>`; return; }
  const byDay = {};
  for (const r of E.upcoming) (byDay[r.date] ||= []).push(r);
  const days = Object.keys(byDay).sort();
  $("#upcoming").innerHTML = days.length ? days.map(d => `
    <div class="day">
      <div class="when"><b>${dayLabel(d)}</b><span>${inDays(byDay[d][0].days)}</span></div>
      <div class="items">${byDay[d].map(r => `
        <button class="earn ${r.held ? "held" : ""}" data-sym="${esc(r.symbol)}" title="${esc(r.source)}">
          <span class="s">${esc(r.symbol)}</span>
          <span class="t">${esc(r.time || "")}${r.eps_forecast ? ` · צפי EPS <bdi class="num">${esc(r.eps_forecast)}</bdi>` : ""}</span>
          ${r.held ? `<span class="chip">בתיק</span>` : ""}${r.estimated ? `<span class="chip est">הערכה</span>` : ""}
        </button>`).join("")}</div>
    </div>`).join("")
    : `<p class="muted">אין דוחות ב-4 השבועות הקרובים למניות שלך.</p>`;
  $("#recent-wrap").hidden = !E.recent.length;
  $("#recent").innerHTML = E.recent.map(r => `
    <button class="earn" data-sym="${esc(r.symbol)}"><span class="s">${esc(r.symbol)}</span>
      <span class="t">${esc(r.form)} לתקופה שהסתיימה ב-<bdi class="num">${esc(r.period)}</bdi>, הוגש <bdi class="num">${esc(r.filed)}</bdi></span></button>`).join("");
  const errs = E.errors || [];
  $("#earn-errors").hidden = !errs.length;
  $("#earn-errors").textContent = errs.length ? "חלק מלוח הדוחות לא נטען: " + errs.join(" · ") : "";
}
$("#tab-earnings").addEventListener("click", e => {
  const b = e.target.closest(".earn");
  if (b) openDrawer(b.dataset.sym, "analysis");
});

// ------------------------------------------------------------------ drawer

let drawerSym = null;

async function openDrawer(sym, focus) {
  drawerSym = sym;
  $("#drawer-body").innerHTML = `<p class="muted">טוען ${esc(sym)}…</p>`;
  $("#drawer").classList.add("open");
  $("#drawer").setAttribute("aria-hidden", "false");
  $("#scrim").hidden = false;
  const s = await loadStock(sym);
  if (drawerSym !== sym) return;
  if (s.error) { $("#drawer-body").innerHTML = `<p class="down">${esc(s.error)}</p>`; return; }
  renderDrawer(s);
  const sec = $("#d-analysis");
  try {
    const a = await api(`/api/analysis?symbol=${encodeURIComponent(sym)}`);
    if (drawerSym === sym) sec.innerHTML = analysisHtml(a);
  } catch (e) {
    if (drawerSym === sym) sec.innerHTML = `<h3>ניתוח דוחות</h3><p class="muted">${esc(e.message)}</p>`;
  }
  if (focus === "analysis") sec.scrollIntoView({ behavior: "smooth", block: "start" });
}
function closeDrawer() {
  drawerSym = null;
  $("#drawer").classList.remove("open");
  $("#drawer").setAttribute("aria-hidden", "true");
  $("#scrim").hidden = true;
  $("#tooltip").hidden = true;
}
$("#drawer-close").addEventListener("click", closeDrawer);
$("#scrim").addEventListener("click", closeDrawer);
document.addEventListener("keydown", e => { if (e.key === "Escape" && drawerSym) closeDrawer(); });

function renderDrawer(s) {
  const q = s.quote, sc = s.score, e = state.earnBySym[s.symbol];
  const held = state.holdings.some(h => h.symbol === s.symbol);
  const pos = q.high_52w > q.low_52w ? (q.price - q.low_52w) / (q.high_52w - q.low_52w) * 100 : 50;
  $("#drawer-body").innerHTML = `
    <div class="d-head">
      <div><span class="s">${esc(s.symbol)}</span><div class="n">${esc(q.name)} · ${esc(q.exchange)}</div></div>
      <div><div class="p">${money(q.price, q.currency)}</div><div style="text-align:end">${pct(q.change_pct, 2)} היום</div></div>
    </div>
    <div class="links" style="margin:0 0 12px">
      ${held ? `<span class="chip">בתיק</span>` : `<button class="btn primary small" id="d-add">הוסף לתיק</button>`}
      ${e ? `<span class="chip soon">דוח הבא: ${dayLabel(e.date)} · ${inDays(e.days)}${e.time ? " · " + esc(e.time) : ""}${e.estimated ? " (הערכה)" : ""}</span>` : ""}
    </div>
    <section class="d-sec">
      <h3><span>מחיר</span>
        <span class="range-btns" role="group" aria-label="טווח">
          <button data-range="63" aria-pressed="false">3ח'</button>
          <button data-range="126" aria-pressed="false">6ח'</button>
          <button data-range="260" aria-pressed="true">שנה</button>
        </span></h3>
      <div class="legend"><span>מחיר סגירה</span><span class="ref">ממוצע 50 יום</span></div>
      <div class="chart" id="d-chart"></div>
    </section>
    <section class="d-sec">
      <h3>נתונים</h3>
      <div class="metrics">
        ${metric("תשואה חודש", pct(q.ret_1m))}${metric("תשואה 3 חודשים", pct(q.ret_3m))}${metric("תשואה שנה", pct(q.ret_1y))}
        ${metric("ממוצע 50 יום", money(q.ma50, q.currency))}${metric("ממוצע 200 יום", money(q.ma200, q.currency))}
        ${metric("RSI 14", `<bdi class="num">${fmt(q.rsi14, 0)}</bdi>`)}
        ${metric("תנודתיות שנתית", q.volatility != null ? `<bdi class="num">${fmt(q.volatility, 0)}%</bdi>` : "—")}
        ${metric("מהשיא השנתי", pct(q.off_high_pct, 1, false))}
        ${metric("ימי מסחר בנתונים", `<bdi class="num">${q.bars}</bdi>`)}
      </div>
      <div style="margin-top:14px">
        <div class="k muted" style="font-size:12px">טווח 52 שבועות</div>
        <div class="range" style="direction:ltr"><i style="left:calc(${Math.max(0, Math.min(100, pos))}% - 6px);transform:none"></i></div>
        <div class="range-l"><span>${fmt(q.low_52w)}</span><span>${fmt(q.high_52w)}</span></div>
      </div>
    </section>
    <section class="d-sec">
      <h3><span>ציון פוטנציאל</span>${scoreHtml(sc, true)}</h3>
      <div class="parts">${sc.parts.map(p => `
        <div class="part"><span>${esc(p.label)}</span>
          <span class="pb"><i style="width:${p.max ? p.points / p.max * 100 : 0}%"></i></span>
          <span class="pp num">${fmt(p.points, p.points % 1 ? 1 : 0)}/${p.max}</span>
          <span class="pd">${bidiText(p.detail)}</span></div>`).join("")}</div>
      <p class="muted" style="font-size:12px;margin-top:10px">
        ${sc.with_fundamentals ? "הציון כולל צמיחה מדוחות ה-SEC." : "בלי נתוני צמיחה: " + esc(s.fundamentals_error || "אין דוחות SEC") + ". הציון מנורמל לפי מה שיש."}
        סכום של כללים פשוטים, לא תחזית.</p>
    </section>
    <section class="d-sec" id="d-analysis"><h3>ניתוח דוחות</h3><p class="muted">טוען דוחות מ-SEC…</p></section>`;
  const btn = $("#d-add");
  if (btn) btn.addEventListener("click", () => addToPortfolio(s.symbol).then(() => renderDrawer(s)));
  const draw = n => {
    document.querySelectorAll(".range-btns button").forEach(b => b.setAttribute("aria-pressed", b.dataset.range == n));
    drawChart($("#d-chart"), s.series, +n, q.currency);
  };
  document.querySelectorAll(".range-btns button").forEach(b => b.addEventListener("click", () => draw(b.dataset.range)));
  draw(260);
}
const metric = (k, v) => `<div class="metric"><div class="k">${k}</div><div class="v">${v}</div></div>`;

function analysisHtml(a) {
  const rows = a.rows.slice(0, 6);
  const f = a.filings || {};
  const links = [];
  if (f.earnings_8k) links.push(`<a href="${esc(f.earnings_8k.exhibit || f.earnings_8k.url)}" target="_blank" rel="noopener">הודעת התוצאות (8-K, ${esc(f.earnings_8k.filed)})</a>`);
  if (f.report) links.push(`<a href="${esc(f.report.url)}" target="_blank" rel="noopener">${esc(f.report.form)} האחרון (${esc(f.report.filed)})</a>`);
  return `<h3><span>ניתוח דוחות</span><span class="muted" style="font-size:12px;font-weight:400">${esc(a.entity)} · SEC</span></h3>
    <ul class="bullets">${a.summary.map(b => `<li>${bidiText(b)}</li>`).join("")}</ul>
    ${a.stale ? `<p class="note" style="margin-top:10px">${esc(a.stale)}</p>` : ""}
    ${a.ttm && a.ttm.revenue ? `<p class="muted" style="margin-top:10px">12 חודשים אחרונים: הכנסות ${bn(a.ttm.revenue)},
      רווח נקי ${bn(a.ttm.net_income)}, EPS ${money(a.ttm.eps_diluted)}</p>` : ""}
    <div class="table-wrap" style="margin-top:10px"><table class="q-table">
      <thead><tr><th>רבעון</th><th>הכנסות</th><th>שינוי שנתי</th><th>שולי תפעולי</th><th>רווח נקי</th><th>EPS</th><th>שינוי EPS</th></tr></thead>
      <tbody>${rows.map(r => `<tr>
        <td><bdi class="num">${esc(r.fiscal.trim() || r.end)}</bdi>${r.derived ? ` <span class="chip" title="רבעון רביעי מחושב מהדוח השנתי">מחושב</span>` : ""}</td>
        <td>${bn(r.revenue)}</td>
        <td>${pct(r.revenue_yoy, 1, false)}</td>
        <td><bdi class="num">${r.operating_margin != null ? fmt(r.operating_margin, 1) + "%" : "—"}</bdi></td>
        <td>${bn(r.net_income)}</td>
        <td>${money(r.eps_diluted)}</td>
        <td>${pct(r.eps_yoy, 1, false)}</td></tr>`).join("")}</tbody></table></div>
    <div class="links">${links.join("")}</div>`;
}

// ------------------------------------------------------------------ price chart

function drawChart(el, series, n, cur) {
  const closes = series.closes, ts = series.ts;
  const ma = closes.map((_, i) => i >= 49 ? closes.slice(i - 49, i + 1).reduce((a, b) => a + b, 0) / 50 : null);
  const start = Math.max(0, closes.length - n);
  const C = closes.slice(start), M = ma.slice(start), T = ts.slice(start);
  const W = 660, H = 240, pl = 8, pr = 52, pt = 10, pb = 24;
  const vals = C.concat(M.filter(v => v != null));
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
  const x = i => pl + (i / Math.max(1, C.length - 1)) * (W - pl - pr);
  const y = v => pt + (1 - (v - lo) / (hi - lo)) * (H - pt - pb);
  const line = arr => arr.map((v, i) => v == null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`).filter(Boolean).join(" ");
  const ticks = niceTicks(lo, hi, 4);
  const dfmt = t => { const d = new Date(t * 1000); return `${d.getDate()}.${d.getMonth() + 1}`; };
  const xt = [0, Math.floor((C.length - 1) / 2), C.length - 1];
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" style="direction:ltr" role="img" aria-label="גרף מחיר">
    <g class="grid">${ticks.map(t => `<line x1="${pl}" x2="${W - pr}" y1="${y(t)}" y2="${y(t)}"/>`).join("")}</g>
    <g class="axis">${ticks.map(t => `<text x="${W - pr + 6}" y="${y(t) + 4}">${fmt(t, t >= 100 ? 0 : 2)}</text>`).join("")}
      ${xt.map(i => `<text x="${x(i)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : i === C.length - 1 ? "end" : "middle"}">${dfmt(T[i])}</text>`).join("")}</g>
    <polyline points="${line(M)}" fill="none" stroke="var(--series-ref)" stroke-width="1.5" stroke-dasharray="4 3"/>
    <polyline points="${line(C)}" fill="none" stroke="var(--series-1)" stroke-width="2" stroke-linejoin="round"/>
    <line id="xh" y1="${pt}" y2="${H - pb}" stroke="var(--text-muted)" stroke-width="1" visibility="hidden"/>
    <circle id="xd" r="4" fill="var(--series-1)" stroke="var(--surface-1)" stroke-width="2" visibility="hidden"/>
    <rect x="${pl}" y="0" width="${W - pl - pr}" height="${H}" fill="transparent" id="hit"/>
  </svg>`;
  const svg = el.querySelector("svg"), tip = $("#tooltip");
  const xh = svg.querySelector("#xh"), xd = svg.querySelector("#xd");
  svg.querySelector("#hit").addEventListener("pointermove", ev => {
    const r = svg.getBoundingClientRect();
    const px = (ev.clientX - r.left) / r.width * W;
    const i = Math.max(0, Math.min(C.length - 1, Math.round((px - pl) / (W - pl - pr) * (C.length - 1))));
    xh.setAttribute("x1", x(i)); xh.setAttribute("x2", x(i)); xh.setAttribute("visibility", "visible");
    xd.setAttribute("cx", x(i)); xd.setAttribute("cy", y(C[i])); xd.setAttribute("visibility", "visible");
    const d = new Date(T[i] * 1000);
    tip.innerHTML = `<div class="muted" style="margin:0">${d.getDate()}.${d.getMonth() + 1}.${d.getFullYear()}</div>
      <div>מחיר: <b>${money(C[i], cur)}</b></div>${M[i] != null ? `<div>ממוצע 50: ${money(M[i], cur)}</div>` : ""}`;
    tip.hidden = false;
    const tw = tip.offsetWidth;
    tip.style.left = Math.min(window.innerWidth - tw - 8, Math.max(8, ev.clientX + 14)) + "px";
    tip.style.top = (ev.clientY - 60) + "px";
  });
  svg.querySelector("#hit").addEventListener("pointerleave", () => {
    tip.hidden = true; xh.setAttribute("visibility", "hidden"); xd.setAttribute("visibility", "hidden");
  });
}

function niceTicks(lo, hi, n) {
  const step0 = (hi - lo) / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= step0) || step0;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) out.push(+v.toFixed(6));
  return out;
}

// ------------------------------------------------------------------ tabs, theme, boot

function showTab(name) {
  document.querySelectorAll(".tabs button").forEach(b => b.setAttribute("aria-selected", b.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach(p => (p.hidden = p.id !== "tab-" + name));
  if (name === "ideas") loadIdeas().catch(e => ($("#ideas").innerHTML = `<p class="down">${esc(e.message)}</p>`));
  if (name === "earnings") {
    renderEarnings();
    if (!state.earnings) loadEarnings();
  }
  try { localStorage.setItem("radar-tab", name); } catch (e) { /* storage off */ }
}
document.querySelectorAll(".tabs button").forEach(b => b.addEventListener("click", () => showTab(b.dataset.tab)));

function applyTheme(t) {
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
}
$("#theme").addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  const t = dark ? "light" : "dark";
  applyTheme(t);
  try { localStorage.setItem("radar-theme", t); } catch (e) { /* storage off */ }
});

(async function boot() {
  try { applyTheme(localStorage.getItem("radar-theme")); } catch (e) { /* storage off */ }
  try {
    state.status = await api("/api/status");
    const m = $("#mode");
    m.textContent = state.status.mode === "demo" ? `דמו · נתונים מ-${state.status.today}` : "נתונים חיים";
    m.classList.toggle("demo", state.status.mode === "demo");
  } catch (e) { toast("השרת לא זמין"); }
  let tab = "portfolio";
  try { tab = localStorage.getItem("radar-tab") || tab; } catch (e) { /* storage off */ }
  await loadPortfolio();
  showTab(tab);
  // ideas first, so the calendar can include them
  if (!state.ideas) await loadIdeas().catch(() => {});
  if (!state.earnings) loadEarnings();
})();
