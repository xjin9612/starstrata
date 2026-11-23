"use strict";

// 所有來自資料的文字都用 textContent 放進 DOM；repo 描述是外部輸入。

const NUM = new Intl.NumberFormat("zh-TW");
const PERIOD_LABEL = { daily: "今日", weekly: "本週", monthly: "本月" };
const DAY_MS = 86400000;

const state = { latest: null, index: null, trendingLang: "", trendingPeriod: "", range: "365" };

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function svgEl(tag, attrs) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, String(value));
  return node;
}

function fmt(n) {
  return n === null || n === undefined ? "—" : NUM.format(n);
}

// 依刻度間距決定單位與小數位數，讓相鄰刻度的標籤不會相同；需要超過 2 位小數時改用較小的單位。
function tickFormatter(ticks) {
  const step = ticks.length > 1 ? ticks[1] - ticks[0] : 1;
  const max = Math.max(...ticks.map(Math.abs));
  const units = [[1e6, "M"], [1e3, "K"]].filter(([unit]) => max >= unit * 10 && step / unit >= 0.01);
  if (!units.length) return (v) => NUM.format(v);
  const [unit, suffix] = units[0];
  const digits = Math.max(0, Math.ceil(-Math.log10(step / unit)));
  return (v) => (v === 0 ? "0" : `${(v / unit).toFixed(digits)}${suffix}`);
}

