'use strict';

const DATA_BASE = 'data/';      // 正式資料位置，格式見 Design System 的「資料格式」
const PAGE_SIZE = 24;          // 新上榜每次多顯示的筆數
const SKELETON_DELAY = 150;     // 載入超過這個毫秒數才顯示骨架，避免閃爍

class NotFound extends Error {}
const cache = new Map();
function getData(path) {
  if (cache.has(path)) return cache.get(path);
  const url = DATA_BASE + path.split('/').map(encodeURIComponent).join('/') + '.json';
  const p = fetch(url, { cache: 'no-cache' }).then(r => {
    if (r.status === 404) throw new NotFound(path);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  });
  cache.set(path, p);
  p.catch(() => cache.delete(path)); // 失敗不快取，重試時重新讀取
  return p;
}

const LANG_FILTERS = ['全部', 'Python', 'TypeScript', 'JavaScript', 'Java', 'Go', 'Rust'];
const LANG_COLORS = { Python: 'var(--lang-python)', TypeScript: 'var(--lang-typescript)', JavaScript: 'var(--lang-javascript)', Java: 'var(--lang-java)', Go: 'var(--lang-go)', Rust: 'var(--lang-rust)', Shell: 'var(--lang-shell)', C: 'var(--lang-c)', 'C++': 'var(--lang-cpp)' };
const PERIODS = { daily: '今日', weekly: '本週', monthly: '本月' };
const STATES = { active: '追蹤中', dormant: '休眠', gone: '已消失' };
const ROUTE_LABELS = { trending: 'Trending', new: '新上榜', daily: '日增排行', alerts: '爆紅警示' };
const langKey = l => (l === '全部' ? 'all' : l.toLowerCase());
const langLabel = key => LANG_FILTERS.find(l => langKey(l) === key) ?? key;

const $ = (s, r = document) => r.querySelector(s);
const fmt = n => n.toLocaleString('en-US');
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const splitName = full => { const i = full.indexOf('/'); return [full.slice(0, i), full.slice(i + 1)]; };
const repoHref = full => '#/repo/' + full.split('/').map(encodeURIComponent).join('/');
const nameHtml = full => { const [o, n] = splitName(full); return `<span class="own">${esc(o)}/</span>${esc(n)}`; };
const langHtml = lang => lang
  ? `<span class="lang"><i class="dot" style="--c:${LANG_COLORS[lang] || 'var(--muted)'}"></i>${esc(lang)}</span>`
  : '<span class="lang">未標示語言</span>';
const ICON = {
  star: '<svg class="icon" width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2.5l2.9 6.2 6.6.7-5 4.5 1.4 6.6L12 17.2l-5.9 3.3 1.4-6.6-5-4.5 6.6-.7z"/></svg>',
  back: '<svg class="icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>',
  external: '<svg class="icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
  github: $('.gh-link svg').outerHTML,
  auto: '<svg class="icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" stroke="none"/></svg>',
  light: '<svg class="icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  dark: '<svg class="icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M20.5 14.5A8.5 8.5 0 1 1 9.5 3.5a7 7 0 0 0 11 11z"/></svg>'
};

// 每個歷史項目在 history.state 存 { id, from }；捲動位置、已展開筆數、點過的連結、
// 圖表期間存在 entryStore[id]，並寫進 sessionStorage，重新整理也能還原。
if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
const STORE_KEY = 'starstrata-entries';
let entryStore = {};
try { entryStore = JSON.parse(sessionStorage.getItem(STORE_KEY) || '{}'); } catch (e) {}
const saveStore = () => { try { sessionStorage.setItem(STORE_KEY, JSON.stringify(entryStore)); } catch (e) {} };
const newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
const entry = () => (entryStore[history.state?.id] ||= {});
let lastScrollY = 0;
let currentRoute = null;

