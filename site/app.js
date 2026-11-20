/* GoalSeeker front end. Reads data/app.json and data/backtest.json; computes nothing that
   isn't derivable from them. Every probability is shown next to the price it was compared with. */
"use strict";

const S = { data: null, bt: null, expanded: new Set(), likely: { minOdds: null, minAgree: "", market: "", shown: 40 }, filters: { league: "", country: "", date: "", minEdge: "", market: "", oddsMin: "", oddsMax: "" }, picksShown: 100 };
const SLIP_KEY = "goalseeker.slip.v1";

// ---------------------------------------------------------------- helpers
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  setAttrs(el, attrs);
  append(el, kids);
  return el;
}
function sv(tag, attrs, ...kids) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  setAttrs(el, attrs);
  append(el, kids);
  return el;
}
function setAttrs(el, attrs) {
  if (!attrs) return;
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.setAttribute("class", v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
}
function append(el, kids) {
  for (const c of kids.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}
const t = (iso) => new Date(iso).getTime();
const pct = (p, d = 1) => (p == null ? "–" : (p * 100).toFixed(d) + "%");
const pts = (e, d = 1) => (e == null ? "–" : (e >= 0 ? "+" : "−") + Math.abs(e * 100).toFixed(d) + " pts");
const sgnPct = (x, d = 1) => (x == null ? "–" : (x >= 0 ? "+" : "−") + Math.abs(x * 100).toFixed(d) + "%");
const odds = (x) => (x == null ? "–" : Number(x).toFixed(2));
const units = (u, d = 2) => (u == null ? "–" : (u < 0 ? "−" : "") + Math.abs(u).toFixed(d) + " u");
const sgnUnits = (u, d = 2) => (u == null ? "–" : (u >= 0 ? "+" : "−") + Math.abs(u).toFixed(d) + " u");
const intl = (n) => Number(n).toLocaleString();
const fmtDay = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short" });
const fmtTime = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
const fmtFull = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const fmtDate = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });
const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
const dayKey = (iso) => { const d = new Date(iso); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
function ago(iso) {
  const s = (Date.now() - t(iso)) / 1000;
  if (s < 90) return "just now";
  if (s < 5400) return Math.round(s / 60) + " min ago";
  if (s < 172800) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " days ago";
}
function rel(iso) {
  const m = (t(iso) - nowRef()) / 60000;
  if (m < 0) return "kicked off";
  if (m < 90) return `in ${Math.round(m)} min`;
  if (m < 48 * 60) return `in ${Math.floor(m / 60)}h ${Math.round(m % 60)}m`;
  return `in ${Math.round(m / 1440)} days`;
}
function nowRef() {
  // A preview build is a snapshot of a past moment: measure "today" from when it was generated.
  return S.data && S.data.preview_note ? t(S.data.generated_at) : Date.now();
}
function marketLabel(key, fx) {
  const m = key.match(/^ou_([\d.]+)_(over|under)$/);
  if (m) return `${m[2] === "over" ? "Over" : "Under"} ${m[1]}`;
  if (key === "1x2_home") return fx ? `${fx.home} win` : "Home win";
  if (key === "1x2_draw") return "Draw";
  if (key === "1x2_away") return fx ? `${fx.away} win` : "Away win";
  if (key === "btts_yes") return "Both teams score";
  if (key === "btts_no") return "Not both teams score";
  return key;
}
const candKey = (c) => (c.market === "ou" ? `ou_${c.line}_${c.selection}` : `${c.market}_${c.selection}`);
const candLabel = (c, fx) => marketLabel(candKey(c), fx);
const bkName = (code) => (S.data.bookmakers && S.data.bookmakers[code]) || code;
const cfg = () => S.data.config;

function loadSlip() { try { return JSON.parse(localStorage.getItem(SLIP_KEY) || "[]"); } catch (e) { return []; } }
function saveSlip(s) { try { localStorage.setItem(SLIP_KEY, JSON.stringify(s)); } catch (e) { /* storage unavailable: slip lasts this visit */ } }
let SLIP = loadSlip();

// ---------------------------------------------------------------- data access
let picksById = new Map(), boardById = new Map();
function index() {
  picksById = new Map(S.data.picks.map((p) => [p.pick_id, p]));
  boardById = new Map(S.data.board.fixtures.map((e) => [e.fixture.fixture_id, e]));
}
function latestPrice(entry, pick) {
  if (!entry) return null;
  const pr = entry.prices.find((p) => p.bookmaker === pick.bookmaker && p.market === pick.market && (p.line ?? null) === (pick.line ?? null));
  return pr ? { price: pr.prices[pick.selection], at: pr.fetched_at } : null;
}

// ---------------------------------------------------------------- chrome
function renderChrome(route) {
  const tabs = document.getElementById("tabs");
  tabs.replaceChildren();
  const todayCount = todayPicks().length;
  const items = [["today", "Today", todayCount], ["likely", "Most likely"], ["week", "Next 7 days"], ["slip", "My slip", SLIP.length || null], ["record", "Record"], ["config", "Settings"]];
  for (const [id, label, count] of items) {
    tabs.append(h("a", { href: `#${id}`, "aria-current": route === id ? "page" : null }, label, count ? h("span", { class: "count" }, count) : null));
  }
  const st = S.data.status;
  const fresh = document.getElementById("fresh");
  const jobs = Object.values(st.jobs || {});
  const failing = jobs.filter((j) => j.status === "error");
  const priceAt = st.source_state && st.source_state.fixtures_csv_changed_at;
  fresh.replaceChildren(
    h("span", { class: "dot" + (failing.length ? " err" : "") }),
    failing.length
      ? h("span", null, `Last sync had an error (${failing[0].job}) · `, h("a", { href: "#config" }, "details"))
      : h("span", null, S.data.preview_note ? `Snapshot of ${fmtFull.format(new Date(S.data.generated_at))}` : `Updated ${ago(S.data.generated_at)}`),
    priceAt ? h("span", { class: "muted" }, `· prices published ${fmtFull.format(new Date(priceAt))}`) : null,
  );
  const banners = document.getElementById("banners");
  banners.replaceChildren();
  if (S.data.preview_note) banners.append(h("div", { class: "banner" }, h("span", { class: "ico" }, "i"), h("div", null, h("strong", null, "Preview. "), S.data.preview_note)));
  if (route === "today" || route === "week" || route === "likely") {
    const b = backtestBanner();
    if (b) banners.append(b);
  }
}
function backtestBanner() {
  const bt = S.bt;
  if (!bt || !bt.overall || !bt.overall.n) return null;
  const o = bt.overall;
  const live = S.data.record.overall;
  const losing = o.roi_level < 0;
  return h("div", { class: "banner " + (losing ? "warn" : "") },
    h("span", { class: "ico" }, "!"),
    h("div", null,
      h("strong", null, `Backtest ${bt.period.from.slice(0, 4)}–${bt.period.to.slice(0, 4)}: `),
      `${intl(o.n)} picks under these exact rules returned ${sgnPct(o.roi_level)} per pick at level stakes `,
      `(model expected ${Math.round(o.expected_wins)} winners, got ${o.wins}). `,
      losing ? "The model has not beaten the bookmakers' prices. Treat picks as paper trades until the live record says otherwise. " : "Past results; the live record is what counts. ",
      h("a", { href: "#record" }, live.n ? `Live record: ${live.n} settled` : "Live record: none settled yet"), "."));
}

// ---------------------------------------------------------------- shared pieces
function badge(status) {
  const g = { won: "✓", lost: "✕", void: "–", pending: "…" }[status] || "";
  const label = { won: "Won", lost: "Lost", void: "Void", pending: "Pending" }[status] || status;
  return h("span", { class: `badge ${status}` }, h("span", { class: "glyph", "aria-hidden": "true" }, g), label);
}

function pickCard(p, entry) {
  const fx = p.fixture;
  const id = p.pick_id;
  const open = S.expanded.has(id);
  const lp = latestPrice(entry, p);
  const moved = lp && lp.price && Math.abs(lp.price - p.odds_at_pick) > 1e-9;
  const inSlip = SLIP.some((s) => s.pick_id === id);
  const c = cfg();
  const card = h("article", { class: "card" },
    h("div", { class: "meta" }, h("span", null, `${fx.country} · ${fx.league}`), h("span", null, "·"),
      h("time", { datetime: fx.kickoff_utc }, fmtFull.format(new Date(fx.kickoff_utc))), h("span", { class: "rel" }, rel(fx.kickoff_utc))),
    h("h3", { class: "teams" }, fx.home, h("span", { class: "v" }, "v"), fx.away),
    h("div", { class: "bet" }, h("span", { class: "sel" }, p.label), h("span", { class: "at" }, "@"),
      h("span", { class: "price" }, odds(p.odds_at_pick)), h("span", { class: "bk" }, p.bookmaker_name),
      p.status !== "pending" ? badge(p.status) : null,
      p.status !== "pending" && p.ft_home != null ? h("span", { class: "muted small" }, `FT ${p.ft_home}–${p.ft_away}`) : null),
    h("dl", { class: "nums" },
      h("div", null, h("dt", null, "Model"), h("dd", null, pct(p.model_probability), h("small", null, `range ${pct(p.model_p10, 0)}–${pct(p.model_p90, 0)}`))),
      h("div", null, h("dt", null, "Bookmaker fair"), h("dd", null, pct(p.implied_probability), h("small", null, `1/price ${pct(p.raw_implied_probability)} · margin ${pct(p.overround)}`))),
      h("div", { class: "edge" }, h("dt", null, "Edge"), h("dd", null, pts(p.edge), h("small", null, `EV ${sgnPct(p.ev)} at ${odds(p.effective_odds)}`))),
      h("div", null, h("dt", null, "Stake"), h("dd", null, units(p.stake_units), h("small", null, `${fraction(p.thresholds.kelly_fraction)} Kelly, cap ${pct(p.thresholds.max_stake_pct, 0)}`))),
      h("div", null, h("dt", null, "Confidence"), h("dd", null, p.confidence, h("small", null, `+EV in ${pct(p.prob_positive_ev, 0)} of model draws`)))),
    h("p", { class: "stamp" }, `Price collected ${fmtFull.format(new Date(p.odds_fetched_at))}`,
      !moved && entry && entry.listed_at_source_until && lp && t(entry.listed_at_source_until) > t(lp.at) + 60e3
        ? `, unchanged at the source's last check (${fmtFull.format(new Date(entry.listed_at_source_until))}). ` : ". ",
      moved ? h("span", { class: "moved" }, `Latest ${p.bookmaker_name} price: ${odds(lp.price)} (${fmtFull.format(new Date(lp.at))}). `) : null,
      p.bookmaker === "BFE" ? `Exchange price shown before ${pct(c.betfair_commission, 0)} commission. ` : null,
      "Re-check your own bookmaker before betting."),
    entry && entry.context && entry.context.length ? contextBox(entry.context) : null,
    h("div", { class: "actions" },
      h("button", { class: "btn primary", disabled: inSlip || p.status !== "pending", onclick: () => { addToSlip(p); route(); } }, inSlip ? "In slip" : "Add to slip"),
      h("button", { class: "btn", "aria-expanded": String(open), onclick: () => { toggle(id); } }, open ? "Hide details" : "Details")),
    open ? detailPanel(entry, p) : null);
  return card;
}
function fraction(f) { return { 0.25: "¼", 0.5: "½", 0.125: "⅛", 1: "Full" }[f] || `${f}×`; }
function contextBox(notes) {
  return h("div", { class: "context" }, h("span", { class: "lbl" }, "Context · from stored results · never used in the numbers"),
    h("ul", null, notes.map((n) => h("li", null, n))));
}
function toggle(id) { if (S.expanded.has(id)) S.expanded.delete(id); else S.expanded.add(id); route(); }

function detailPanel(entry, pick) {
  if (!entry || !entry.model) {
    return h("div", { class: "detail" }, h("div", { class: "full muted" },
      entry ? `No model numbers for this fixture: ${entry.reason_text}${entry.reason_detail ? " (" + entry.reason_detail + ")" : ""}.` : "This fixture is no longer in the 7-day window, so only the stored pick snapshot is shown above."));
  }
  const fx = entry.fixture, m = entry.model;
  const tg = m.total_goals;
  const maxP = Math.max(...tg);
  const dist = h("div", { class: "dist", role: "table", "aria-label": "Total goals distribution" },
    tg.map((p, i) => h("div", { class: "row", role: "row" }, h("span", { class: "num", role: "cell" }, i === 6 ? "6+" : String(i)),
      h("span", { class: "bar", role: "cell", "aria-hidden": "true" }, h("span", { style: `width:${(p / maxP) * 100}%` })),
      h("span", { class: "num", role: "cell", style: "text-align:right" }, pct(p)))));
  // priced selections per market key
  const priced = new Map();
  for (const pr of entry.prices) {
    for (const [sel, price] of Object.entries(pr.prices)) {
      const key = pr.market === "ou" ? `ou_${pr.line}_${sel}` : `${pr.market}_${sel}`;
      if (!priced.has(key)) priced.set(key, []);
      priced.get(key).push({ bk: pr.bookmaker, price, at: pr.fetched_at, all: pr.prices });
    }
  }
  const keys = ["ou_0.5_over", "ou_0.5_under", "ou_1.5_over", "ou_1.5_under", "ou_2.5_over", "ou_2.5_under", "ou_3.5_over", "ou_3.5_under", "1x2_home", "1x2_draw", "1x2_away"];
  const rows = [];
  for (const k of keys) {
    const mk = m.markets[k];
    if (!mk) continue;
    const ps = priced.get(k) || [];
    if (!ps.length) {
      rows.push(h("tr", { class: "dim" }, h("td", null, marketLabel(k, fx)), h("td", { class: "n" }, pct(mk.p)), h("td", { colspan: "4" }, "No bookmaker price published, so it can't be compared or picked")));
      continue;
    }
    for (const x of ps) {
      const inv = Object.fromEntries(Object.entries(x.all).map(([s, v]) => [s, 1 / v]));
      const sum = Object.values(inv).reduce((a, b) => a + b, 0);
      const sel = k.split("_").pop();
      const fair = inv[sel] / sum;
      rows.push(h("tr", null, h("td", null, marketLabel(k, fx)), h("td", { class: "n" }, pct(mk.p), h("span", { class: "muted" }, ` (${pct(mk.p10, 0)}–${pct(mk.p90, 0)})`)),
        h("td", null, bkName(x.bk)), h("td", { class: "n" }, odds(x.price)), h("td", { class: "n" }, pct(fair)), h("td", { class: "n" }, pts(mk.p - fair))));
    }
  }
  const failNote = (c) => (c.fail ? { no_edge: "no edge", edge_too_large: "edge too large", odds_too_short: "odds too short", prob_below_floor: "below probability floor" }[c.fail] || c.fail : "qualifies");
  return h("div", { class: "detail" },
    h("div", null, h("h4", null, "Total goals (model)"), dist,
      h("p", { class: "xg" }, `Expected goals: ${fx.home} ${m.xg_home.toFixed(2)} (${m.xg_home_range[0].toFixed(2)}–${m.xg_home_range[1].toFixed(2)}), ${fx.away} ${m.xg_away.toFixed(2)} (${m.xg_away_range[0].toFixed(2)}–${m.xg_away_range[1].toFixed(2)}). Ranges are the model's 10th–90th percentile.`)),
    h("div", null, h("h4", null, "Check your bookmaker's price"), checker(entry),
      entry.candidates.length ? h("div", { style: "margin-top:14px" }, h("h4", null, "Every price the rules looked at"),
        h("table", null, h("thead", null, h("tr", null, h("th", null, "Bet"), h("th", null, "Book"), h("th", { class: "n" }, "Price"), h("th", { class: "n" }, "Edge"), h("th", null, "Verdict"))),
          h("tbody", null, entry.candidates.map((c) => h("tr", { class: c.fail ? "dim" : "" }, h("td", null, candLabel(c, fx)), h("td", null, bkName(c.bookmaker)),
            h("td", { class: "n" }, odds(c.price)), h("td", { class: "n" }, pts(c.edge)), h("td", null, failNote(c))))))) : null),
    h("div", { class: "full tablewrap" }, h("h4", null, "Model vs every published price"),
      h("table", null, h("thead", null, h("tr", null, h("th", null, "Selection"), h("th", { class: "n" }, "Model (10–90%)"), h("th", null, "Bookmaker"), h("th", { class: "n" }, "Price"), h("th", { class: "n" }, "Fair prob."), h("th", { class: "n" }, "Edge"))),
        h("tbody", null, rows))),
    h("p", { class: "full trace" }, "Trace: fixture ", h("code", null, fx.fixture_id), ` from ${fx.source}`,
      pick ? [" · odds snapshot ", h("code", null, pick.odds_snapshot_id), " · model run ", h("code", null, pick.model_run_id), ` (ratings as of ${pick.fit_asof}, data ${pick.fit_data_hash}) · settings `, h("code", null, pick.config_hash), " · pick ", h("code", null, pick.pick_id)] : null,
      entry.prices.length ? ` · prices fetched ${entry.prices.map((p) => fmtFull.format(new Date(p.fetched_at)))[0]}` : ""));
}

function checker(entry) {
  const fx = entry.fixture, m = entry.model;
  const keys = Object.keys(m.markets).filter((k) => !k.startsWith("btts"));
  const sel = h("select", { "aria-label": "Selection" }, keys.map((k) => h("option", { value: k, selected: k === "ou_2.5_over" }, marketLabel(k, fx))));
  const inp = h("input", { type: "number", step: "0.01", min: "1.01", placeholder: "e.g. 1.95", "aria-label": "Decimal price" });
  const out = h("div", { class: "checker-out muted" }, "Enter the decimal price your bookmaker offers.");
  const run = () => {
    const price = parseFloat(inp.value);
    const p = m.markets[sel.value].p;
    if (!(price > 1)) { out.className = "checker-out muted"; out.textContent = "Enter the decimal price your bookmaker offers."; return; }
    const ev = p * price - 1;
    const k = Math.max(0, (p * price - 1) / (price - 1)) * cfg().kelly_fraction;
    const share = Math.min(k, cfg().max_stake_pct);
    const step = cfg().stake_step_units;
    const st = share > 0 ? Math.max(step, Math.floor((share * cfg().bankroll_units) / step) * step) : 0;
    out.className = "checker-out";
    out.replaceChildren(
      h("div", null, `Model ${pct(p)} vs 1/price ${pct(1 / price)} → EV `, h("b", { class: ev > 0 ? "pos" : "neg" }, sgnPct(ev)),
        ev > 0 ? ` · stake by your rules: ${units(st)}` : " · no bet at this price"),
      h("div", { class: "muted small" }, "One price can't be de-margined, so this compares with 1/price. Your entry is not saved and never counts in the Record."));
  };
  inp.addEventListener("input", run);
  sel.addEventListener("change", run);
  return h("div", null, h("div", { class: "checker" }, sel, h("span", null, "@"), inp), out);
}

// ---------------------------------------------------------------- Today
function todayPicks() {
  const ref = nowRef();
  const until = ref + cfg().today_hours * 3600e3;
  const out = [];
  for (const p of S.data.picks) {
    const k = t(p.fixture.kickoff_utc);
    if (k >= ref - 2 * 3600e3 && k <= until) out.push(p);
  }
  return out.sort((a, b) => b.edge - a.edge);
}
function viewToday() {
  const picks = todayPicks();
  const ref = nowRef();
  const until = ref + cfg().today_hours * 3600e3;
  const examined = S.data.board.fixtures.filter((e) => t(e.fixture.kickoff_utc) >= ref - 2 * 3600e3 && t(e.fixture.kickoff_utc) <= until).length;
  return [
    h("div", { class: "view-head" }, h("h2", null, "Today"), h("span", { class: "muted small" }, `Next ${cfg().today_hours} hours · times in ${tz}`)),
    h("p", { class: "lede" }, picks.length
      ? `${picks.length} pick${picks.length > 1 ? "s" : ""} from ${examined} fixtures examined, ranked by edge. A pick needs an edge of at least ${pct(cfg().edge_threshold, 0)} over the bookmaker's fair price, model probability of at least ${pct(cfg().min_model_probability, 0)}, and odds of ${odds(cfg().min_odds)} or more.`
      : ""),
    picks.length ? h("div", { class: "cards" }, picks.map((p) => pickCard(p, boardById.get(p.fixture.fixture_id))))
      : h("div", { class: "empty" }, h("strong", null, "No qualifying edges in this window"),
        examined ? `${examined} fixtures were examined and none cleared every rule. That's normal: most prices are fair. See why for each one in `
          : "There are no fixtures with published prices in this window yet. Prices are published on Tuesday and Friday afternoons (UK). ",
        examined ? h("a", { href: "#week" }, "Next 7 days") : null, examined ? "." : null),
  ];
}


// ---------------------------------------------------------------- Most likely
// The result both the model AND the bookmaker rate most likely in each match. Not value bets:
// ranked by likelihood, shown with the price, and with its own backtest record above the list.
function likelyChoice(entry) {
  const m = entry.model;
  if (!m) return null;
  const c = cfg();
  const minOdds = S.likely.minOdds ?? c.min_odds;
  let best = null;
  for (const pr of entry.prices) {
    if (!c.bettable_bookmakers.includes(pr.bookmaker) || !["ou", "1x2"].includes(pr.market)) continue;
    if (pr.market === "ou" && !(pr.line != null && (pr.line * 2) % 2 === 1)) continue;
    const inv = Object.fromEntries(Object.entries(pr.prices).map(([sel, v]) => [sel, 1 / v]));
    const sum = Object.values(inv).reduce((a, b) => a + b, 0);
    for (const [sel, price] of Object.entries(pr.prices)) {
      const key = pr.market === "ou" ? `ou_${pr.line}_${sel}` : `1x2_${sel}`;
      const mk = m.markets[key];
      if (!mk) continue;
      const eo = pr.bookmaker === "BFE" ? 1 + (price - 1) * (1 - c.betfair_commission) : price;
      if (eo < minOdds) continue;
      const fair = inv[sel] / sum;
      const score = Math.min(mk.p, fair);
      if (!best || score > best.score) best = { key, market: pr.market, bk: pr.bookmaker, price, eo, p: mk.p, fair, score, at: pr.fetched_at };
    }
  }
  return best;
}
function viewLikely() {
  const c = cfg(), L = S.likely, ref = nowRef();
  const rows = [];
  for (const e of S.data.board.fixtures) {
    if (t(e.fixture.kickoff_utc) < ref) continue;
    const ch = likelyChoice(e);
    if (!ch) continue;
    if (L.minAgree !== "" && ch.score * 100 < parseFloat(L.minAgree)) continue;
    if (L.market && ch.market !== L.market) continue;
    rows.push({ e, ch });
  }
  rows.sort((a, b) => b.ch.score - a.ch.score);
  const lk = S.bt && S.bt.likely;
  const o = lk && lk.overall;
  const set = (k, num) => (ev) => { L[k] = ev.target.value === "" ? (num ? null : "") : (num ? parseFloat(ev.target.value) : ev.target.value); L.shown = 40; route(); };
  const bucketTable = (title, rs) => h("div", { class: "tablewrap" }, h("h4", { style: "margin:14px 0 6px;font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--text-muted)" }, title),
    h("table", null, h("thead", null, h("tr", null, h("th", null, ""), h("th", { class: "n" }, "Bets"), h("th", { class: "n" }, "Won"), h("th", { class: "n" }, "Bookmaker expected"), h("th", { class: "n" }, "Avg price"), h("th", { class: "n" }, "Return per bet"))),
      h("tbody", null, rs.map((r) => r.hidden ? h("tr", { class: "dim" }, h("td", null, r.key), h("td", { class: "n" }, r.n), h("td", { colspan: "4" }, "too few to show"))
        : h("tr", null, h("td", null, r.key), h("td", { class: "n" }, intl(r.n)), h("td", { class: "n" }, pct(r.hit_rate)), h("td", { class: "n" }, pct(r.expected_hit_rate_bookmaker)),
          h("td", { class: "n" }, odds(r.avg_odds)), h("td", { class: "n " + sign(r.roi_level) }, sgnPct(r.roi_level)))))));
  return [
    h("div", { class: "view-head" }, h("h2", null, "Most likely"), h("span", { class: "muted small" }, `Times in ${tz}`)),
    h("p", { class: "lede" }, `For each match: the bet that both the model and the bookmaker rate most likely, with odds of at least ${odds(S.likely.minOdds ?? c.min_odds)}, ranked from most to least likely. `,
      h("strong", null, "These are the likeliest results, not value bets."), " A likely result at a short price still loses the bookmaker's margin over time, which is why the Today tab ranks by edge instead."),
    o && o.n ? h("div", { class: "banner warn", style: "margin:0 0 14px" }, h("span", { class: "ico" }, "!"), h("div", null,
      h("strong", null, "How this list did in the backtest: "),
      `betting this choice in each of ${intl(o.n)} past matches won ${pct(o.hit_rate)} of bets (the bookmakers' prices predicted ${pct(o.expected_hit_rate_bookmaker)}) and returned ${sgnPct(o.roi_level)} per bet at an average price of ${odds(o.avg_odds)}. `,
      "Higher agreement wins more often but pays less: see the table below the list.")) : h("p", { class: "muted small" }, "The backtest for this list appears after the next weekly backtest run."),
    h("div", { class: "filters", role: "group", "aria-label": "Filters" },
      h("label", null, "Min odds", h("input", { type: "number", step: "0.05", min: "1.01", value: S.likely.minOdds ?? c.min_odds, onchange: set("minOdds", true) })),
      h("label", null, "Min agreement (%)", h("input", { type: "number", step: "5", value: L.minAgree, placeholder: "any", onchange: set("minAgree", false) })),
      h("label", null, "Market", h("select", { onchange: set("market", false) }, h("option", { value: "" }, "Goals and results"),
        h("option", { value: "ou", selected: L.market === "ou" }, "Goals (over/under 2.5)"), h("option", { value: "1x2", selected: L.market === "1x2" }, "Match result (1X2)"))),
      h("button", { class: "btn link", onclick: () => { S.likely = { minOdds: null, minAgree: "", market: "", shown: 40 }; route(); } }, "Reset")),
    rows.length ? h("div", { class: "tablewrap" }, h("table", null,
      h("thead", null, h("tr", null, h("th", null, "Kickoff"), h("th", null, "Match"), h("th", null, "Most likely"), h("th", { class: "n" }, "Price"),
        h("th", { class: "n" }, "Model"), h("th", { class: "n" }, "Bookmaker"), h("th", { class: "n" }, "Both agree"), h("th", { class: "n" }, "Only worth it at"), h("th", null, ""))),
      h("tbody", null, rows.slice(0, L.shown).map(({ e, ch }) => {
        const fx = e.fixture, id = "lk-" + fx.fixture_id, open = S.expanded.has(id);
        return [h("tr", null,
          h("td", null, fmtDay.format(new Date(fx.kickoff_utc)), h("div", { class: "muted small" }, fx.time_known ? fmtTime.format(new Date(fx.kickoff_utc)) : "TBC")),
          h("td", null, `${fx.home} v ${fx.away}`, h("div", { class: "muted small" }, `${fx.country} · ${fx.league}`)),
          h("td", null, h("strong", null, marketLabel(ch.key, fx))),
          h("td", { class: "n" }, odds(ch.price), h("div", { class: "muted small" }, bkName(ch.bk))),
          h("td", { class: "n" }, pct(ch.p)), h("td", { class: "n" }, pct(ch.fair)), h("td", { class: "n" }, h("strong", null, pct(ch.score))),
          h("td", { class: "n" }, `${odds(1 / ch.score)}+`),
          h("td", null, h("button", { class: "btn", "aria-expanded": String(open), onclick: () => toggle(id) }, open ? "Hide" : "Details"))),
          open ? h("tr", null, h("td", { colspan: "9", style: "padding:0" }, detailPanel(e, null))) : null];
      })))) : h("div", { class: "empty" }, h("strong", null, "Nothing matches these filters"), "Try a lower minimum agreement or odds."),
    rows.length > L.shown ? h("button", { class: "btn", style: "margin-top:10px", onclick: () => { L.shown += 40; route(); } }, `Show more (${rows.length - L.shown})`) : null,
    h("p", { class: "small muted", style: "margin-top:10px" }, "Both agree = the lower of the model's and the bookmaker's probability. ",
      "Only worth it at = the lowest price at which the bet breaks even if that lower probability is right. If Sportingbet offers less, the bet loses money on average. ",
      "These are not picks: they are not staked and never count in the Record."),
    lk ? h("section", { class: "section" }, h("h3", null, "Backtest of this list"),
      bucketTable("By how strongly model and bookmaker agree", lk.by_agreement), bucketTable("By market", lk.by_market)) : null,
  ];
}

// ---------------------------------------------------------------- Next 7 days
function bestCand(e) { return e.candidates && e.candidates.length ? e.candidates[0] : null; }
function viewWeek() {
  const f = S.filters;
  const all = S.data.board.fixtures.slice().sort((a, b) => t(a.fixture.kickoff_utc) - t(b.fixture.kickoff_utc));
  const leagues = [...new Map(all.map((e) => [e.fixture.div, e.fixture])).values()].sort((a, b) => (a.country + a.league).localeCompare(b.country + b.league));
  const countries = [...new Set(all.map((e) => e.fixture.country))].sort();
  const days = [...new Set(all.map((e) => dayKey(e.fixture.kickoff_utc)))];
  const shown = all.filter((e) => {
    const fx = e.fixture;
    if (f.league && fx.div !== f.league) return false;
    if (f.country && fx.country !== f.country) return false;
    if (f.date && dayKey(fx.kickoff_utc) !== f.date) return false;
    const pk = e.pick_ids.map((id) => picksById.get(id)).filter(Boolean)[0];
    const ref = pk ? { edge: pk.edge, market: pk.market, odds: pk.odds_at_pick } : (bestCand(e) ? { edge: bestCand(e).edge, market: bestCand(e).market, odds: bestCand(e).price } : null);
    if (f.minEdge !== "" && (!ref || ref.edge * 100 < parseFloat(f.minEdge))) return false;
    if (f.market && (!ref || ref.market !== f.market)) return false;
    if (f.oddsMin !== "" && (!ref || ref.odds < parseFloat(f.oddsMin))) return false;
    if (f.oddsMax !== "" && (!ref || ref.odds > parseFloat(f.oddsMax))) return false;
    return true;
  });
  const set = (k) => (ev) => { S.filters[k] = ev.target.value; route(); };
  const filters = h("div", { class: "filters", role: "group", "aria-label": "Filters" },
    h("label", null, "Country", h("select", { onchange: set("country") }, h("option", { value: "" }, "All"), countries.map((c) => h("option", { value: c, selected: f.country === c }, c)))),
    h("label", null, "League", h("select", { onchange: set("league") }, h("option", { value: "" }, "All"), leagues.map((l) => h("option", { value: l.div, selected: f.league === l.div }, `${l.country} · ${l.league}`)))),
    h("label", null, "Date", h("select", { onchange: set("date") }, h("option", { value: "" }, "All"), days.map((d) => h("option", { value: d, selected: f.date === d }, fmtDay.format(new Date(d + "T12:00:00")))))),
    h("label", null, "Market", h("select", { onchange: set("market") }, h("option", { value: "" }, "All"), h("option", { value: "ou", selected: f.market === "ou" }, "Over/under"), h("option", { value: "1x2", selected: f.market === "1x2" }, "1X2"))),
    h("label", null, "Min edge (pts)", h("input", { type: "number", step: "0.5", value: f.minEdge, onchange: set("minEdge") })),
    h("label", null, "Odds from", h("input", { type: "number", step: "0.05", value: f.oddsMin, onchange: set("oddsMin") })),
    h("label", null, "to", h("input", { type: "number", step: "0.05", value: f.oddsMax, onchange: set("oddsMax") })),
    h("button", { class: "btn link", onclick: () => { for (const k of Object.keys(S.filters)) S.filters[k] = ""; route(); } }, "Reset"));
  const groups = new Map();
  for (const e of shown) {
    const k = dayKey(e.fixture.kickoff_utc);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(e);
  }
  const nPick = all.filter((e) => e.pick_ids.length).length;
  return [
    h("div", { class: "view-head" }, h("h2", null, "Next 7 days"), h("span", { class: "muted small" }, `Times in ${tz}`)),
    h("p", { class: "lede" }, `${all.length} fixtures with published prices · ${nPick} with a pick. Every fixture shows its pick or the reason it has none.`,
      shown.length < all.length ? ` ${all.length - shown.length} hidden by filters.` : ""),
    filters,
    all.length === 0 ? h("div", { class: "empty" }, h("strong", null, "No fixtures in the window"), "The source hasn't published prices for the coming days yet (it does so on Tuesday and Friday afternoons UK time), or the last sync failed — see Settings.") : null,
    [...groups.entries()].map(([d, es]) => h("section", { class: "daygroup" }, h("h3", null, fmtDay.format(new Date(d + "T12:00:00"))), es.map(fixtureRow))),
  ];
}
function fixtureRow(e) {
  const fx = e.fixture, id = fx.fixture_id, open = S.expanded.has(id);
  const pks = e.pick_ids.map((i) => picksById.get(i)).filter(Boolean);
  let decision;
  if (pks.length) {
    const p = pks[0];
    decision = h("span", { class: "chip pick" }, "PICK ", h("b", null, p.label), ` @ ${odds(p.odds_at_pick)} ${p.bookmaker_name} · ${pts(p.edge)} · ${units(p.stake_units)}`, p.status !== "pending" ? [" ", badge(p.status)] : null);
  } else {
    const c = bestCand(e);
    let sub = null;
    if (e.reason === "insufficient_data" && e.reason_detail) sub = e.reason_detail;
    else if (c) sub = `best: ${candLabel(c, fx)} @ ${odds(c.price)} (${bkName(c.bookmaker)}) · model ${pct(c.p_model)} vs fair ${pct(c.p_fair)} · ${pts(c.edge)}`;
    else if (e.reason === "no_odds") sub = e.prices.length ? "prices published only for markets or books the rules don't use" : "no over/under price published for this fixture";
    decision = h("span", null, h("span", { class: "chip" }, e.reason_text), sub ? h("span", { class: "reason-sub" }, sub) : null);
  }
  return h("div", { class: "fx" },
    h("div", { class: "fx-row" }, h("time", { datetime: fx.kickoff_utc }, fx.time_known ? fmtTime.format(new Date(fx.kickoff_utc)) : "TBC"),
      h("div", { class: "fx-teams" }, `${fx.home} v ${fx.away}`, h("small", null, `${fx.country} · ${fx.league}`)),
      h("div", { class: "fx-decision" }, decision),
      h("button", { class: "btn fx-more", "aria-expanded": String(open), onclick: () => toggle(id) }, open ? "Hide" : "Details")),
    open ? (pks.length ? h("div", { style: "margin-bottom:14px" }, pickCard(pks[0], e)) : detailPanel(e, null)) : null);
}

// ---------------------------------------------------------------- Slip
function addToSlip(p) {
  if (SLIP.some((s) => s.pick_id === p.pick_id)) return;
  SLIP.push({ pick_id: p.pick_id, label: p.label, home: p.fixture.home, away: p.fixture.away, kickoff_utc: p.fixture.kickoff_utc,
    league: p.fixture.league, price: p.odds_at_pick, effective_odds: p.effective_odds, bookmaker_name: p.bookmaker_name,
    model_probability: p.model_probability, implied_probability: p.implied_probability, edge: p.edge, ev: p.ev, stake_units: p.stake_units, added_at: new Date().toISOString() });
  saveSlip(SLIP);
}
function viewSlip() {
  if (!SLIP.length) return [h("h2", null, "My slip"), h("div", { class: "empty", style: "margin-top:16px" }, h("strong", null, "Your slip is empty"), "Add picks from ", h("a", { href: "#today" }, "Today"), " or ", h("a", { href: "#week" }, "Next 7 days"), ". The slip is saved in this browser.")];
  const legs = SLIP.map((s) => ({ ...s, live: picksById.get(s.pick_id) }));
  const totalStake = legs.reduce((a, l) => a + l.stake_units, 0);
  const expProfit = legs.reduce((a, l) => a + l.stake_units * l.ev, 0);
  const combPrice = legs.reduce((a, l) => a * l.effective_odds, 1);
  const combP = legs.reduce((a, l) => a * l.model_probability, 1);
  const combFair = legs.reduce((a, l) => a * l.implied_probability, 1);
  const sameMatch = new Set(legs.map((l) => l.home + l.away)).size < legs.length;
  return [
    h("div", { class: "view-head" }, h("h2", null, "My slip"), h("button", { class: "btn link", onclick: () => { SLIP = []; saveSlip(SLIP); route(); } }, "Clear slip")),
    h("div", { class: "tablewrap" }, h("table", null,
      h("thead", null, h("tr", null, h("th", null, "Match"), h("th", null, "Bet"), h("th", { class: "n" }, "Price"), h("th", { class: "n" }, "Model"), h("th", { class: "n" }, "Fair"), h("th", { class: "n" }, "Edge"), h("th", { class: "n" }, "Stake"), h("th", null, "Status"), h("th", null, ""))),
      h("tbody", null, legs.map((l) => h("tr", null,
        h("td", null, `${l.home} v ${l.away}`, h("div", { class: "muted small" }, `${l.league} · ${fmtFull.format(new Date(l.kickoff_utc))}`)),
        h("td", null, l.label, h("div", { class: "muted small" }, l.bookmaker_name)),
        h("td", { class: "n" }, odds(l.price)), h("td", { class: "n" }, pct(l.model_probability)), h("td", { class: "n" }, pct(l.implied_probability)),
        h("td", { class: "n" }, pts(l.edge)), h("td", { class: "n" }, units(l.stake_units)),
        h("td", null, badge(l.live ? l.live.status : "pending")),
        h("td", null, h("button", { class: "btn link", onclick: () => { SLIP = SLIP.filter((x) => x.pick_id !== l.pick_id); saveSlip(SLIP); route(); } }, "Remove"))))))),
    h("div", { class: "split slip-total" },
      h("div", { class: "tile" }, h("div", { class: "k" }, "As singles (recommended)"),
        h("div", { class: "v" }, units(totalStake)), h("div", { class: "s" }, `total stake · model's expected profit ${sgnUnits(expProfit)} (${legs.length} leg${legs.length > 1 ? "s" : ""})`)),
      h("div", { class: "tile" }, h("div", { class: "k" }, "As one accumulator"),
        h("div", { class: "v" }, odds(combPrice)), h("div", { class: "s" }, `combined price · model ${pct(combP, 2)} vs bookmakers' fair ${pct(combFair, 2)} · EV ${sgnPct(combP * combPrice - 1)}`))),
    h("div", { class: "warnline" }, h("strong", null, "Accumulators multiply variance. "),
      `Each leg must win, so the chance of the whole slip landing is only ${pct(combP, 1)} by the model's own numbers. Any model error is compounded across legs. Singles keep the staking rules intact.`,
      sameMatch ? " Two legs are on the same match, so they are correlated and the combined figures above are not valid." : ""),
  ];
}

// ---------------------------------------------------------------- Record
function tile(k, v, s, cls) { return h("div", { class: "tile" }, h("div", { class: "k" }, k), h("div", { class: "v " + (cls || "") }, v), s ? h("div", { class: "s" }, s) : null); }
const sign = (x) => (x == null ? "" : x >= 0 ? "pos" : "neg");
function statTiles(o, isBacktest) {
  if (!o || !o.n) return null;
  return h("div", { class: "tiles" },
    tile("Settled picks", intl(o.n), `${o.wins} won · ${o.losses} lost`),
    tile("Hit rate", pct(o.hit_rate), `n=${o.n} · model expected ${pct(o.expected_wins / o.n)}`),
    tile("ROI, level stakes", sgnPct(o.roi_level), `n=${o.n} · ${sgnUnits(o.level_profit_units, 1)} at 1 u each`, sign(o.roi_level)),
    tile("ROI, Kelly stakes", o.roi_kelly == null ? "–" : sgnPct(o.roi_kelly), `n=${o.n} · ${sgnUnits(o.kelly_profit_units, 1)} on ${units(o.staked_units, 1)}`, sign(o.roi_kelly)),
    tile("Average edge at pick", pts(o.avg_edge), `n=${o.n} · avg price ${odds(o.avg_odds)}`),
    tile("Closing line value", o.clv_n ? sgnPct(o.avg_clv) : "–", o.clv_n ? `n=${o.clv_n} · beat the close ${pct(o.clv_beat_close_share, 0)} of the time` : "n=0 · no closing prices published yet", o.clv_n ? sign(o.avg_clv) : ""),
    tile("Longest losing run", String(o.longest_losing_streak), `n=${o.n} picks`),
    tile("Worst drawdown", units(-o.max_drawdown_level_units, 1), "level stakes, peak to trough", "neg"));
}
function breakdownTable(title, rows, minSample) {
  return h("div", { class: "tablewrap" }, h("h4", { style: "margin:18px 0 6px;font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--text-muted)" }, title),
    h("table", null, h("thead", null, h("tr", null, h("th", null, ""), h("th", { class: "n" }, "n"), h("th", { class: "n" }, "Hit rate"), h("th", { class: "n" }, "ROI (level)"), h("th", { class: "n" }, "Avg edge"), h("th", { class: "n" }, "CLV"))),
      h("tbody", null, rows.map((r) => r.hidden
        ? h("tr", { class: "dim" }, h("td", null, r.key), h("td", { class: "n" }, r.n), h("td", { colspan: "4" }, `fewer than ${minSample} settled; hidden`))
        : h("tr", null, h("td", null, r.key), h("td", { class: "n" }, r.n), h("td", { class: "n" }, pct(r.hit_rate)),
          h("td", { class: "n " + sign(r.roi_level) }, sgnPct(r.roi_level)), h("td", { class: "n" }, pts(r.avg_edge)),
          h("td", { class: "n" }, r.clv_n ? `${sgnPct(r.avg_clv)} (n=${r.clv_n})` : "–"))))));
}
function lineChart(title, series, yLabel) {
  // series: [{name, color, points:[{x:Date, y}]}]
  const wrap = h("div", { class: "chart" });
  const head = h("p", { class: "chart-title" }, title);
  const legend = series.length > 1 ? h("div", { class: "legend" }, series.map((s) => h("span", null, h("i", { style: `background:${s.color}` }), s.name))) : null;
  wrap.append(head);
  if (legend) wrap.append(legend);
  const draw = () => {
    const old = wrap.querySelector("svg");
    if (old) old.remove();
    const W = Math.max(320, wrap.clientWidth - 24), H = 240, m = { l: 52, r: series.length > 1 ? 118 : 16, t: 10, b: 26 };
    const xs = series.flatMap((s) => s.points.map((p) => p.x.getTime()));
    const ys = series.flatMap((s) => s.points.map((p) => p.y)).concat([0]);
    if (!xs.length) return;
    let x0 = Math.min(...xs), x1 = Math.max(...xs);
    if (x0 === x1) { x0 -= 86400e3; x1 += 86400e3; }
    let y0 = Math.min(...ys), y1 = Math.max(...ys);
    const pad = (y1 - y0) * 0.08 || 1; y0 -= pad; y1 += pad;
    const X = (v) => m.l + ((v - x0) / (x1 - x0)) * (W - m.l - m.r);
    const Y = (v) => m.t + (1 - (v - y0) / (y1 - y0)) * (H - m.t - m.b);
    const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${title}. Use the table below for exact values.` });
    const ticks = niceTicks(y0, y1, 5);
    svg.append(sv("g", { class: "grid" }, ticks.map((v) => sv("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }))));
    svg.append(sv("g", { class: "axis" }, ticks.map((v) => sv("text", { x: m.l - 8, y: Y(v) + 4, "text-anchor": "end" }, (v > 0 ? "+" : "") + fmtNum(v))),
      sv("text", { x: 4, y: m.t + 4, "text-anchor": "start" }, yLabel)));
    const xt = 5;
    svg.append(sv("g", { class: "axis" }, Array.from({ length: xt }, (_, i) => x0 + (i * (x1 - x0)) / (xt - 1)).map((v, i) =>
      sv("text", { x: X(v), y: H - 6, "text-anchor": i === 0 ? "start" : i === xt - 1 ? "end" : "middle" }, fmtDate.format(new Date(v))))));
    svg.append(sv("line", { class: "zero", x1: m.l, x2: W - m.r, y1: Y(0), y2: Y(0) }));
    const ends = [];
    for (const s of series) {
      const d = s.points.map((p, i) => `${i ? "L" : "M"}${X(p.x.getTime()).toFixed(1)},${Y(p.y).toFixed(1)}`).join("");
      svg.append(sv("path", { d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      const last = s.points[s.points.length - 1];
      if (last && series.length > 1) ends.push({ name: s.name, x: X(last.x.getTime()) + 8, y: Y(last.y) + 4 });
    }
    ends.sort((a, b) => a.y - b.y);
    for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 14) ends[i].y = ends[i - 1].y + 14;
    svg.append(sv("g", { class: "axis" }, ends.map((e) => sv("text", { x: e.x, y: e.y, "text-anchor": "start" }, e.name))));
    const xh = sv("line", { class: "xh", y1: m.t, y2: H - m.b, visibility: "hidden" });
    const dots = series.map((s) => sv("circle", { r: 4.5, fill: s.color, stroke: "var(--surface-1)", "stroke-width": 2, visibility: "hidden" }));
    svg.append(xh, ...dots);
    const tip = document.getElementById("tooltip");
    const overlay = sv("rect", { x: m.l, y: 0, width: W - m.l - m.r, height: H, fill: "transparent" });
    const base = series[0].points;
    overlay.addEventListener("pointermove", (ev) => {
      const r = svg.getBoundingClientRect();
      const vx = x0 + ((ev.clientX - r.left) * (W / r.width) - m.l) / (W - m.l - m.r) * (x1 - x0);
      let bi = 0, bd = Infinity;
      base.forEach((p, i) => { const dd = Math.abs(p.x.getTime() - vx); if (dd < bd) { bd = dd; bi = i; } });
      const px = X(base[bi].x.getTime());
      xh.setAttribute("x1", px); xh.setAttribute("x2", px); xh.setAttribute("visibility", "visible");
      series.forEach((s, i) => { const p = s.points[bi]; if (p) { dots[i].setAttribute("cx", X(p.x.getTime())); dots[i].setAttribute("cy", Y(p.y)); dots[i].setAttribute("visibility", "visible"); } });
      tip.replaceChildren(h("div", null, fmtDate.format(base[bi].x)), ...series.map((s) => h("div", { class: "row" }, h("span", null, s.name), h("span", null, sgnUnits(s.points[bi] ? s.points[bi].y : null)))),
        base[bi].n != null ? h("div", { class: "row" }, h("span", null, "picks settled"), h("span", null, base[bi].n)) : null);
      tip.hidden = false;
      tip.style.left = Math.min(window.innerWidth - 270, ev.clientX + 14) + "px";
      tip.style.top = ev.clientY + 14 + "px";
    });
    overlay.addEventListener("pointerleave", () => { tip.hidden = true; xh.setAttribute("visibility", "hidden"); dots.forEach((d) => d.setAttribute("visibility", "hidden")); });
    svg.append(overlay);
    wrap.insertBefore(svg, wrap.querySelector("details"));
  };
  const table = h("details", { class: "tableview" }, h("summary", null, "Show as table"),
    h("div", { class: "tablewrap" }, h("table", null, h("thead", null, h("tr", null, h("th", null, "Date"), series.map((s) => h("th", { class: "n" }, s.name)))),
      h("tbody", null, series[0].points.map((p, i) => h("tr", null, h("td", null, fmtDate.format(p.x)), series.map((s) => h("td", { class: "n" }, sgnUnits(s.points[i] ? s.points[i].y : null)))))))));
  wrap.append(table);
  requestAnimationFrame(draw);
  new ResizeObserver(() => draw()).observe(wrap);
  return wrap;
}
function fmtNum(v) { return Math.abs(v) >= 100 ? Math.round(v).toString() : Math.abs(v) >= 10 ? v.toFixed(0) : v.toFixed(1); }
function niceTicks(lo, hi, n) {
  const span = hi - lo, step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => s >= step0) || 10 * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Math.abs(v) < 1e-9 ? 0 : v);
  return out;
}
function dailySeries(points, key) {
  const byDay = new Map();
  points.forEach((p, i) => byDay.set(p.date, { y: p[key], n: i + 1 }));
  return [...byDay.entries()].map(([d, v]) => ({ x: new Date(d + "T12:00:00"), y: v.y, n: v.n }));
}
function viewRecord() {
  const r = S.data.record, o = r.overall, c = r.counts;
  const out = [
    h("div", { class: "view-head" }, h("h2", null, "Record")),
    h("p", { class: "lede" }, `Computed only from settled pick records. ${intl(c.picks_total)} picks made: ${c.won} won, ${c.lost} lost, ${c.void} void, ${c.pending} pending`,
      c.reconciles ? " (reconciles with every pick record)." : " — WARNING: counts do not reconcile; check the pipeline log.",
      ` Breakdowns with fewer than ${r.min_sample} settled picks are hidden.`),
  ];
  if (!o.n) {
    out.push(h("div", { class: "empty" }, h("strong", null, "No settled picks yet"), c.pending ? `${c.pending} pick${c.pending > 1 ? "s are" : " is"} waiting for results. ` : "", "Nothing here is estimated: the record starts with the first settled pick."));
  } else {
    out.push(statTiles(o, false));
    out.push(lineChart("Cumulative profit (units)", [
      { name: "Level stakes (1 u)", color: "var(--series-1)", points: dailySeries(r.series, "level") },
      { name: "Kelly stakes", color: "var(--series-2)", points: dailySeries(r.series, "kelly") }], "units"));
    out.push(h("div", { class: "split" },
      h("div", null, breakdownTable("By league", r.breakdowns.league, r.min_sample), breakdownTable("By selection", r.breakdowns.selection, r.min_sample)),
      h("div", null, breakdownTable("By confidence", r.breakdowns.confidence, r.min_sample), breakdownTable("By bookmaker", r.breakdowns.bookmaker, r.min_sample))));
  }
  if (S.data.picks.length) out.push(picksTable());
  out.push(backtestSection());
  return out;
}
function picksTable() {
  const rows = S.data.picks.slice(0, S.picksShown);
  return h("section", { class: "section" }, h("h3", null, "Every pick"),
    h("div", { class: "tablewrap" }, h("table", null,
      h("thead", null, h("tr", null, ["Kickoff", "Match", "Bet", "Price", "Model", "Fair", "Edge", "Stake", "Conf.", "Result", "Profit", "CLV"].map((x, i) => h("th", { class: i >= 3 && i <= 7 || i >= 10 ? "n" : "" }, x)))),
      h("tbody", null, rows.map((p) => h("tr", null,
        h("td", null, fmtDay.format(new Date(p.fixture.kickoff_utc))),
        h("td", null, `${p.fixture.home} v ${p.fixture.away}`, h("div", { class: "muted small" }, `${p.fixture.country} · ${p.fixture.league}`)),
        h("td", null, p.label, h("div", { class: "muted small" }, p.bookmaker_name)),
        h("td", { class: "n" }, odds(p.odds_at_pick)), h("td", { class: "n" }, pct(p.model_probability)), h("td", { class: "n" }, pct(p.implied_probability)),
        h("td", { class: "n" }, pts(p.edge)), h("td", { class: "n" }, units(p.stake_units)), h("td", null, p.confidence),
        h("td", null, badge(p.status), p.ft_home != null ? h("div", { class: "muted small" }, `${p.ft_home}–${p.ft_away}`) : null),
        h("td", { class: "n " + sign(p.profit_units) }, p.status === "pending" ? "" : sgnUnits(p.profit_units)),
        h("td", { class: "n" }, p.clv != null ? sgnPct(p.clv) : "")))))),
    S.data.picks.length > S.picksShown ? h("button", { class: "btn", style: "margin-top:10px", onclick: () => { S.picksShown += 200; route(); } }, `Show more (${S.data.picks.length - S.picksShown} older)`) : null);
}
function backtestSection() {
  const bt = S.bt;
  if (!bt || !bt.overall) return h("section", { class: "section" }, h("h3", null, "Backtest"), h("p", { class: "muted" }, "No backtest has run yet. It runs automatically once a week."));
  const cal = bt.calibration_ou25 || {};
  return h("section", { class: "bt-wrap" }, h("span", { class: "bt-tag" }, "Backtest · simulated · not live picks"),
    h("h3", null, `Walk-forward replay, ${fmtDate.format(new Date(bt.period.from))} – ${fmtDate.format(new Date(bt.period.to))}`),
    h("p", { class: "lede" }, "Each week the model was refitted on earlier results only, then priced against the pre-match odds the source published before those games, using exactly the live rules. ",
      `Generated ${fmtFull.format(new Date(bt.generated_at))}. These numbers never mix with the live record above. `,
      "The model settings were chosen on prediction accuracy over part of this period, so treat the result as optimistic, if anything."),
    statTiles(bt.overall, true),
    cal.n ? h("p", { class: "lede", style: "margin-top:14px" }, h("strong", null, "Is the model better informed than the bookmaker? "),
      `On ${intl(cal.n)} over/under 2.5 markets the model's log-loss was ${cal.model_log_loss.toFixed(4)} against the bookmaker's ${cal.market_log_loss.toFixed(4)} (lower is better; a coin flip scores ${cal.coin_flip_log_loss.toFixed(4)}). `,
      cal.model_log_loss > cal.market_log_loss ? "The bookmaker's prices were the better forecast, so most apparent edges are model error." : "The model forecast better than the bookmaker on this sample.") : null,
    bt.series && bt.series.length ? lineChart("Backtest cumulative profit, level stakes (units)", [{ name: "Level stakes (1 u)", color: "var(--series-1)", points: bt.series.map((p) => ({ x: new Date(p.date + "T12:00:00"), y: p.level })) }], "units") : null,
    h("div", { class: "split" },
      h("div", null, breakdownTable("By edge size", bt.breakdowns.edge, cfg().min_sample_for_breakdown), breakdownTable("By selection", bt.breakdowns.selection, cfg().min_sample_for_breakdown)),
      h("div", null, breakdownTable("By confidence", bt.breakdowns.confidence, cfg().min_sample_for_breakdown), breakdownTable("By league", bt.breakdowns.league, cfg().min_sample_for_breakdown))));
}

// ---------------------------------------------------------------- Settings
const SETTING_TEXT = {
  bankroll_units: "Bankroll, in units", kelly_fraction: "Kelly fraction", max_stake_pct: "Max stake per pick (share of bankroll)",
  stake_step_units: "Stake rounding (units)", edge_threshold: "Minimum edge", min_model_probability: "Minimum model probability",
  min_odds: "Minimum odds", max_edge: "Edges above this are treated as model error", markets: "Markets", bettable_bookmakers: "Bookmakers that can become picks",
  betfair_commission: "Betfair Exchange commission", window_days: "Fixture window (days)", today_hours: "Today window (hours)",
  pick_cutoff_minutes: "No new picks within (minutes of kickoff)", void_after_days: "Void if no result after (days)",
  min_team_matches: "Min matches per team (last 400 days)", min_sample_for_breakdown: "Min sample for a breakdown",
};
function fmtSetting(k, v) {
  if (["edge_threshold", "min_model_probability", "max_edge", "max_stake_pct", "betfair_commission"].includes(k)) return pct(v, 1);
  if (Array.isArray(v)) return v.map((x) => (k === "bettable_bookmakers" ? `${bkName(x)} (${x})` : x)).join(", ");
  return String(v);
}
function viewSettings() {
  const c = cfg(), st = S.data.status;
  const jobs = Object.values(st.jobs || {}).sort((a, b) => a.job.localeCompare(b.job));
  const run = st.last_model_run;
  return [
    h("div", { class: "view-head" }, h("h2", null, "Settings")),
    h("p", { class: "lede" }, "Settings live in ", h("code", null, "config.yaml"), " in your GitHub repository. Open it there, click the pencil icon, change a value and commit. The next scheduled run uses it. Picks already made keep the settings they were made with (each pick stores them)."),
    h("section", { class: "section" }, h("h3", null, "Staking and pick rules"),
      h("dl", { class: "kv" }, Object.keys(SETTING_TEXT).filter((k) => k in c).map((k) => [h("dt", null, SETTING_TEXT[k]), h("dd", { class: "mono" }, fmtSetting(k, c[k]))]))),
    h("section", { class: "section" }, h("h3", null, "Model"),
      h("dl", { class: "kv" },
        h("dt", null, "Version"), h("dd", { class: "mono" }, c.model.version),
        h("dt", null, "Half-life of a match"), h("dd", { class: "mono" }, `${c.model.half_life_days} days`),
        h("dt", null, "Shrinkage toward division average (prior sd)"), h("dd", { class: "mono" }, c.model.prior_sd),
        h("dt", null, "Weight on shots on target"), h("dd", { class: "mono" }, c.model.shots_weight),
        h("dt", null, "Seasons of history"), h("dd", { class: "mono" }, c.model.seasons_of_history)),
      run ? h("div", { class: "tablewrap", style: "margin-top:14px" }, h("p", { class: "small muted" }, `Last model run ${run.run_id} at ${fmtFull.format(new Date(run.at))}: ${run.fixtures_processed} fixtures, ${run.fixtures_with_prediction} priced by the model, ${run.picks_created} new picks.`),
        h("table", null, h("thead", null, h("tr", null, h("th", null, "Group"), h("th", null, "Ratings as of"), h("th", { class: "n" }, "Matches"), h("th", { class: "n" }, "Teams"), h("th", null, "Goals per team (base)"), h("th", null, "Fit"))),
          h("tbody", null, Object.entries(run.groups).map(([g, x]) => x.error ? h("tr", null, h("td", null, g), h("td", { colspan: "5", class: "neg" }, x.error))
            : h("tr", null, h("td", null, g), h("td", null, x.asof), h("td", { class: "n" }, intl(x.n_matches)), h("td", { class: "n" }, x.n_teams),
              h("td", { class: "mono small" }, Object.entries(x.league_base_goals).map(([d, v]) => `${d} ${v}`).join("  ")),
              h("td", null, x.reused_from_cache ? "cached (no new results)" : `fitted in ${x.seconds}s`)))))) : null),
    h("section", { class: "section" }, h("h3", null, "Leagues"),
      h("p", { class: "small muted" }, "Switch leagues on or off in leagues.yaml."),
      h("div", { class: "tablewrap" }, h("table", null, h("thead", null, h("tr", null, h("th", null, "Code"), h("th", null, "League"), h("th", null, "Country"), h("th", null, "Active"))),
        h("tbody", null, S.data.leagues.map((l) => h("tr", { class: l.active ? "" : "dim" }, h("td", { class: "mono" }, l.code), h("td", null, l.name), h("td", null, l.country), h("td", null, l.active ? "Yes" : "No"))))))),
    h("section", { class: "section" }, h("h3", null, "Data sync status"),
      h("dl", { class: "kv" },
        h("dt", null, "Site data generated"), h("dd", null, `${fmtFull.format(new Date(S.data.generated_at))} (${ago(S.data.generated_at)})`),
        h("dt", null, "Prices last changed at source"), h("dd", null, st.source_state.fixtures_csv_changed_at ? fmtFull.format(new Date(st.source_state.fixtures_csv_changed_at)) : "never"),
        h("dt", null, "Source last checked"), h("dd", null, st.source_state.fixtures_csv_checked_at ? fmtFull.format(new Date(st.source_state.fixtures_csv_checked_at)) : "never"),
        h("dt", null, "Results files stored"), h("dd", null, st.history_files)),
      h("div", { class: "tablewrap", style: "margin-top:14px" }, h("table", null, h("thead", null, h("tr", null, h("th", null, "Job"), h("th", null, "Last run"), h("th", null, "Status"), h("th", null, "Last success"), h("th", null, "Counts"))),
        h("tbody", null, jobs.map((j) => h("tr", null, h("td", { class: "mono" }, j.job), h("td", null, fmtFull.format(new Date(j.at))),
          h("td", { class: j.status === "error" ? "neg" : "" }, j.status, j.message ? h("div", { class: "small muted" }, j.message) : null),
          h("td", null, st.last_success[j.job] ? ago(st.last_success[j.job]) : "never"),
          h("td", { class: "small mono" }, Object.entries(j.counts || {}).filter(([, v]) => v).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join(" · "))))))),
      st.recent_fetch_errors.length ? h("div", { class: "tablewrap", style: "margin-top:14px" }, h("h4", { class: "small" }, "Recent request errors"),
        h("table", null, h("tbody", null, st.recent_fetch_errors.map((e) => h("tr", null, h("td", null, fmtFull.format(new Date(e.at))), h("td", { class: "mono small" }, e.url), h("td", { class: "neg" }, e.error)))))) : h("p", { class: "small muted" }, "No request errors recently.")),
    h("section", { class: "section" }, h("h3", null, "This browser"),
      h("dl", { class: "kv" }, h("dt", null, "Times shown in"), h("dd", null, tz), h("dt", null, "Picks in your slip"), h("dd", null, SLIP.length))),
  ];
}

// ---------------------------------------------------------------- router
// Plain #name routes (no slashes) so links survive being opened anywhere.
const VIEWS = { today: viewToday, likely: viewLikely, week: viewWeek, slip: viewSlip, record: viewRecord, config: viewSettings };
function route() {
  const r = (location.hash.match(/^#\/?([\w-]+)/) || [])[1] || "today";
  const name = VIEWS[r] ? r : "today";
  renderChrome(name);
  const view = document.getElementById("view");
  const y = window.scrollY;
  view.replaceChildren(...[VIEWS[name]()].flat(Infinity).filter(Boolean));
  if (route.last === name) window.scrollTo(0, y); else { window.scrollTo(0, 0); }
  route.last = name;
  document.title = `GoalSeeker · ${{ today: "Today", likely: "Most likely", week: "Next 7 days", slip: "My slip", record: "Record", config: "Settings" }[name]}`;
}
async function boot() {
  try {
    const res = await fetch("data/app.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    S.data = await res.json();
  } catch (e) {
    document.getElementById("view").replaceChildren(h("div", { class: "empty" }, h("strong", null, "No data yet"),
      "data/app.json could not be loaded. If you have just set this up, run the GoalSeeker workflow once from the Actions tab of your GitHub repository and reload."));
    return;
  }
  try { const b = await fetch("data/backtest.json", { cache: "no-store" }); if (b.ok) S.bt = await b.json(); } catch (e) { /* optional */ }
  index();
  window.addEventListener("hashchange", route);
  route();
}
boot();