// 與 starstrata.snapshots.node_file_stem 相同：node_id 的 UTF-8 十六進位編碼。
function nodeFileStem(nodeId) {
  return [...new TextEncoder().encode(nodeId)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function repoLink(fullName, nodeId) {
  return nodeId ? el("a", { href: `#/repo/${encodeURIComponent(nodeId)}` }, fullName) : fullName;
}

function githubLink(fullName) {
  return el("a", { href: `https://github.com/${fullName}`, rel: "noopener" }, "GitHub ↗");
}

async function getJSON(path) {
  const response = await fetch(path, { cache: "no-cache" });
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json();
}

function repoCell(item) {
  return el(
    "td",
    {},
    repoLink(item.full_name, item.node_id),
    item.language ? el("span", { class: "lang" }, ` · ${item.language}`) : null,
    item.description ? el("div", { class: "desc" }, item.description) : null,
  );
}

function table(headers, rows) {
  if (!rows.length) return el("p", { class: "empty" }, "沒有資料");
  return el(
    "div",
    { class: "table-wrap" },
    el(
      "table",
      {},
      el("thead", {}, el("tr", {}, headers.map(([label, cls]) => el("th", { class: cls }, label)))),
      el("tbody", {}, rows),
    ),
  );
}

function card(title, subtitle, ...body) {
  return el("section", { class: "card" }, el("h2", {}, title), subtitle ? el("p", { class: "sub" }, subtitle) : null, body);
}


function viewTrending() {
  const latest = state.latest;
  const pages = latest.trending;
  if (!pages.length) return card("Trending", null, el("p", { class: "empty" }, "今天沒有 Trending 資料"));
  const languages = [...new Set(pages.map((p) => p.language || ""))];
  const periods = [...new Set(pages.map((p) => p.period))];
  if (!languages.includes(state.trendingLang)) state.trendingLang = languages[0];
  if (!periods.includes(state.trendingPeriod)) state.trendingPeriod = periods[0];

  const langSelect = el(
    "select",
    { onchange: (e) => { state.trendingLang = e.target.value; render(); } },
    languages.map((l) => el("option", { value: l, selected: l === state.trendingLang }, l || "全部語言")),
  );
  const periodButtons = el(
    "div",
    { class: "seg", role: "group", "aria-label": "期間" },
    periods.map((p) =>
      el("button", {
        type: "button",
        "aria-pressed": p === state.trendingPeriod ? "true" : "false",
        onclick: () => { state.trendingPeriod = p; render(); },
      }, PERIOD_LABEL[p] || p),
    ),
  );
  const page = pages.find((p) => (p.language || "") === state.trendingLang && p.period === state.trendingPeriod);
  const rows = (page ? page.entries : []).map((e) =>
    el("tr", {},
      el("td", { class: "num" }, e.rank),
      repoCell(e),
      el("td", { class: "num up" }, e.stars_in_period === null ? "—" : `+${fmt(e.stars_in_period)}`),
      el("td", { class: "num" }, fmt(e.stars)),
    ),
  );
  return [
    el("div", { class: "filters" }, el("label", {}, "語言", langSelect), periodButtons),
    card(
      "GitHub Trending",
      `${state.trendingLang || "全部語言"} · ${PERIOD_LABEL[state.trendingPeriod] || state.trendingPeriod}`,
      table([["#", "num"], ["Repo"], [`${PERIOD_LABEL[state.trendingPeriod] || ""}新增 ★`, "num"], ["總 ★", "num"]], rows),
    ),
  ];
}

function viewNew() {
  const rows = state.latest.new_repos.map((r) =>
    el("tr", {}, repoCell(r), el("td", { class: "num" }, fmt(r.stars))),
  );
  return card("新上榜", "今天第一次出現在 Trending 或 Search 的 repo", table([["Repo"], ["總 ★", "num"]], rows));
}

function viewGainers() {
  const rows = state.latest.gainers.map((r, i) =>
    el("tr", {},
      el("td", { class: "num" }, i + 1),
      repoCell(r),
      el("td", { class: "num up" }, `+${fmt(r.delta)}`),
      el("td", { class: "num" }, fmt(r.stars)),
    ),
  );
  return card("日增排行", "與前一天快照相比", table([["#", "num"], ["Repo"], ["日增 ★", "num"], ["總 ★", "num"]], rows));
}

function viewAlerts() {
  const rows = state.latest.alerts.map((a) =>
    el("tr", {},
      el("td", {}, a.date),
      el("td", {}, el("span", { class: "badge" }, "爆紅"), " ", repoLink(a.full_name, a.node_id),
        a.description ? el("div", { class: "desc" }, a.description) : null),
      el("td", { class: "num up" }, `+${fmt(a.daily_stars)}`),
      el("td", { class: "num" }, a.baseline === null ? "資料不足" : `+${NUM.format(Math.round(a.baseline * 10) / 10)}`),
    ),
  );
  return card("爆紅警示", "最近 30 天", table([["日期"], ["Repo"], ["日增 ★", "num"], ["基準（日平均）", "num"]], rows));
}


function niceStep(raw) {
  const pow = 10 ** Math.floor(Math.log10(raw));
  const f = raw / pow;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * pow;
}

// star 數一律是整數，刻度間距至少為 1；全為非負值時軸不低於 0。
function niceTicks(lo, hi, count) {
  if (lo === hi) {
    hi += 1;
    if (lo > 0) lo -= 1;
  }
  const step = Math.max(1, niceStep((hi - lo) / count));
  const start = Math.floor(lo / step) * step;
  const end = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let v = start; v <= end + step / 2; v += step) ticks.push(Math.round(v * 1e6) / 1e6);
  return ticks;
}

function dateTicks(t0, t1, count) {
  const span = t1 - t0;
  const ticks = [];
  for (let i = 0; i <= count; i++) ticks.push(t0 + (span * i) / count);
  const long = span > 120 * DAY_MS;
  return ticks.map((t) => ({ t, label: new Date(t).toISOString().slice(long ? 0 : 5, long ? 7 : 10) }));
}

function toTime(d) {
  return Date.parse(`${d}T00:00:00Z`);
}

// kind 為 "line"（star 累計）或 "bar"（每日新增）。series 為 [{t, v, d, src}]，依時間排序。
function drawChart(host, series, kind, describe) {
  host.replaceChildren();
  if (!series.length) {
    host.append(el("p", { class: "empty" }, "這段期間沒有資料"));
    return;
  }
  const width = Math.max(host.clientWidth || 600, 300);
  const height = 240;
  const m = { top: 12, right: kind === "line" ? 80 : 16, bottom: 26, left: 52 };
  const plotW = width - m.left - m.right;
  const plotH = height - m.top - m.bottom;
  let t0 = series[0].t;
  let t1 = series[series.length - 1].t;
  if (t0 === t1) { t0 -= DAY_MS; t1 += DAY_MS; }
  const values = series.map((p) => p.v);
  const lo = kind === "bar" ? Math.min(0, ...values) : Math.min(...values);
  const hi = kind === "bar" ? Math.max(0, ...values) : Math.max(...values);
  const yTicks = niceTicks(lo, hi, 4);
  const tickLabel = tickFormatter(yTicks);
  const y0 = yTicks[0];
  const y1 = yTicks[yTicks.length - 1];
  const x = (t) => m.left + ((t - t0) / (t1 - t0)) * plotW;
  const y = (v) => m.top + plotH - ((v - y0) / (y1 - y0)) * plotH;

  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", tabindex: 0, "aria-label": describe });
  for (const v of yTicks) {
    svg.append(svgEl("line", { class: v === 0 && kind === "bar" ? "axis" : "grid", x1: m.left, x2: width - m.right, y1: y(v), y2: y(v) }));
    const label = svgEl("text", { class: "tick", x: m.left - 8, y: y(v) + 4, "text-anchor": "end" });
    label.textContent = tickLabel(v);
    svg.append(label);
  }
  for (const { t, label } of dateTicks(t0, t1, Math.max(2, Math.min(5, Math.floor(plotW / 110))))) {
    const text = svgEl("text", { class: "tick", x: x(t), y: height - 6, "text-anchor": "middle" });
    text.textContent = label;
    svg.append(text);
  }

  if (kind === "line") {
    const path = series.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
    svg.append(svgEl("path", { class: "area", d: `${path}L${x(series[series.length - 1].t).toFixed(1)},${y(y0)}L${x(series[0].t).toFixed(1)},${y(y0)}Z` }));
    svg.append(svgEl("path", { class: "line", d: path }));
    const last = series[series.length - 1];
    svg.append(svgEl("circle", { class: "dot", cx: x(last.t), cy: y(last.v), r: 4 }));
    const end = svgEl("text", { class: "end-label", x: x(last.t) + 8, y: y(last.v) + 4 });
    end.textContent = fmt(last.v);
    svg.append(end);
  } else {
    const days = Math.max(1, (t1 - t0) / DAY_MS + 1);
    const barW = Math.max(1, Math.min(24, (plotW / days) * 0.7));
    for (const p of series) {
      const top = y(Math.max(p.v, 0));
      const h = Math.max(Math.abs(y(p.v) - y(0)), p.v === 0 ? 0 : 1);
      const r = Math.min(4, barW / 2, h);
      const bx = x(p.t) - barW / 2;
      // 圓角朝向資料端，貼著基線的一端保持方角。
      const d = p.v >= 0
        ? `M${bx},${top + h}V${top + r}Q${bx},${top} ${bx + r},${top}H${bx + barW - r}Q${bx + barW},${top} ${bx + barW},${top + r}V${top + h}Z`
        : `M${bx},${top}V${top + h - r}Q${bx},${top + h} ${bx + r},${top + h}H${bx + barW - r}Q${bx + barW},${top + h} ${bx + barW},${top + h - r}V${top}Z`;
      svg.append(svgEl("path", { class: p.src === "b" ? "bar dim" : "bar", d }));
    }
  }

  const cross = svgEl("line", { class: "crosshair", y1: m.top, y2: m.top + plotH, visibility: "hidden" });
  const marker = svgEl("circle", { class: "dot", r: 4, visibility: "hidden" });
  svg.append(cross, marker);
  const tip = el("div", { class: "tooltip", hidden: true });
  host.append(svg, tip);

  let current = -1;
  function show(i) {
    current = Math.max(0, Math.min(series.length - 1, i));
    const p = series[current];
    cross.setAttribute("x1", x(p.t));
    cross.setAttribute("x2", x(p.t));
    cross.setAttribute("visibility", "visible");
    if (kind === "line") {
      marker.setAttribute("cx", x(p.t));
      marker.setAttribute("cy", y(p.v));
      marker.setAttribute("visibility", "visible");
    }
    const value = kind === "bar" && p.v > 0 ? `+${fmt(p.v)}` : fmt(p.v);
    tip.replaceChildren(
      el("strong", {}, el("span", { class: "key" }), value),
      el("span", {}, `${p.d} · ${p.src === "b" ? "回補" : "快照"}`),
    );
    tip.hidden = false;
    const scale = host.clientWidth / width;
    const left = x(p.t) * scale;
    tip.style.left = `${Math.min(left + 12, host.clientWidth - tip.offsetWidth)}px`;
    tip.style.top = `${m.top * scale}px`;
  }
  function hide() {
    cross.setAttribute("visibility", "hidden");
    marker.setAttribute("visibility", "hidden");
    tip.hidden = true;
  }
  function nearest(clientX) {
    const rect = svg.getBoundingClientRect();
    const t = t0 + ((clientX - rect.left) * (width / rect.width) - m.left) / plotW * (t1 - t0);
    let best = 0;
    for (let i = 1; i < series.length; i++) {
      if (Math.abs(series[i].t - t) < Math.abs(series[best].t - t)) best = i;
    }
    return best;
  }
  svg.addEventListener("pointermove", (e) => show(nearest(e.clientX)));
  svg.addEventListener("pointerleave", hide);
  svg.addEventListener("focus", () => show(current < 0 ? series.length - 1 : current));
  svg.addEventListener("blur", hide);
  svg.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") { show(current - 1); e.preventDefault(); }
    if (e.key === "ArrowRight") { show(current + 1); e.preventDefault(); }
  });
}