function parseRoute(hash = location.hash) {
  const raw = hash.replace(/^#\/?/, '');
  const qi = raw.indexOf('?');
  const path = qi < 0 ? raw : raw.slice(0, qi);
  const q = new URLSearchParams(qi < 0 ? '' : raw.slice(qi + 1));
  if (path.startsWith('repo/')) {
    let full;
    try { full = path.slice(5).split('/').map(decodeURIComponent).join('/'); } catch (e) { full = path.slice(5); }
    return { name: 'repo', full, hash };
  }
  const name = ROUTE_LABELS[path] ? path : 'trending';
  const lang = LANG_FILTERS.includes(q.get('lang')) ? q.get('lang') : '全部';
  const period = PERIODS[q.get('period')] ? q.get('period') : 'daily';
  return { name, lang, period, hash };
}
function trendingHash(lang, period) {
  const q = new URLSearchParams();
  if (lang !== '全部') q.set('lang', lang);
  if (period !== 'daily') q.set('period', period);
  const s = q.toString();
  return '#/trending' + (s ? '?' + s : '');
}
function routeLabel(r) {
  if (r.name === 'repo') return r.full;
  if (r.name === 'trending' && (r.lang !== '全部' || r.period !== 'daily'))
    return `Trending（${r.lang === '全部' ? '全部語言' : r.lang}・${PERIODS[r.period]}）`;
  return ROUTE_LABELS[r.name];
}

function renderKpis(meta) {
  if (!meta) {
    $('#kpis').innerHTML = ['資料日期', '追蹤中 repo', '今日新上榜', '今日爆紅'].map(t =>
      `<div class="kpi"><dt>${t}</dt><dd><span class="sk h24 w50"></span><span class="sr">載入中</span></dd></div>`).join('');
    return;
  }
  const first = meta.is_first_day, hot = meta.hot_count;
  $('#hdrDate').textContent = meta.data_date;
  $('#hdrDate').setAttribute('datetime', meta.data_date);
  $('#kpis').innerHTML = `
    <div class="kpi"><dt>資料日期</dt><dd class="mono">${meta.data_date}</dd></div>
    <div class="kpi"><dt>追蹤中 repo</dt><dd>${fmt(meta.tracked_count)}</dd></div>
    <div class="kpi"><dt>今日新上榜</dt>${first ? '<dd class="none">首日，明天起計算</dd>' : `<dd class="acc">${fmt(meta.new_count)}</dd>`}</div>
    <div class="kpi"><dt>今日爆紅</dt>${first ? '<dd class="none">首日，明天起計算</dd>' : `<dd class="${hot ? 'hot' : ''}">${hot ? '▲ ' : ''}${hot}</dd>`}</div>`;
}
function renderKpisError() {
  $('#kpis').innerHTML = ['資料日期', '追蹤中 repo', '今日新上榜', '今日爆紅'].map(t =>
    `<div class="kpi"><dt>${t}</dt><dd class="none">—<span class="sr">無法讀取</span></dd></div>`).join('');
}

function renderTabs(route, meta) {
  const items = [
    ['trending', 'Trending', ''],
    ['new', '新上榜', meta && !meta.is_first_day && meta.new_count ? `<span class="badge">${fmt(meta.new_count)}<span class="sr"> 筆</span></span>` : ''],
    ['daily', '日增排行', ''],
    ['alerts', '爆紅警示', meta && meta.hot_count ? `<span class="badge hot">${meta.hot_count}<span class="sr"> 筆今日警示</span></span>` : '']
  ];
  $('#tabs').innerHTML = items.map(([k, label, extra]) =>
    `<a class="tab" href="#/${k}"${route.name === k ? ' aria-current="page"' : ''}>${label}${extra}</a>`).join('');
}

function cardHtml(it, opts = {}) {
  const flags = (it.is_hot ? '<span class="flag hot">▲ 爆紅</span>' : '') + (it.is_new && opts.showNew !== false ? '<span class="flag new">新上榜</span>' : '');
  const text = it.zh_native ? it.description : (it.description_zh || it.description);
  const desc = text ? `<p class="desc">${esc(text)}</p>` : '<p class="desc none">（無說明）</p>';
  const ai = text && !it.zh_native && it.description_zh
    ? `<div class="c-foot"><button type="button" class="ai" aria-expanded="false">AI 翻譯</button>
         <div class="pop" role="tooltip"><span class="pop-l">原文</span>${it.description ? esc(it.description) : '<span style="color:var(--muted)">（未提供原文）</span>'}</div></div>`
    : '';
  return `<li><article class="card">
    <div class="c-top"><span class="rank"><span class="sr">第 </span>#${it.rank}<span class="sr"> 名</span></span>${langHtml(it.language)}${flags}</div>
    <h3 class="name"><a href="${repoHref(it.full_name)}">${nameHtml(it.full_name)}</a></h3>
    ${opts.created && it.created_at ? `<div class="created">建立於 <time datetime="${it.created_at}">${it.created_at}</time></div>` : ''}
    <div class="nums">${it.period_stars == null
      ? (opts.nullLabel ? `<span class="per src">${opts.nullLabel}</span>` : `<span class="delta">—</span><span class="per">${opts.unit || '今日'}</span>`)
      : `<span class="delta">+${fmt(it.period_stars)}</span><span class="per">${opts.unit || '今日'}</span>`}
      ${it.stargazers == null ? '' : `<span class="total" title="總 star">${ICON.star}<span class="sr">總 star </span>${fmt(it.stargazers)}</span>`}</div>
    ${desc}${ai}
  </article></li>`;
}

// kind: day1 | quiet | filter | error
function emptyHtml(kind, title, text, action = '') {
  let art;
  if (kind === 'error') {
    // 斷層：地層左右錯開
    art = '<path d="M0 50 L58 47 L58 55 L0 58 Z" fill="#5eead4"/><path d="M0 58 L58 55 L58 63 L0 66 Z" fill="#14b8a6"/><path d="M0 66 L58 63 L58 71 L0 73 Z" fill="#0f766e"/><path d="M0 73 L58 71 L58 80 L0 80 Z" fill="#1e3a8a"/>'
      + '<path d="M62 39 L120 36 L120 44 L62 47 Z" fill="#5eead4"/><path d="M62 47 L120 44 L120 52 L62 55 Z" fill="#14b8a6"/><path d="M62 55 L120 52 L120 60 L62 63 Z" fill="#0f766e"/><path d="M62 63 L120 60 L120 80 L62 80 Z" fill="#1e3a8a"/>'
      + '<path d="M60 30 L57 52 L63 60 L59 80" stroke="#fbbf24" stroke-width="2" fill="none"/>';
  } else {
    const layers = kind === 'day1'
      ? '<path d="M0 62 L120 56 L120 64 L0 70 Z" fill="#5eead4"/>'
      : '<path d="M0 50 L120 44 L120 52 L0 58 Z" fill="#5eead4"/><path d="M0 58 L120 52 L120 60 L0 66 Z" fill="#14b8a6"/><path d="M0 66 L120 60 L120 68 L0 73 Z" fill="#0f766e"/><path d="M0 73 L120 68 L120 80 L0 80 Z" fill="#1e3a8a"/>';
    const star = kind === 'filter'
      ? '<circle cx="92" cy="20" r="3" fill="#fbbf24"/><circle cx="70" cy="30" r="1.5" fill="#a7b4c8"/><circle cx="40" cy="16" r="1.5" fill="#a7b4c8"/>'
      : '<polygon points="92,10 94.6,17 102,17.4 96.2,22 98.2,29.2 92,25 85.8,29.2 87.8,22 82,17.4 89.4,17" fill="#fbbf24"/>';
    art = star + layers;
  }
  return `<div class="empty" role="status">
    <svg width="120" height="80" viewBox="0 0 120 80" aria-hidden="true"><rect width="120" height="80" rx="10" fill="#0f172a"/>${art}</svg>
    <h3>${title}</h3><p>${text}</p>${action}</div>`;
}
const errorHtml = () => emptyHtml('error', '資料讀取失敗', '可能是網路不穩，或今天的資料還在更新。請稍後再試。', '<button type="button" class="btn" data-retry>重試</button>');

const skCards = n => `<ol class="grid" aria-hidden="true">${Array.from({ length: n }, () => `<li><div class="card sk-card">
  <span class="sk w30"></span><span class="sk h24 w70"></span><span class="sk h28 w50"></span><span class="sk w90"></span><span class="sk w70"></span></div></li>`).join('')}</ol>`;
function skeletonHtml(route) {
  if (route.name === 'repo') return `<div aria-hidden="true" style="margin-top:72px"><span class="sk h28 w50"></span><div style="height:14px"></div><span class="sk w70"></span>
    <div class="tiles">${'<div class="kpi"><span class="sk w50"></span><span class="sk h28 w70"></span></div>'.repeat(4)}</div>
    <div class="panel"><span class="sk h200"></span></div></div>`;
  if (route.name === 'alerts') return `<div class="view-head"><h2 tabindex="-1">爆紅警示</h2></div><div class="table-wrap" aria-hidden="true" style="padding:18px;display:grid;gap:18px">${'<span class="sk w90"></span>'.repeat(4)}</div>`;
  const head = route.name === 'trending' ? filtersHtml(route) + '<div class="view-head compact"><h2 class="sr" tabindex="-1">Trending</h2><p>&nbsp;</p></div>'
    : `<div class="view-head"><h2 tabindex="-1">${ROUTE_LABELS[route.name]}</h2></div>`;
  return head + skCards(8);
}

function filtersHtml(route) {
  const chips = LANG_FILTERS.map(l =>
    `<button type="button" class="chip" data-lang="${l}" aria-pressed="${route.lang === l}">${LANG_COLORS[l] ? `<i class="dot" style="--c:${LANG_COLORS[l]}"></i>` : ''}${l}</button>`).join('');
  const seg = Object.entries(PERIODS).map(([k, v]) =>
    `<button type="button" data-period="${k}" aria-pressed="${route.period === k}">${v}</button>`).join('');
  return `<div class="filters" id="filters">
      <div class="chips" role="group" aria-label="語言">${chips}</div>
      <div class="seg" role="group" aria-label="期間">${seg}</div>
    </div>`;
}

async function viewTrending(route) {
  const data = await getData('trending');
  const list = data[route.period]?.[langKey(route.lang)] || [];
  const body = list.length
    ? `<ol class="grid" aria-label="Trending 清單">${list.map(it => cardHtml(it, { unit: PERIODS[route.period] })).join('')}</ol>`
    : emptyHtml('filter', `${esc(route.lang)} 目前沒有上榜的 repo`, `${PERIODS[route.period]}的 Trending 裡沒有 ${esc(route.lang)} 專案。換個語言或期間看看。`,
        '<button type="button" class="btn" data-lang="全部">顯示全部語言</button>');
  return filtersHtml(route) + `
    <div class="view-head compact"><h2 class="sr" tabindex="-1">Trending</h2>
      <p>${route.lang === '全部' ? '全部語言' : esc(route.lang)}・${PERIODS[route.period]}・共 ${list.length} 筆</p></div>
    ${body}`;
}

async function viewNew(route, meta) {
  const head = `<div class="view-head"><h2 tabindex="-1">今日新上榜</h2><p>今天第一次出現在 Trending 或 Search 的 repo，依今日新增 star 排序</p></div>`;
  if (meta.is_first_day) return head + emptyHtml('day1', '首日建立基準，明天開始計算', '今天是第一次收集資料，所有 repo 都算是「第一次出現」。明天起會列出真正新冒出來的專案。');
  const all = (await getData('new')).items;
  if (!all.length) return head + emptyHtml('quiet', '今天沒有新上榜的 repo', '今天的 Trending 與 Search 都是之前出現過的專案。', '<a class="btn" href="#/trending">看 Trending</a>');
  const shown = all.slice(0, entry().newShown || PAGE_SIZE);
  const rest = all.length - shown.length;
  return head + `<ol class="grid" aria-label="新上榜清單">${shown.map(it => cardHtml(it, { created: true, showNew: false, nullLabel: it.from_search ? '來自 Search，沒有今日 Trending 數字' : '' })).join('')}</ol>
    ${rest > 0 ? `<div class="more"><button type="button" class="btn" id="moreNew">顯示更多（還有 ${fmt(rest)} 筆）</button></div>` : `<p class="all-shown">已顯示全部 ${fmt(all.length)} 筆</p>`}`;
}

async function viewDaily(route, meta) {
  const head = `<div class="view-head"><h2 tabindex="-1">日增排行</h2><p>與前一天快照相比的 star 增加數，前 50 名</p></div>`;
  if (meta.is_first_day) return head + emptyHtml('day1', '首日建立基準，明天開始計算', '日增需要前一天的快照才能比較。今天的總 star 數已經記下，明天就會出現排行。');
  const list = (await getData('daily')).items.slice(0, 50);
  if (!list.length) return head + emptyHtml('quiet', '今天沒有日增資料', '前一天的快照裡沒有可以比較的 repo。');
  return head + `<ol class="grid" aria-label="日增排行">${list.map(it => cardHtml(it, { unit: '日增' })).join('')}</ol>`;
}

async function viewAlerts(route, meta) {
  const head = `<div class="view-head"><h2 tabindex="-1">爆紅警示</h2><p>最近 30 天，單日新增 star 明顯高於近 7 日平均的 repo</p></div>`;
  if (meta.is_first_day) return head + emptyHtml('day1', '首日建立基準，明天開始計算', '爆紅警示要拿當日新增和近 7 日平均比較，資料累積幾天後就會開始偵測。');
  const list = (await getData('alerts')).items;
  if (!list.length) return head + emptyHtml('quiet', '最近 30 天沒有爆紅警示', '夜空很平靜。某個 repo 單日新增 star 遠高於它近 7 日平均時，會出現在這裡。', '<a class="btn" href="#/daily">看日增排行</a>');
  const rows = [...list].sort((a, b) => b.date.localeCompare(a.date) || (alertRatio(b) ?? -1) - (alertRatio(a) ?? -1));
  const maxX = Math.max(1, ...rows.map(a => alertRatio(a) ?? 0));
  return head + `<div class="table-wrap"><table class="data">
    <caption class="sr">爆紅警示清單</caption>
    <thead><tr><th scope="col">日期</th><th scope="col">repo</th><th scope="col" class="num">當日新增 star</th><th scope="col" class="num">近 7 日平均</th><th scope="col">倍數</th></tr></thead>
    <tbody>${rows.map(a => `<tr>
        <td data-label="日期" class="mono">${a.date}</td>
        <td class="repo"><a href="${repoHref(a.full_name)}">${nameHtml(a.full_name)}</a></td>
        <td data-label="當日新增" class="num added">+${fmt(a.added)}</td>
        <td data-label="近 7 日平均" class="num">${a.baseline_7d == null ? '資料不足' : fmt(a.baseline_7d)}</td>
        <td data-label="倍數" class="wide">${alertRatio(a) == null ? '—' : `<span class="mult-bar" style="width:${Math.round(alertRatio(a) / maxX * 120)}px" aria-hidden="true"></span><span class="mult">×${alertRatio(a).toFixed(1)}</span>`}</td>
      </tr>`).join('')}</tbody></table></div>`;
}

const RANGES = { '30': ['30 天', 30], '90': ['90 天', 90], '365': ['1 年', 365], all: ['全部', Infinity] };
let detailSeries = null;

function backHtml() {
  const from = history.state?.from;
  if (from) return `<button type="button" class="btn back" data-back>${ICON.back}返回${/^[A-Za-z0-9]/.test(from.label) ? ' ' : ''}${esc(from.label)}</button>`;
  return `<a class="btn back" href="#/trending">${ICON.back}前往 Trending</a>`;
}

async function viewRepo(route) {
  let det;
  try { det = await getData('repos/' + route.full); }
  catch (e) {
    if (e instanceof NotFound) return backHtml() + emptyHtml('quiet', '找不到這個 repo', `starstrata 沒有追蹤 <b>${esc(route.full)}</b>。它可能還沒上過 Trending，或名稱有誤。`, '<a class="btn" href="#/trending">看 Trending</a>');
    throw e;
  }
  const full = det.full_name;
  const series = buildSeries(det);
  detailSeries = series;
  const total = series ? series[series.length - 1].c : (det.stargazers ?? null);
  const hasB = k => series.slice(-k).some(p => p.s === 'b');
  const none = '<dd class="none">尚無資料</dd>';
  const gainHtml = k => {
    const v = series && sumLast(series, k);
    return v === null ? none : `<dd class="acc">+${fmt(v)}${hasB(k) ? '<small>含回補</small>' : ''}</dd>`;
  };
  const ghUrl = 'https://github.com/' + full.split('/').map(encodeURIComponent).join('/');
  const zh = det.zh_native ? det.description : det.description_zh;
  const range = entry().range || 'all';

  const tiles = `<dl class="tiles" aria-label="star 統計">
    <div class="kpi"><dt>總 star</dt>${total !== null ? `<dd>${fmt(total)}</dd>` : none}</div>
    <div class="kpi"><dt>近 7 日新增</dt>${gainHtml(7)}</div>
    <div class="kpi"><dt>近 30 日新增</dt>${gainHtml(30)}</div>
    <div class="kpi"><dt>Trending 上榜天數</dt><dd>${fmt(new Set((det.trending || []).map(r => r[0])).size)}<small>天</small></dd></div>
  </dl>`;

  const rangeSeg = Object.entries(RANGES).map(([k, [label]]) =>
    `<button type="button" data-range="${k}" aria-pressed="${range === k}">${label}</button>`).join('');
  const charts = series ? `
    <div class="range-row"><h3 class="sr">star 歷史</h3>
      <div class="seg" role="group" aria-label="圖表期間">${rangeSeg}</div></div>
    <section class="panel" aria-labelledby="c1t">
      <div class="panel-head"><h3 id="c1t">star 累計</h3><span class="sub" id="c1sub"></span>
        <div class="legend"><span><i class="lg-line"></i>回補推算</span><span><i class="lg-line s"></i>每日實測</span></div></div>
      <div class="chart" id="chartCum" tabindex="0" role="group" aria-roledescription="折線圖" aria-label="star 累計折線圖，可用左右方向鍵逐日查看"></div>
    </section>
    <section class="panel" aria-labelledby="c2t">
      <div class="panel-head"><h3 id="c2t">每日新增 star</h3><span class="sub" id="c2sub"></span>
        <div class="legend"><span><i class="lg-bar"></i>回補推算</span><span><i class="lg-bar s"></i>每日實測</span></div></div>
      <div class="chart" id="chartDaily" tabindex="0" role="group" aria-roledescription="長條圖" aria-label="每日新增 star 長條圖，可用左右方向鍵查看"></div>
    </section>`
    : `<section class="panel"><div class="panel-head"><h3>star 歷史</h3></div>
        <div class="chart-empty"><div><b>歷史資料收集中</b><br>首次收錄後，每天會累積一筆實測資料。</div></div></section>`;

  const trRows = [...(det.trending || [])].sort((a, b) => b[0].localeCompare(a[0]));
  const table = `<h3 class="section-title">Trending 上榜紀錄</h3>` + (trRows.length
    ? `<div class="table-wrap"><table class="data"><caption class="sr">Trending 上榜紀錄</caption>
      <thead><tr><th scope="col">日期</th><th scope="col">期間</th><th scope="col">語言</th><th scope="col" class="num">名次</th></tr></thead>
      <tbody>${trRows.map(([dt, p, l, rk]) => `<tr><td data-label="日期" class="mono">${dt}</td><td data-label="期間">${PERIODS[p] || esc(p)}</td><td data-label="語言">${l ? esc(langLabel(l)) : '全部語言'}</td><td data-label="名次" class="num"><b>#${rk}</b></td></tr>`).join('')}</tbody></table></div>`
    : '<div class="chart-empty" style="height:120px">還沒有上榜紀錄</div>');

  return `${backHtml()}
    <div class="d-head">
      <h2 tabindex="-1">${nameHtml(full)}</h2>
      <div class="d-badges">
        <span class="state ${esc(det.state)}">${STATES[det.state] || esc(det.state)}</span>
        ${det.archived ? '<span class="state archived">已封存（archived）</span>' : ''}
        <a class="btn" href="${ghUrl}" target="_blank" rel="noopener noreferrer">${ICON.github}在 GitHub 開啟${ICON.external}<span class="sr">（另開分頁）</span></a>
      </div>
      <div class="d-desc">
        ${zh ? `<p>${esc(zh)}${det.zh_native ? '' : ' <span class="ai" style="cursor:default">AI 翻譯</span>'}</p>` : '<p style="color:var(--muted)">（無說明）</p>'}
        ${det.description && !det.zh_native ? `<p class="orig" lang="en"><b lang="zh-Hant">原文</b>${esc(det.description)}</p>` : ''}
      </div>
      <dl class="d-meta">
        <div><dt>語言</dt><dd>${langHtml(det.language)}</dd></div>
        <div><dt>建立日期</dt><dd class="mono">${esc(det.created_at || '—')}</dd></div>
        <div><dt>首次收錄</dt><dd class="mono">${esc(det.first_seen || '—')}</dd></div>
      </dl>
      ${det.topics?.length ? `<ul class="topics" aria-label="topics">${det.topics.map(t => `<li>${esc(t)}</li>`).join('')}</ul>` : ''}
    </div>
    ${tiles}${charts}${table}`;
}

const SVGNS = 'http://www.w3.org/2000/svg';
function niceTicks(min, max, count = 4) {
  if (max === min) max = min + 1;
  const raw = (max - min) / count, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw);
  const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Math.round(v));
  return { lo, hi, ticks, step };
}
// 刻度間距 ≥ 1 萬時以「萬」表示，否則顯示完整數字，避免相鄰刻度四捨五入成同一個值
function shortNum(v, step) {
  if (v === 0) return '0';
  if (step >= 10000) return (v / 10000).toFixed(v % 10000 ? 1 : 0) + ' 萬';
  return fmt(v);
}
function xTicks(data, plotW) {
  const days = data.length, maxN = Math.max(2, Math.floor(plotW / 90));
  let cand;
  if (days > 730) {
    cand = data.map((p, i) => ({ p, i })).filter(({ p }) => p.date.endsWith('-01-01')).map(({ p, i }) => ({ i, label: p.date.slice(0, 4) }));
    if (!cand.length) cand = [{ i: 0, label: data[0].date.slice(0, 4) }];
  } else if (days > 100) {
    cand = data.map((p, i) => ({ p, i })).filter(({ p }) => p.date.endsWith('-01')).map(({ p, i }) => ({ i, label: p.date.slice(0, 7) }));
  } else {
    cand = data.map((p, i) => ({ p, i })).filter(({ i }) => (days - 1 - i) % 7 === 0).map(({ p, i }) => ({ i, label: p.date.slice(5) }));
  }
  const step = Math.ceil(cand.length / maxN);
  return cand.filter((_, k) => (cand.length - 1 - k) % step === 0);
}
function el(tag, attrs, parent) {
  const e = document.createElementNS(SVGNS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}

// kind: 'cum'（累計折線）| 'bar'（每日新增長條）
function drawChart(box, full, kind, subEl, rangeKey) {
  const days = RANGES[rangeKey][1];
  const data = days === Infinity ? full : full.slice(-days);
  const W = box.clientWidth, H = box.clientHeight;
  if (!W || !H) return;
  const m = { l: 64, r: 12, t: 12, b: 28 };
  const pw = W - m.l - m.r, ph = H - m.t - m.b;
  box.innerHTML = '';
  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, 'aria-hidden': 'true' }, box);

  let items, binDays = 1;
  if (kind === 'bar') {
    // 點數太多時分組（7 日／30 日合計）；分組不跨越來源切換點，實測資料自成一組
    if (data.length > pw / 3) binDays = data.length / 7 <= pw / 3 ? 7 : 30;
    items = [];
    for (let end = data.length; end > 0;) {
      let start = Math.max(0, end - binDays);
      for (let k = end - 2; k >= start; k--) if (data[k].s !== data[end - 1].s) { start = k + 1; break; }
      const chunk = data.slice(start, end);
      end = start;
      const known = chunk.filter(p => p.n !== null);
      items.unshift({ from: chunk[0].date, to: chunk[chunk.length - 1].date, v: known.length ? known.reduce((s, p) => s + p.n, 0) : null, s: chunk[0].s });
    }
    if (subEl) subEl.textContent = binDays === 1 ? '每日' : `每 ${binDays} 日合計`;
  } else {
    items = data;
    if (subEl) subEl.textContent = `${data[0].date} – ${data[data.length - 1].date}`;
  }

  const vals = kind === 'bar' ? items.map(d => d.v ?? 0) : items.map(d => d.c);
  const y = niceTicks(kind === 'bar' ? 0 : Math.min(...vals), Math.max(...vals));
  const sy = v => m.t + ph - (v - y.lo) / (y.hi - y.lo) * ph;
  const n = items.length;
  const sx = kind === 'bar' ? i => m.l + (i + 0.5) * pw / n : i => m.l + (n === 1 ? pw / 2 : i / (n - 1) * pw);

  for (const v of y.ticks) {
    el('line', { x1: m.l, x2: W - m.r, y1: sy(v), y2: sy(v), class: 'grid-l' }, svg);
    el('text', { x: m.l - 8, y: sy(v) + 5, 'text-anchor': 'end' }, svg).textContent = shortNum(v, y.step);
  }
  el('line', { x1: m.l, x2: W - m.r, y1: m.t + ph, y2: m.t + ph, class: 'axis-l' }, svg);
  for (const tk of xTicks(data, pw)) {
    const xi = kind === 'bar' ? m.l + (tk.i + 0.5) * pw / data.length : m.l + (data.length === 1 ? pw / 2 : tk.i / (data.length - 1) * pw);
    el('line', { x1: xi, x2: xi, y1: m.t + ph, y2: m.t + ph + 4, class: 'axis-l' }, svg);
    el('text', { x: xi, y: H - 6, 'text-anchor': 'middle' }, svg).textContent = tk.label;
  }

  if (kind === 'cum') {
    // 降採樣：每像素最多約 1.5 點，保留來源切換點與末點
    const firstS = items.findIndex(p => p.s === 's');
    const stride = Math.max(1, Math.floor(n / (pw * 1.5)));
    const pick = (a, b) => { const out = []; for (let i = a; i <= b; i += stride) out.push(i); if (out[out.length - 1] !== b) out.push(b); return out; };
    const path = idx => idx.map((i, k) => `${k ? 'L' : 'M'}${sx(i).toFixed(1)},${sy(items[i].c).toFixed(1)}`).join('');
    const bEnd = firstS === -1 ? n - 1 : Math.max(0, firstS - 1);
    if (firstS !== 0) el('path', { d: path(pick(0, bEnd)), class: 'ln-b' }, svg);
    if (firstS !== -1) {
      el('path', { d: path(pick(Math.max(0, firstS - 1), n - 1)), class: 'ln-s' }, svg);
      for (let i = firstS; i < n; i += stride) el('circle', { cx: sx(i), cy: sy(items[i].c), r: 4, class: 'pt-s' }, svg);
    }
  } else {
    const bw = Math.max(1, pw / n - (pw / n > 4 ? 2 : 0));
    items.forEach((d, i) => {
      const h = Math.max(d.v > 0 ? 1 : 0, m.t + ph - sy(d.v ?? 0));
      el('rect', { x: sx(i) - bw / 2, y: m.t + ph - h, width: bw, height: h, rx: bw > 6 ? 2 : 0, class: d.s === 'b' ? 'bar-b' : 'bar-s' }, svg);
    });
  }

  const hover = el('g', { style: 'display:none' }, svg);
  const hl = kind === 'bar' ? el('rect', { class: 'bar-hl', y: m.t, height: ph }, hover) : null;
  const cross = el('line', { class: 'cross', y1: m.t, y2: m.t + ph }, hover);
  const dot = kind === 'cum' ? el('circle', { r: 5, class: 'hover-pt' }, hover) : null;
  const tip = document.createElement('div');
  tip.className = 'tip'; tip.hidden = true;
  box.appendChild(tip);

  let cur = -1;
  const show = i => {
    cur = Math.max(0, Math.min(n - 1, i));
    const d = items[cur], x = sx(cur);
    hover.style.display = '';
    cross.setAttribute('x1', x); cross.setAttribute('x2', x);
    if (dot) { dot.setAttribute('cx', x); dot.setAttribute('cy', sy(d.c)); }
    if (hl) { const w = pw / n; hl.setAttribute('x', x - w / 2); hl.setAttribute('width', w); }
    const srcLabel = d.s === 's' ? '每日實測' : '回補推算';
    const dateLabel = kind === 'bar' && d.from !== d.to ? `${d.from} – ${d.to}` : (d.date || d.from);
    const valLabel = kind === 'cum' ? `${fmt(d.c)} ★` : d.v === null ? '無資料' : `+${fmt(d.v)}`;
    tip.innerHTML = `<div class="t-date">${dateLabel}</div><div class="t-val">${valLabel}</div><div class="t-src ${d.s === 's' ? 's' : ''}"><i></i>${srcLabel}</div>`;
    tip.hidden = false;
    const tw = tip.offsetWidth;
    let left = x + 12;
    if (left + tw > W) left = x - tw - 12;
    tip.style.left = Math.max(0, left) + 'px';
    $('#live').textContent = `${dateLabel}，${kind === 'cum' ? '累計' : '新增'} ${valLabel}，${srcLabel}`;
  };
  const hide = () => { hover.style.display = 'none'; tip.hidden = true; cur = -1; };
  const idxAt = px => kind === 'bar' ? Math.floor((px - m.l) / pw * n) : Math.round((px - m.l) / pw * (n - 1));
  box.onpointermove = e => { const r = box.getBoundingClientRect(); show(idxAt(e.clientX - r.left)); };
  box.onpointerleave = hide;
  box.onblur = hide;
  box.onkeydown = e => {
    const step = e.shiftKey ? 10 : 1;
    if (e.key === 'ArrowRight') show(cur < 0 ? n - 1 : cur + step);
    else if (e.key === 'ArrowLeft') show(cur < 0 ? n - 1 : cur - step);
    else if (e.key === 'Home') show(0);
    else if (e.key === 'End') show(n - 1);
    else if (e.key === 'Escape') hide();
    else return;
    e.preventDefault();
  };
}
function drawCharts() {
  if (!detailSeries) return;
  const range = entry().range || 'all';
  const a = $('#chartCum'), b = $('#chartDaily');
  if (a) drawChart(a, detailSeries, 'cum', $('#c1sub'), range);
  if (b) drawChart(b, detailSeries, 'bar', $('#c2sub'), range);
}
const chartRO = new ResizeObserver(() => drawCharts());