function inRange(items) {
  if (state.range === "all" || !items.length) return items;
  const cutoff = items[items.length - 1].t - (Number(state.range) - 1) * DAY_MS;
  return items.filter((p) => p.t >= cutoff);
}

function tile(label, value) {
  return el("div", { class: "tile" }, el("div", { class: "label" }, label), el("div", { class: "value" }, value));
}

async function viewRepo(nodeId) {
  let repo;
  try {
    repo = await getJSON(`data/repos/${nodeFileStem(nodeId)}.json`);
  } catch {
    return card("找不到這個 repo", null, el("p", { class: "empty" }, "資料可能尚未產生。"));
  }
  const points = repo.points.map(([d, v, src]) => ({ t: toTime(d), d, v, src }));
  const daily = repo.daily.map(([d, v, src]) => ({ t: toTime(d), d, v, src }));
  const latestPoint = points.length ? points[points.length - 1] : null;
  const stateLabel = { active: "每日追蹤", dormant: "每週追蹤", gone: "已刪除或轉為私人" }[repo.state] || repo.state;

  const lineHost = el("div", { class: "chart" });
  const barHost = el("div", { class: "chart" });
  const tableBody = el("tbody");
  const rangeButtons = el("div", { class: "seg", role: "group", "aria-label": "期間" });

  function redraw() {
    const p = inRange(points);
    const dd = inRange(daily);
    drawChart(lineHost, p, "line", `${repo.full_name} 的 star 數走勢，共 ${p.length} 個資料點`);
    drawChart(barHost, dd, "bar", `${repo.full_name} 每日新增 star，共 ${dd.length} 天`);
    const added = new Map(dd.map((q) => [q.d, q.v]));
    tableBody.replaceChildren(
      ...[...p].reverse().map((q) =>
        el("tr", {},
          el("td", {}, q.d),
          el("td", { class: "num" }, fmt(q.v)),
          el("td", { class: "num" }, added.has(q.d) ? fmt(added.get(q.d)) : "—"),
          el("td", {}, q.src === "b" ? "回補" : "快照"),
        ),
      ),
    );
    rangeButtons.replaceChildren(
      ...[["30", "30 天"], ["90", "90 天"], ["365", "1 年"], ["all", "全部"]].map(([value, label]) =>
        el("button", {
          type: "button",
          "aria-pressed": state.range === value ? "true" : "false",
          onclick: () => { state.range = value; redraw(); },
        }, label),
      ),
    );
  }

  const view = [
    el("p", {}, el("a", { href: "#/" }, "← 返回")),
    el("div", { class: "repo-head" },
      el("h1", {}, repo.full_name, " ", el("small", {}, githubLink(repo.full_name))),
      repo.description ? el("p", {}, repo.description) : null,
      repo.topics.length ? el("div", { class: "topics" }, repo.topics.map((t) => el("span", {}, t))) : null,
    ),
    el("div", { class: "tiles" },
      tile("目前 ★", latestPoint ? fmt(latestPoint.v) : "—"),
      tile("主要語言", repo.language || "—"),
      tile("首次上榜", repo.first_seen),
      tile("追蹤狀態", stateLabel),
    ),
    el("div", { class: "filters" }, rangeButtons),
    card("Star 數", "第一筆快照之前由 star history API 回補推算，之後為每日實測；游標停留處標示來源", lineHost),
    card("每日新增 star", "較淡的長條為回補資料；快照期間只在前後兩天都有資料時計算", barHost,
      el("details", {}, el("summary", {}, "資料表"),
        el("div", { class: "table-wrap" },
          el("table", {},
            el("thead", {}, el("tr", {}, el("th", {}, "日期"), el("th", { class: "num" }, "★"), el("th", { class: "num" }, "新增"), el("th", {}, "來源"))),
            tableBody)))),
  ];
  if (repo.trending.length) {
    view.push(card("Trending 上榜紀錄", `共 ${repo.trending.length} 次`,
      table([["日期"], ["榜單"], ["名次", "num"]], repo.trending.slice(0, 100).map(([d, period, lang, rank]) =>
        el("tr", {}, el("td", {}, d), el("td", {}, `${lang || "全部語言"} · ${PERIOD_LABEL[period] || period}`), el("td", { class: "num" }, rank))))));
  }
  // 圖表要在節點掛上頁面、量得到寬度之後才畫。
  requestAnimationFrame(redraw);
  return view;
}