// mode：'new' 新頁面（捲到頂、焦點移到標題）｜'restore' 返回（還原捲動與焦點）｜
//       'update' 同一頁更新（保留捲動）｜'init' 第一次載入
let renderToken = 0;
async function render(mode = 'update') {
  const token = ++renderToken;
  const route = parseRoute();
  currentRoute = route;
  detailSeries = null;
  chartRO.disconnect();
  const view = $('#view');
  document.title = (route.name === 'repo' ? route.full : ROUTE_LABELS[route.name]) + ' — starstrata';
  renderTabs(route, null);

  let skeletonShown = false;
  const skTimer = setTimeout(() => {
    if (token !== renderToken) return;
    skeletonShown = true;
    renderKpis(null);
    view.innerHTML = skeletonHtml(route);
    view.setAttribute('aria-busy', 'true');
    $('#live').textContent = '載入中';
    afterPaint(mode, route, true);
  }, SKELETON_DELAY);

  try {
    const meta = await getData('meta');
    const viewFn = { trending: viewTrending, new: viewNew, daily: viewDaily, alerts: viewAlerts, repo: viewRepo }[route.name];
    const html = await viewFn(route, meta);
    if (token !== renderToken) return;
    clearTimeout(skTimer);
    renderKpis(meta);
    renderTabs(route, meta);
    view.innerHTML = html;
    view.removeAttribute('aria-busy');
    if (skeletonShown) $('#live').textContent = '已載入';
  } catch (e) {
    if (token !== renderToken) return;
    clearTimeout(skTimer);
    console.error(e);
    renderKpisError();
    view.innerHTML = (route.name === 'trending' ? filtersHtml(route) : '') + errorHtml();
    view.removeAttribute('aria-busy');
    $('#live').textContent = '資料讀取失敗';
  }
  if (detailSeries) {
    drawCharts();
    chartRO.observe($('#chartCum'));
  }
  measureSticky();
  afterPaint(mode, route, false);
}

// 處理捲動與焦點。骨架階段只捲到頂端，不移焦點；內容到了再完成還原。
function afterPaint(mode, route, isSkeleton) {
  const view = $('#view');
  if (mode === 'restore') {
    if (isSkeleton) return;
    const st = entry();
    window.scrollTo(0, st.scrollY || 0);
    const target = st.focusHref && [...view.querySelectorAll('a[href]')].find(a => a.getAttribute('href') === st.focusHref);
    if (target) target.focus({ preventScroll: true });
    // 以免內容高度不足造成捲動被截斷，等圖表繪製後再確認一次
    requestAnimationFrame(() => window.scrollTo(0, st.scrollY || 0));
  } else if (mode === 'new') {
    window.scrollTo(0, 0);
    if (!isSkeleton) view.querySelector('h2')?.focus({ preventScroll: true });
  } else if (mode === 'init' && isSkeleton === false && history.state && entry().scrollY) {
    // 重新整理：還原捲動
    window.scrollTo(0, entry().scrollY);
  }
}

function measureSticky() {
  const tabsH = $('#tabs').offsetHeight;
  const filters = $('#filters');
  const desktop = matchMedia('(min-width: 721px)').matches;
  document.documentElement.style.setProperty('--tabs-h', tabsH + 'px');
  document.documentElement.style.setProperty('--sticky-h', (tabsH + (filters && desktop ? filters.offsetHeight : 0) + 8) + 'px');
  updateStuck();
}
function updateStuck() {
  const f = $('#filters');
  if (!f) return;
  const top = parseFloat(getComputedStyle(f).top) || 0;
  f.classList.toggle('is-stuck', getComputedStyle(f).position === 'sticky' && f.getBoundingClientRect().top <= top + 0.5 && window.scrollY > 0);
}
window.addEventListener('resize', measureSticky);