async function setupSearch() {
  const input = document.getElementById("search-input");
  const list = document.getElementById("search-results");
  input.addEventListener("input", async () => {
    const q = input.value.trim().toLowerCase();
    if (!q) { list.hidden = true; return; }
    if (!state.index) {
      try { state.index = await getJSON("data/index.json"); } catch { return; }
    }
    const hits = state.index.filter((r) => r[1].toLowerCase().includes(q)).slice(0, 20);
    list.replaceChildren(
      ...hits.map(([nodeId, fullName, language, , stars]) =>
        el("li", {}, el("a", { href: `#/repo/${encodeURIComponent(nodeId)}`, onclick: () => { list.hidden = true; input.value = ""; } },
          el("span", {}, fullName),
          el("span", { class: "meta" }, `${language || ""} ★${fmt(stars)}`))),
      ),
    );
    if (!hits.length) list.append(el("li", { class: "empty" }, "找不到符合的 repo"));
    list.hidden = false;
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".search")) list.hidden = true;
  });
}


let renderSeq = 0;

async function render() {
  const seq = ++renderSeq;
  const view = document.getElementById("view");
  const hash = location.hash || "#/";
  const route = hash.startsWith("#/repo/") ? "repo" : { "#/": "trending", "#/new": "new", "#/gainers": "gainers", "#/alerts": "alerts" }[hash] || "trending";
  for (const a of document.querySelectorAll(".tabs a")) {
    if (a.dataset.route === route) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  let content;
  if (route === "repo") {
    let nodeId = null;
    try {
      nodeId = decodeURIComponent(hash.slice("#/repo/".length));
    } catch {
      nodeId = null;
    }
    content = nodeId === null
      ? card("找不到這個 repo", null, el("p", { class: "empty" }, "網址格式不正確。"))
      : await viewRepo(nodeId);
  } else if (route === "new") content = viewNew();
  else if (route === "gainers") content = viewGainers();
  else if (route === "alerts") content = viewAlerts();
  else content = viewTrending();
  // 等待資料時使用者已切到別的檢視，就不覆蓋較新的畫面。
  if (seq !== renderSeq) return;
  view.replaceChildren(...[content].flat());
}

async function main() {
  try {
    state.latest = await getJSON("data/latest.json");
  } catch (e) {
    document.getElementById("view").replaceChildren(el("p", { class: "empty" }, `無法載入資料：${e.message}`));
    return;
  }
  document.getElementById("as-of").textContent = state.latest.date ? `資料日期 ${state.latest.date}` : "尚無資料";
  setupSearch();
  window.addEventListener("hashchange", render);
  let width = window.innerWidth;
  window.addEventListener("resize", () => {
    if (Math.abs(window.innerWidth - width) > 40 && location.hash.startsWith("#/repo/")) {
      width = window.innerWidth;
      render();
    }
  });
  render();
}

main();