// 點連結前記下：這一頁點的是哪個連結（返回時把焦點還給它）
document.addEventListener('click', e => {
  const a = e.target.closest('a[href^="#/"]');
  if (a && !e.defaultPrevented && !e.metaKey && !e.ctrlKey && !e.shiftKey && e.button === 0) entry().focusHref = a.getAttribute('href');
}, true);

let scrollTick = false;
window.addEventListener('scroll', () => {
  lastScrollY = window.scrollY;
  if (!scrollTick) { scrollTick = true; requestAnimationFrame(() => { scrollTick = false; updateStuck(); }); }
}, { passive: true });

window.addEventListener('hashchange', e => {
  // 離開的那一頁：保存捲動位置（以 oldURL 對應的項目為準）
  if (prevEntryId) { (entryStore[prevEntryId] ||= {}).scrollY = lastScrollY; }
  const prevRoute = parseRoute(new URL(e.oldURL).hash);
  if (!history.state?.id) {
    // 新的瀏覽：記錄來源頁，供「返回」使用
    history.replaceState({ id: newId(), from: { hash: prevRoute.hash, label: routeLabel(prevRoute) } }, '');
    prevEntryId = history.state.id;
    saveStore();
    render('new');
  } else {
    // 上一頁／下一頁
    prevEntryId = history.state.id;
    render('restore');
  }
});
let prevEntryId = null;

window.addEventListener('pagehide', () => { entry().scrollY = window.scrollY; saveStore(); });
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') { entry().scrollY = window.scrollY; saveStore(); } });

document.addEventListener('click', e => {
  const t = e.target.closest('button');
  if (!t) return;
  if (t.dataset.lang || t.dataset.period) {
    // 篩選寫進網址但不新增歷史紀錄，返回時直接回到上一個頁面
    const r = currentRoute.name === 'trending' ? currentRoute : parseRoute('#/trending');
    const lang = t.dataset.lang || r.lang, period = t.dataset.period || r.period;
    history.replaceState(history.state, '', trendingHash(lang, period));
    render('update').then(() => $(t.dataset.lang ? `#filters [data-lang="${CSS.escape(lang)}"]` : `#filters [data-period="${period}"]`)?.focus({ preventScroll: true }));
  } else if (t.dataset.range) {
    entry().range = t.dataset.range;
    document.querySelectorAll('[data-range]').forEach(b => b.setAttribute('aria-pressed', b.dataset.range === t.dataset.range));
    drawCharts();
  } else if (t.id === 'moreNew') {
    const before = document.querySelectorAll('#view .card').length;
    entry().newShown = (entry().newShown || PAGE_SIZE) + PAGE_SIZE;
    render('update').then(() => document.querySelectorAll('#view .card .name a')[before]?.focus());
  } else if (t.hasAttribute('data-back')) {
    history.back();
  } else if (t.hasAttribute('data-retry')) {
    render('update');
  } else if (t.classList.contains('ai')) {
    const open = t.getAttribute('aria-expanded') !== 'true';
    document.querySelectorAll('.ai[aria-expanded="true"]').forEach(b => b.setAttribute('aria-expanded', 'false'));
    t.setAttribute('aria-expanded', String(open));
  }
});
document.addEventListener('click', e => {
  if (!e.target.closest('.ai')) document.querySelectorAll('.ai[aria-expanded="true"]').forEach(b => b.setAttribute('aria-expanded', 'false'));
}, true);
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') document.querySelectorAll('.ai[aria-expanded="true"]').forEach(b => b.setAttribute('aria-expanded', 'false'));
});

const THEMES = { auto: '跟隨系統', light: '淺色', dark: '深色' };
let theme = 'auto';
try { const t = localStorage.getItem('starstrata-theme'); if (THEMES[t]) theme = t; } catch (e) {}
function applyTheme() {
  if (theme === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', theme);
  const btn = $('#themeBtn');
  btn.innerHTML = ICON[theme] + `<span class="sr">主題：${THEMES[theme]}</span>`;
  btn.title = `主題：${THEMES[theme]}（點擊切換）`;
}
$('#themeBtn').addEventListener('click', () => {
  document.documentElement.classList.add('no-transition');
  requestAnimationFrame(() => requestAnimationFrame(() => document.documentElement.classList.remove('no-transition')));
  theme = { auto: 'light', light: 'dark', dark: 'auto' }[theme];
  try { theme === 'auto' ? localStorage.removeItem('starstrata-theme') : localStorage.setItem('starstrata-theme', theme); } catch (e) {}
  applyTheme();
  $('#live').textContent = `主題：${THEMES[theme]}`;
});

const MAX_RESULTS = 8;
const q = $('#q'), panel = $('#qPanel'), listEl = $('#qList'), foot = $('#qFoot');
let searchIndex = null, indexState = 'idle'; // idle | loading | ready | error
let results = [], active = -1;

function loadIndex() {
  if (indexState === 'loading' || indexState === 'ready') return;
  indexState = 'loading';
  getData('search-index').then(rows => {
    searchIndex = rows.map(([full, lang, stars]) => ({ full, lang, stars, key: full.toLowerCase(), nameKey: full.toLowerCase().split('/')[1] || '' }));
    indexState = 'ready';
    if (q.value.trim()) updateSearch();
  }, () => { indexState = 'error'; if (q.value.trim()) updateSearch(); });
}
function highlight(full, term) {
  const i = full.toLowerCase().indexOf(term);
  if (i < 0 || !term) return nameHtml(full);
  const [o] = splitName(full);
  const seg = (s, a) => {
    const lo = Math.max(i, a), hi = Math.min(i + term.length, a + s.length);
    if (lo >= hi) return esc(s);
    return esc(s.slice(0, lo - a)) + '<mark>' + esc(s.slice(lo - a, hi - a)) + '</mark>' + esc(s.slice(hi - a));
  };
  const ownPart = o + '/';
  return `<span class="own">${seg(ownPart, 0)}</span>${seg(full.slice(ownPart.length), ownPart.length)}`;
}
function openPanel() { panel.hidden = false; q.setAttribute('aria-expanded', 'true'); }
function updateSearch() {
  const raw = q.value.trim(), term = raw.toLowerCase();
  if (!term) { closeSearch(); return; }
  results = []; active = -1;
  if (indexState !== 'ready') {
    listEl.innerHTML = '';
    foot.innerHTML = indexState === 'error'
      ? '<div class="dd-empty"><span>搜尋索引讀取失敗</span><button type="button" class="btn" id="qRetry">重試</button></div>'
      : '<div class="dd-empty">載入搜尋索引中…</div>';
    if (indexState === 'idle') loadIndex();
    openPanel(); syncActive();
    return;
  }
  const all = searchIndex.filter(r => r.key.includes(term))
    .sort((a, b) => (b.nameKey.startsWith(term) - a.nameKey.startsWith(term)) || b.stars - a.stars);
  results = all.slice(0, MAX_RESULTS);
  active = results.length ? 0 : -1;
  listEl.innerHTML = results.map((r, i) => `<li class="dd-item" role="option" id="opt${i}" aria-selected="${i === active}" data-full="${esc(r.full)}">
      <span class="dd-name">${highlight(r.full, term)}</span><span class="dd-star">★ ${fmt(r.stars)}</span>
      <span class="dd-meta">${langHtml(r.lang)}</span></li>`).join('');
  foot.innerHTML = !results.length
    ? `<div class="dd-empty">找不到符合「${esc(raw)}」的 repo</div>`
    : `<div class="dd-foot">符合 ${fmt(all.length)} 筆${all.length > MAX_RESULTS ? `，顯示前 ${MAX_RESULTS} 筆` : ''}・↑↓ 選擇・Enter 開啟</div>`;
  openPanel(); syncActive();
  $('#live').textContent = results.length ? `${all.length} 筆符合` : '沒有符合的 repo';
}
function syncActive() {
  listEl.querySelectorAll('.dd-item').forEach((li, i) => li.setAttribute('aria-selected', i === active));
  if (active >= 0) { q.setAttribute('aria-activedescendant', 'opt' + active); $('#opt' + active).scrollIntoView({ block: 'nearest' }); }
  else q.removeAttribute('aria-activedescendant');
}
function closeSearch() { panel.hidden = true; q.setAttribute('aria-expanded', 'false'); q.removeAttribute('aria-activedescendant'); active = -1; }
function openRepo(full) { closeSearch(); q.value = ''; q.blur(); location.hash = repoHref(full); }

q.addEventListener('focus', () => { loadIndex(); if (q.value.trim()) updateSearch(); });
q.addEventListener('input', updateSearch);
q.addEventListener('keydown', e => {
  if (e.key === 'ArrowDown' && results.length) { active = (active + 1) % results.length; syncActive(); e.preventDefault(); }
  else if (e.key === 'ArrowUp' && results.length) { active = (active - 1 + results.length) % results.length; syncActive(); e.preventDefault(); }
  else if (e.key === 'Enter' && active >= 0) { openRepo(results[active].full); e.preventDefault(); }
  else if (e.key === 'Escape') { if (!panel.hidden) closeSearch(); else { q.value = ''; q.blur(); } }
});
panel.addEventListener('mousedown', e => {
  const li = e.target.closest('.dd-item');
  if (li) { e.preventDefault(); openRepo(li.dataset.full); return; }
  if (e.target.closest('#qRetry')) { e.preventDefault(); indexState = 'idle'; loadIndex(); updateSearch(); }
});
listEl.addEventListener('mousemove', e => { const li = e.target.closest('.dd-item'); if (li) { active = [...listEl.children].indexOf(li); syncActive(); } });
document.addEventListener('mousedown', e => { if (!e.target.closest('.search')) closeSearch(); });
document.addEventListener('keydown', e => {
  if (e.key === '/' && !e.ctrlKey && !e.metaKey && !e.altKey && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) && !document.activeElement.isContentEditable) { e.preventDefault(); q.focus(); }
});

applyTheme();
if (!history.state?.id) history.replaceState({ id: newId(), from: null }, '');
prevEntryId = history.state.id;
render('init');
