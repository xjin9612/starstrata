'use strict';
/* starstrata dashboard 前端。純原生 JavaScript，不經過建置。
 * 資料格式見 docs/DEVELOPMENT.md「Dashboard」；介面文字在 i18n.js。 */

/* =====================================================================
 * 設定與語言
 * ===================================================================== */
const DATA_BASE = 'data/';
const PAGE_SIZE = 24;          // 新上榜每次多顯示的筆數
const SKELETON_DELAY = 150;    // 載入超過這個毫秒數才顯示骨架，避免閃爍
const STALE_HOURS = 36;        // collected_at（收集完成時間）距今超過這個時數就提示資料過期；GitHub 排程常延後數小時（實測 7.5 小時），提示條只表示流程停擺
const SEEN_MAX = 5000;         // 「上次造訪後新出現」最多記住的 repo 數

// 語言由 site_page.py 依 site.locale 寫入的 <html lang> 決定
const LOCALE = document.documentElement.lang.toLowerCase().startsWith('en') ? 'en' : 'zh-TW';
const DICT = window.I18N[LOCALE];
document.documentElement.lang = DICT['html.lang'];
const t = (key, vars = {}) => (DICT[key] ?? key).replace(/\{(\w+)\}/g, (_, n) => (vars[n] ?? ''));

function applyStaticText() {
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll('[data-i18n-aria]').forEach(el => el.setAttribute('aria-label', t(el.dataset.i18nAria)));
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => el.setAttribute('placeholder', t(el.dataset.i18nPlaceholder)));
}

/* =====================================================================
 * 資料讀取
 * ===================================================================== */
class NotFound extends Error {}
const cache = new Map();

function fetchJSON(path) {
  const url = DATA_BASE + path.split('/').map(encodeURIComponent).join('/') + '.json';
  return fetch(url, { cache: 'no-cache' }).then(r => {
    if (r.status === 404) throw new NotFound(path);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  });
}

function getData(path) {
  if (cache.has(path)) return cache.get(path);
  const p = fetchJSON(path);
  cache.set(path, p);
  p.catch(() => cache.delete(path)); // 失敗不快取，重試時重新讀取
  return p;
}

/* =====================================================================
 * 常數與工具
 * ===================================================================== */
const LANG_NAMES = { python: 'Python', typescript: 'TypeScript', javascript: 'JavaScript', java: 'Java', go: 'Go', rust: 'Rust' };
const LANG_COLORS = { Python: 'var(--lang-python)', TypeScript: 'var(--lang-typescript)', JavaScript: 'var(--lang-javascript)', Java: 'var(--lang-java)', Go: 'var(--lang-go)', Rust: 'var(--lang-rust)', Shell: 'var(--lang-shell)', C: 'var(--lang-c)', 'C++': 'var(--lang-cpp)' };
const PERIOD_ORDER = ['daily', 'weekly', 'monthly'];
const ROUTES = ['trending', 'new', 'daily', 'alerts', 'reports'];
const REPORT_KINDS = ['weekly', 'monthly'];
const langName = key => (key === 'all' ? t('filter.all') : LANG_NAMES[key] || key);
// 「期間・語言」榜名，例如「本週・Rust」「本月・全部語言」；language 為 null 表示總榜
const boardName = m => t('board', { period: t('period.' + m.period), lang: m.language == null ? t('summary.allLangs') : langName(m.language) });
const isMissing = (meta, period, lang) => (meta.run?.trending_missing || []).some(m => m.period === period && (m.language ?? 'all') === lang);

const $ = (s, r = document) => r.querySelector(s);
const fmt = n => Number(n).toLocaleString('en-US');
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const splitName = full => { const i = full.indexOf('/'); return [full.slice(0, i), full.slice(i + 1)]; };
const repoHref = full => '#/repo/' + full.split('/').map(encodeURIComponent).join('/');
const nameHtml = full => { const [o, n] = splitName(full); return `<span class="own">${esc(o)}/</span>${esc(n)}`; };
const langHtml = lang => lang
  ? `<span class="lang"><i class="dot" style="--c:${LANG_COLORS[lang] || 'var(--muted)'}"></i>${esc(lang)}</span>`
  : `<span class="lang">${t('card.noLang')}</span>`;
function fmtTime(iso, tz, withDate) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: tz, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
    .formatToParts(new Date(iso)).map(p => [p.type, p.value]));
  return (withDate ? `${parts.month}-${parts.day} ` : '') + `${parts.hour}:${parts.minute}`;
}

const ICON = {
  star: '<svg class="icon" width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2.5l2.9 6.2 6.6.7-5 4.5 1.4 6.6L12 17.2l-5.9 3.3 1.4-6.6-5-4.5 6.6-.7z"/></svg>',
  back: '<svg class="icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>',
  external: '<svg class="icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
  note: '<svg class="icon note-ic" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-5 4z"/><path d="M8 8h8M8 12h5"/></svg>',
  report: '<svg class="icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h7M9 17h5"/></svg>',
  warn: '<svg class="icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17.5v.01"/></svg>',
  github: '<svg class="icon" width="18" height="18" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 0 1-5.45 7.59c-.4.08-.55-.17-.55-.38 0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-1.59-.82-2.15.08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.73.9.82 1.13.16.45.68 1.31 2.69.94 0 .67.01 1.3.01 1.49 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0-4.42 3.58-8 8-8Z"/></svg>',
  auto: '<svg class="icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" stroke="none"/></svg>',
  light: '<svg class="icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  dark: '<svg class="icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M20.5 14.5A8.5 8.5 0 1 1 9.5 3.5a7 7 0 0 0 11 11z"/></svg>'
};

/* =====================================================================
 * 路由與歷史紀錄
 * 每筆歷史紀錄的 history.state 存 { id, from }；捲動位置、已展開筆數、點過的連結、
 * 圖表期間存在 entryStore[id]，並寫進 sessionStorage，重新整理也能還原。
 * ===================================================================== */
if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
const STORE_KEY = 'starstrata-entries';
let entryStore = {};
try { entryStore = JSON.parse(sessionStorage.getItem(STORE_KEY) || '{}'); } catch (e) {}
const saveStore = () => { try { sessionStorage.setItem(STORE_KEY, JSON.stringify(entryStore)); } catch (e) {} };
const newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
const entry = () => (entryStore[history.state?.id] ||= {});
let lastScrollY = 0;
let currentRoute = null;
let prevEntryId = null;

// Trending 的篩選寫在網址：#/trending?lang=python&period=weekly&sort=stars（預設值不寫）
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
  const rm = /^reports\/(weekly|monthly)\/([0-9A-Za-z-]+)$/.exec(path);
  if (rm) return { name: 'report', kind: rm[1], period: rm[2], hash };
  if (path === 'reports') return { name: 'reports', kind: REPORT_KINDS.includes(q.get('kind')) ? q.get('kind') : 'all', hash };
  return {
    name: ROUTES.includes(path) ? path : 'trending',
    lang: q.get('lang') || 'all',
    period: q.get('period') || 'daily',
    sort: q.get('sort') === 'stars' ? 'stars' : 'rank',
    hash
  };
}
function trendingHash(r) {
  const q = new URLSearchParams();
  if (r.lang !== 'all') q.set('lang', r.lang);
  if (r.period !== 'daily') q.set('period', r.period);
  if (r.sort !== 'rank') q.set('sort', r.sort);
  const s = q.toString();
  return '#/trending' + (s ? '?' + s : '');
}
function routeLabel(r) {
  if (r.name === 'repo') return r.full;
  if (r.name === 'report') return t('reports.title.' + r.kind, { period: r.period });
  if (r.name === 'reports' && r.kind !== 'all') return LOCALE === 'en' ? `${t('route.reports')} (${t('reports.kind.' + r.kind)})` : `${t('route.reports')}（${t('reports.kind.' + r.kind)}）`;
  if (r.name === 'trending') {
    const parts = [];
    if (r.lang !== 'all' || r.period !== 'daily') parts.push(r.lang === 'all' ? t('summary.allLangs') : langName(r.lang), t('period.' + r.period));
    if (r.sort === 'stars') parts.push(t('sort.stars'));
    if (parts.length) return LOCALE === 'en' ? `Trending (${parts.join(' · ')})` : `Trending（${parts.join('・')}）`;
  }
  return t('route.' + r.name);
}

/* =====================================================================
 * 上次造訪後新出現：localStorage 記錄看過的 repo（最多 SEEN_MAX 筆）
 * ===================================================================== */
const SEEN_KEY = 'starstrata-seen';
let seenStore = {};
try { seenStore = JSON.parse(localStorage.getItem(SEEN_KEY) || '{}') || {}; } catch (e) { seenStore = null; }
const prevSeen = new Set(seenStore ? Object.keys(seenStore) : []);
const firstVisit = prevSeen.size === 0;
function isUnseen(name) {
  if (!seenStore || firstVisit) return false;
  return !prevSeen.has(name);
}
function recordSeen(names, date) {
  if (!seenStore) return;
  for (const n of names) seenStore[n] = date;
  const keys = Object.keys(seenStore);
  if (keys.length > SEEN_MAX) keys.sort((a, b) => seenStore[a].localeCompare(seenStore[b])).slice(0, keys.length - SEEN_MAX).forEach(k => delete seenStore[k]);
  try { localStorage.setItem(SEEN_KEY, JSON.stringify(seenStore)); } catch (e) {}
}

/* =====================================================================
 * 共用區塊
 * ===================================================================== */
let META = null;

function renderHeaderAndNotice(meta) {
  const tz = meta.timezone || 'Asia/Taipei';
  $('#hdrDate').textContent = meta.data_date || '—';
  if (meta.data_date) $('#hdrDate').setAttribute('datetime', meta.data_date);
  // 新鮮度看 collected_at（收集完成時間）；generated_at 只是建置網站的時間，收集失敗時仍會是新的
  $('#hdrUpd').textContent = meta.collected_at ? t('hdr.updated', { time: fmtTime(meta.collected_at, tz) }) : '';
  const msgs = [];
  if (meta.collected_at && Date.now() - Date.parse(meta.collected_at) > STALE_HOURS * 3600e3)
    msgs.push(t('notice.stale', { time: fmtTime(meta.collected_at, tz, true) }));
  // meta.run 是當天所有執行合併後的狀態；為 null（找不到 run.json）時不提示
  const run = meta.run;
  if (run?.trending_missing?.length)
    msgs.push(t('notice.missing', { date: meta.data_date, list: run.trending_missing.map(boardName).join(LOCALE === 'en' ? ', ' : '、') }));
  if (run && run.search_ok === false) msgs.push(t('notice.search', { date: meta.data_date }));
  if (run?.repos_missing > 0) msgs.push(t(run.repos_missing === 1 ? 'notice.reposOne' : 'notice.repos', { date: meta.data_date, n: fmt(run.repos_missing) }));
  const n = $('#notice');
  n.hidden = !msgs.length;
  n.innerHTML = msgs.length ? `${ICON.warn}<div>${msgs.map(esc).join('<br>')}</div>` : '';
}

function renderKpis(meta) {
  const labels = ['kpi.date', 'kpi.tracked', 'kpi.new', 'kpi.hot'];
  if (meta === undefined) {
    $('#kpis').innerHTML = labels.map(k => `<div class="kpi"><dt>${t(k)}</dt><dd><span class="sk h24 w50"></span><span class="sr">${t('kpi.loading')}</span></dd></div>`).join('');
    return;
  }
  if (meta === null) {
    $('#kpis').innerHTML = labels.map(k => `<div class="kpi"><dt>${t(k)}</dt><dd class="none">—<span class="sr">${t('kpi.unavailable')}</span></dd></div>`).join('');
    return;
  }
  const first = meta.is_first_day, hot = meta.hot_count;
  const fd = `<dd class="none">${t('kpi.firstDay')}</dd>`;
  $('#kpis').innerHTML = `
    <div class="kpi"><dt>${t('kpi.date')}</dt><dd class="mono">${esc(meta.data_date || '—')}${meta.collected_at ? `<small class="kpi-upd">${esc(t('kpi.updated', { time: fmtTime(meta.collected_at, meta.timezone || 'Asia/Taipei') }))}</small>` : ''}</dd></div>
    <div class="kpi"><dt>${t('kpi.tracked')}</dt><dd>${fmt(meta.tracked_count)}</dd></div>
    <div class="kpi"><dt>${t('kpi.new')}</dt>${first ? fd : `<dd class="acc">${fmt(meta.new_count)}</dd>`}</div>
    <div class="kpi"><dt>${t('kpi.hot')}</dt>${first ? fd : `<dd class="${hot ? 'hot' : ''}">${hot ? '▲ ' : ''}${fmt(hot)}</dd>`}</div>`;
}

function renderTabs(route, meta) {
  const items = [
    ['trending', ''],
    ['new', meta && !meta.is_first_day && meta.new_count ? `<span class="badge">${fmt(meta.new_count)}<span class="sr">${t('badge.items')}</span></span>` : ''],
    ['daily', ''],
    ['alerts', meta && meta.hot_count ? `<span class="badge hot">${fmt(meta.hot_count)}<span class="sr">${t('badge.alerts')}</span></span>` : ''],
    ['reports', meta && meta.reports_new ? `<span class="badge">${t('reports.newBadge')}<span class="sr">${esc(t('reports.newSr', { n: meta.reports_new }))}</span></span>` : '']
  ];
  $('#tabs').innerHTML = items.map(([k, extra]) =>
    `<a class="tab" href="#/${k}"${route.name === k || (k === 'reports' && route.name === 'report') ? ' aria-current="page"' : ''}>${t('route.' + k)}${extra}</a>`).join('');
}

/* ===== 卡片 ===== */
function rankChange(it) {
  const prevDate = META?.trending_prev_date;
  if (!prevDate || !('prev_rank' in it)) return '';
  const d = prevDate.slice(5);
  if (it.prev_rank === null) {
    if (it.is_new) return ''; // 已經有「新上榜」標記
    return `<span class="rk-chg in" title="${esc(t('card.inSr', { date: d }))}"><span aria-hidden="true">${t('card.in')}</span><span class="sr">${esc(t('card.inSr', { date: d }))}</span></span>`;
  }
  const diff = it.prev_rank - it.rank;
  if (diff === 0) return `<span class="sr">${esc(t('card.sameSr', { date: d }))}</span>`;
  const up = diff > 0, n = Math.abs(diff);
  const sr = t(up ? 'card.upSr' : 'card.downSr', { n, date: d });
  return `<span class="rk-chg ${up ? 'up' : 'down'}" title="${esc(sr)}" aria-hidden="true">${t(up ? 'card.up' : 'card.down', { n })}</span><span class="sr">${esc(sr)}</span>`;
}

function sparkHtml(vals) {
  if (!Array.isArray(vals)) return '';
  const nums = vals.filter(v => v !== null);
  if (!nums.length) return `<span class="sr">${t('card.sparkNone')}</span>`;
  // star 減少時日增為負，下緣取最小值，走勢線不會超出圖框
  const W = 72, H = 22, P = 3, max = Math.max(...nums, 1), min = Math.min(...nums, 0);
  const x = i => P + i * (W - 2 * P) / (vals.length - 1), y = v => H - P - (v - min) / (max - min) * (H - 2 * P);
  let d = '', pen = false, lastI = -1;
  vals.forEach((v, i) => {
    if (v === null) { pen = false; return; }
    d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true; lastI = i;
  });
  const dots = vals.map((v, i) => {
    const isolated = v !== null && (i === 0 || vals[i - 1] === null) && (i === vals.length - 1 || vals[i + 1] === null);
    return (isolated || i === lastI) && v !== null ? `<circle class="sp-d" cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="${i === lastI ? 2.5 : 1.5}"/>` : '';
  }).join('');
  return `<svg class="spark" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true"><line class="sp-b" x1="${P}" x2="${W - P}" y1="${y(0).toFixed(1)}" y2="${y(0).toFixed(1)}"/><path class="sp-l" d="${d}"/>${dots}</svg><span class="sr">${esc(t('card.spark', { n: fmt(nums.reduce((a, b) => a + b, 0)) }))}</span>`;
}

// 翻譯目標由 meta.translation_lang 決定（config 的 translation.target），沒設定時只顯示原文
const trLang = () => META?.translation_lang || null;
const isTranslated = it => !!(trLang() && !it.tr_native && it.description_tr);
const descText = it => (isTranslated(it) ? it.description_tr : it.description);
function trLangName() {
  try { return new Intl.DisplayNames([DICT['html.lang']], { type: 'language' }).of(trLang()); } catch (e) { return trLang(); }
}

function cardHtml(it, opts = {}) {
  const flags = (it.is_hot ? `<span class="flag hot">${t('card.hot')}</span>` : '') + (it.is_new && opts.showNew !== false ? `<span class="flag new">${t('card.new')}</span>` : '');
  const text = descText(it);
  const desc = text ? `<p class="desc"${isTranslated(it) ? ` lang="${esc(trLang())}"` : ''}>${esc(text)}</p>` : `<p class="desc none">${t('card.noDesc')}</p>`;
  // 只有顯示譯文時才標「AI 翻譯」，與介面語言無關
  const translated = isTranslated(it);
  const ai = translated
    ? `<div class="c-foot"><button type="button" class="ai" aria-expanded="false" title="${esc(t('card.aiTitle', { lang: trLangName() }))}">${t('card.ai')}</button>
         <div class="pop" role="tooltip"><span class="pop-l">${t('card.original')}</span>${it.description ? esc(it.description) : `<span style="color:var(--muted)">${t('card.noOriginal')}</span>`}</div></div>`
    : '';
  const unseen = opts.markSeen && isUnseen(it.full_name);
  return `<li><article class="card">
    <div class="c-top">
      <span class="rk"><span class="rank"><span class="sr">${esc(t('card.rank', { n: it.rank }))} </span><span aria-hidden="true">#${it.rank}</span></span>${opts.showChange ? rankChange(it) : ''}</span>
      ${langHtml(it.language)}
      ${it.stargazers == null ? '' : `<span class="total" title="${t('card.total')}">${ICON.star}<span class="sr">${t('card.total')} </span>${fmt(it.stargazers)}</span>`}
      ${flags ? `<span class="flags">${flags}</span>` : ''}
    </div>
    <div class="name-row">
      ${unseen ? `<span class="seen-dot" title="${t('card.seen')}"></span><span class="sr">${t('card.seen')}</span>` : ''}
      <h3 class="name"><a href="${repoHref(it.full_name)}">${nameHtml(it.full_name)}</a></h3>
      ${it.has_note ? `<span title="${t('card.note')}">${ICON.note}</span><span class="sr">${t('card.note')}</span>` : ''}
    </div>
    ${opts.created && it.created_at ? `<div class="created">${esc(t('card.created', { date: it.created_at }))}</div>` : ''}
    <div class="nums">${it.period_stars == null
      ? (opts.nullLabel ? `<span class="per src">${opts.nullLabel}</span>` : `<span class="delta">—</span><span class="per">${opts.unit}</span>`)
      : `<span class="delta">+${fmt(it.period_stars)}</span><span class="per">${opts.unit}</span>`}${sparkHtml(it.spark)}</div>
    ${desc}${ai}
  </article></li>`;
}

// kind: day1 | quiet | filter | error
function emptyHtml(kind, title, text, action = '') {
  const L = ['#5eead4', '#2dd4bf', '#14b8a6', '#0f766e'];
  let art;
  if (kind === 'error') {
    // 斷層：地層左右錯開
    art = `<path d="M0 50 L58 47 L58 55 L0 58 Z" fill="${L[0]}"/><path d="M0 58 L58 55 L58 63 L0 66 Z" fill="${L[1]}"/><path d="M0 66 L58 63 L58 71 L0 73 Z" fill="${L[2]}"/><path d="M0 73 L58 71 L58 80 L0 80 Z" fill="${L[3]}"/>`
      + `<path d="M62 39 L120 36 L120 44 L62 47 Z" fill="${L[0]}"/><path d="M62 47 L120 44 L120 52 L62 55 Z" fill="${L[1]}"/><path d="M62 55 L120 52 L120 60 L62 63 Z" fill="${L[2]}"/><path d="M62 63 L120 60 L120 80 L62 80 Z" fill="${L[3]}"/>`
      + '<path d="M60 30 L57 52 L63 60 L59 80" stroke="#fbbf24" stroke-width="2" fill="none"/>';
  } else {
    const layers = kind === 'day1'
      ? `<path d="M0 62 L120 56 L120 64 L0 70 Z" fill="${L[0]}"/>`
      : `<path d="M0 50 L120 44 L120 52 L0 58 Z" fill="${L[0]}"/><path d="M0 58 L120 52 L120 60 L0 66 Z" fill="${L[1]}"/><path d="M0 66 L120 60 L120 68 L0 73 Z" fill="${L[2]}"/><path d="M0 73 L120 68 L120 80 L0 80 Z" fill="${L[3]}"/>`;
    const star = kind === 'filter'
      ? '<circle cx="92" cy="20" r="3" fill="#fbbf24"/><circle cx="70" cy="30" r="1.5" fill="#a7b4c8"/><circle cx="40" cy="16" r="1.5" fill="#a7b4c8"/>'
      : '<polygon points="92,10 94.6,17 102,17.4 96.2,22 98.2,29.2 92,25 85.8,29.2 87.8,22 82,17.4 89.4,17" fill="#fbbf24"/>';
    art = star + layers;
  }
  return `<div class="empty" role="status">
    <svg width="120" height="80" viewBox="0 0 120 80" aria-hidden="true"><rect width="120" height="80" rx="10" fill="#0f172a"/>${art}</svg>
    <h3>${title}</h3><p>${text}</p>${action}</div>`;
}
const errorHtml = () => emptyHtml('error', t('error.title'), t('error.text'), `<button type="button" class="btn" data-retry>${t('error.retry')}</button>`);

/* ===== 載入骨架 ===== */
const skCards = n => `<ol class="grid" aria-hidden="true">${Array.from({ length: n }, () => `<li><div class="card sk-card">
  <span class="sk w30"></span><span class="sk h24 w70"></span><span class="sk h28 w50"></span><span class="sk w90"></span><span class="sk w70"></span></div></li>`).join('')}</ol>`;
function skeletonHtml(route) {
  if (route.name === 'reports' || route.name === 'report') return `<div class="view-head"><h2 tabindex="-1">${t('reports.title')}</h2></div><div class="table-wrap" aria-hidden="true" style="padding:18px;display:grid;gap:18px">${'<span class="sk w90"></span>'.repeat(6)}</div>`;
  if (route.name === 'repo') return `<div aria-hidden="true" style="margin-top:72px"><span class="sk h28 w50"></span><div style="height:14px"></div><span class="sk w70"></span>
    <div class="tiles">${'<div class="kpi"><span class="sk w50"></span><span class="sk h28 w70"></span></div>'.repeat(4)}</div>
    <div class="panel"><span class="sk h200"></span></div></div>`;
  if (route.name === 'alerts') return `<div class="view-head"><h2 tabindex="-1">${t('alerts.title')}</h2></div><div class="table-wrap" aria-hidden="true" style="padding:18px;display:grid;gap:18px">${'<span class="sk w90"></span>'.repeat(4)}</div>`;
  const title = route.name === 'trending' ? `<h2 class="sr" tabindex="-1">${t('route.trending')}</h2>` : `<h2 tabindex="-1">${t(route.name + '.title')}</h2>`;
  return `<div class="view-head">${title}</div>` + skCards(8);
}

/* =====================================================================
 * 頁面
 * ===================================================================== */
function filtersHtml(route, langs, periods) {
  const chips = langs.map(l =>
    `<button type="button" class="chip" data-lang="${esc(l)}" aria-pressed="${route.lang === l}">${l !== 'all' && LANG_COLORS[langName(l)] ? `<i class="dot" style="--c:${LANG_COLORS[langName(l)]}"></i>` : ''}${esc(langName(l))}</button>`).join('');
  const per = periods.map(k => `<button type="button" data-period="${k}" aria-pressed="${route.period === k}">${t('period.' + k)}</button>`).join('');
  const sort = ['rank', 'stars'].map(k => `<button type="button" data-sort="${k}" aria-pressed="${route.sort === k}">${t('sort.' + k)}</button>`).join('');
  return `<div class="filters" id="filters">
      <div class="chips" role="group" aria-label="${t('filter.lang')}">${chips}</div>
      <div class="f-right">
        <div class="seg" role="group" aria-label="${t('filter.period')}">${per}</div>
        <div class="seg" role="group" aria-label="${t('filter.sort')}">${sort}</div>
      </div>
    </div>`;
}

async function viewTrending(route, meta) {
  const data = await getData('trending');
  const periods = PERIOD_ORDER.filter(p => data[p]);
  if (!periods.includes(route.period)) route.period = periods[0] || 'daily';
  const langs = ['all', ...Object.keys(data[route.period] || {}).filter(k => k !== 'all')];
  if (!langs.includes(route.lang)) route.lang = 'all';
  if (trendingHash(route) !== location.hash && location.hash.startsWith('#/trending')) history.replaceState(history.state, '', trendingHash(route));
  let list = data[route.period]?.[route.lang] || [];
  if (route.sort === 'stars') list = [...list].sort((a, b) => (b.period_stars ?? -1) - (a.period_stars ?? -1) || a.rank - b.rank);
  recordSeenLater(route, list, meta);
  const lname = route.lang === 'all' ? t('summary.allLangs') : langName(route.lang);
  const body = list.length
    ? `<ol class="grid" aria-label="${t('list.trending')}">${list.map(it => cardHtml(it, { unit: t('unit.' + route.period), showChange: route.sort === 'rank', markSeen: true })).join('')}</ol>`
    : isMissing(meta, route.period, route.lang)
    ? emptyHtml('error', esc(t('empty.missing', { board: boardName({ period: route.period, language: route.lang === 'all' ? null : route.lang }) })), esc(t('empty.missingText', { date: meta.data_date })),
        route.lang === 'all' ? '' : `<button type="button" class="btn" data-lang="all">${t('empty.showAll')}</button>`)
    : emptyHtml('filter', esc(t('empty.filter', { lang: lname })), esc(t('empty.filterText', { period: t('period.' + route.period), lang: lname })),
        `<button type="button" class="btn" data-lang="all">${t('empty.showAll')}</button>`);
  return filtersHtml(route, langs, periods) + `
    <div class="view-head compact"><h2 class="sr" tabindex="-1">${t('route.trending')}</h2>
      <p>${esc(t('summary', { lang: lname, period: t('period.' + route.period), n: list.length }))}${route.sort === 'stars' ? esc(t('summary.byStars')) : ''}</p></div>
    ${body}`;
}

async function viewNew(route, meta) {
  const head = `<div class="view-head"><h2 tabindex="-1">${t('new.title')}</h2><p>${t('new.desc')}</p></div>`;
  if (meta.is_first_day) return head + emptyHtml('day1', t('empty.day1'), t('new.day1'));
  const all = (await getData('new')).items;
  if (!all.length) return head + emptyHtml('quiet', t('new.none'), t('new.noneText'), `<a class="btn" href="#/trending">${t('empty.viewTrending')}</a>`);
  const shown = all.slice(0, entry().newShown || PAGE_SIZE);
  recordSeenLater(route, shown, meta);
  const rest = all.length - shown.length;
  return head + `<ol class="grid" aria-label="${t('new.list')}">${shown.map(it => cardHtml(it, { unit: t('unit.daily'), created: true, showNew: false, markSeen: true, nullLabel: it.from_search ? t('card.fromSearch') : '' })).join('')}</ol>
    ${rest > 0 ? `<div class="more"><button type="button" class="btn" id="moreNew">${esc(t('new.more', { n: fmt(rest) }))}</button></div>` : `<p class="all-shown">${esc(t('new.allShown', { n: fmt(all.length) }))}</p>`}`;
}

async function viewDaily(route, meta) {
  const head = `<div class="view-head"><h2 tabindex="-1">${t('daily.title')}</h2><p>${t('daily.desc')}</p></div>`;
  if (meta.is_first_day) return head + emptyHtml('day1', t('empty.day1'), t('daily.day1'));
  const list = (await getData('daily')).items.slice(0, 50);
  if (!list.length) return head + emptyHtml('quiet', t('daily.none'), t('daily.noneText'));
  recordSeenLater(route, list, meta);
  return head + `<ol class="grid" aria-label="${t('daily.list')}">${list.map(it => cardHtml(it, { unit: t('unit.gain'), markSeen: true })).join('')}</ol>`;
}

async function viewAlerts(route, meta) {
  const rule = meta.alert_rule; // 後端一定輸出，不在前端寫死預設值
  const d = rule.baseline_days;
  const ruleText = t('alerts.rule', { d, m: rule.multiplier, min: fmt(rule.min_daily_stars), cool: rule.cooldown_days ? t('alerts.cool', { c: rule.cooldown_days }) : '' });
  const head = `<div class="view-head"><h2 tabindex="-1">${t('alerts.title')}</h2><p>${t('alerts.desc')}</p></div><p class="rule">${esc(ruleText)}</p>`;
  if (meta.is_first_day) return head + emptyHtml('day1', t('empty.day1'), esc(t('alerts.day1', { d })));
  const list = (await getData('alerts')).items;
  if (!list.length) return head + emptyHtml('quiet', t('alerts.none'), t('alerts.noneText'), `<a class="btn" href="#/daily">${t('empty.viewDaily')}</a>`);
  const rows = [...list].sort((a, b) => b.date.localeCompare(a.date) || (alertRatio(b) ?? -1) - (alertRatio(a) ?? -1) || b.added - a.added);
  const maxX = Math.max(1, ...rows.map(a => alertRatio(a) ?? 0));
  const base = esc(t('alerts.base', { d }));
  return head + `<div class="table-wrap"><table class="data">
    <caption class="sr">${t('alerts.caption')}</caption>
    <thead><tr><th scope="col">${t('alerts.date')}</th><th scope="col">${t('alerts.repo')}</th><th scope="col" class="num">${t('alerts.added')}</th><th scope="col" class="num">${base}</th><th scope="col">${t('alerts.mult')}</th></tr></thead>
    <tbody>${rows.map(a => {
      const x = alertRatio(a);
      return `<tr>
        <td data-label="${t('alerts.date')}" class="mono">${esc(a.date)}</td>
        <td class="repo"><a href="${repoHref(a.full_name)}">${nameHtml(a.full_name)}</a></td>
        <td data-label="${t('alerts.addedShort')}" class="num added">+${fmt(a.added)}</td>
        <td data-label="${base}" class="num">${a.baseline == null ? t('alerts.noBase') : fmt(a.baseline)}</td>
        <td data-label="${t('alerts.mult')}" class="wide">${x == null ? '—' : `<span class="mult-bar" style="width:${Math.round(x / maxX * 120)}px" aria-hidden="true"></span><span class="mult">×${x.toFixed(1)}</span>`}</td>
      </tr>`;
    }).join('')}</tbody></table></div>`;
}

/* ===== 報告 ===== */
// 期間結束日距最新資料日期 REPORT_NEW_DAYS 天內的報告標為新報告，與 meta.reports_new 一致
const REPORT_NEW_DAYS = 7;
const isNewReport = (r, meta) => !!meta.data_date && toMs(meta.data_date) - toMs(r.end) <= REPORT_NEW_DAYS * DAY;
const reportHref = r => `#/reports/${r.kind}/${encodeURIComponent(r.period)}`;
const shortRange = r => `${r.start.slice(5)} – ${r.end.slice(5)}`;
function reportsNotice() {
  // 報告目前只有繁體中文；介面是其他語言時提示
  return LOCALE === 'zh-TW' ? '' : `<p class="rule">${t('reports.zhOnly')}</p>`;
}

async function viewReports(route, meta) {
  const kinds = ['all', ...REPORT_KINDS];
  const seg = kinds.map(k => `<button type="button" data-rkind="${k}" aria-pressed="${route.kind === k}">${t('reports.kind.' + k)}</button>`).join('');
  const head = `<div class="view-head"><h2 tabindex="-1">${t('reports.title')}</h2><p>${t('reports.desc')}</p></div>
    <div class="filters rp-filters"><div class="seg" role="group" aria-label="${t('reports.kindLabel')}">${seg}</div></div>${reportsNotice()}`;
  const all = (await getData('reports/index')).items;
  const list = all.filter(r => route.kind === 'all' || r.kind === route.kind);
  if (!list.length) return head + emptyHtml('quiet', t('reports.none'), t('reports.noneText'));
  const groups = new Map();
  for (const r of list) { const y = r.end.slice(0, 4); if (!groups.has(y)) groups.set(y, []); groups.get(y).push(r); }
  return head + [...groups].map(([y, rs]) => `<section class="rp-year" aria-labelledby="rpy${esc(y)}"><h3 class="section-title" id="rpy${esc(y)}">${esc(y)}</h3>
    <ol class="rp-list">${rs.map(r => {
      const fresh = isNewReport(r, meta);
      return `<li><article class="rp-item">
        <div class="rp-top"><span class="rp-kind ${esc(r.kind)}">${t('reports.kind.' + r.kind)}</span><span class="mono">${esc(r.period)}</span><span class="rp-range">${esc(shortRange(r))}</span>${fresh ? `<span class="flag new">${t('reports.newBadge')}</span>` : ''}</div>
        <h4 class="name"><a href="${reportHref(r)}">${esc(t('reports.title.' + r.kind, { period: r.period }))}</a></h4>
        ${r.top?.length ? `<p class="rp-tops"><span class="sr">${t('reports.top')}</span>${r.top.map(n => `<span>${nameHtml(n)}</span>`).join('')}</p>` : ''}
      </article></li>`;
    }).join('')}</ol></section>`).join('');
}

async function viewReport(route) {
  const key = `reports/${route.kind}/${route.period}`;
  let rp;
  try { rp = await getData(key); }
  catch (e) {
    if (e instanceof NotFound) return backHtml('#/reports', t('reports.toList')) + emptyHtml('quiet', t('reports.notFound'), esc(t('reports.notFoundText', { period: route.period })), `<a class="btn" href="#/reports">${t('reports.toList')}</a>`);
    throw e;
  }
  const idx = (await getData('reports/index')).items.filter(r => r.kind === rp.kind);
  const i = idx.findIndex(r => r.period === rp.period);
  const newer = i > 0 ? idx[i - 1] : null, older = i >= 0 && i < idx.length - 1 ? idx[i + 1] : null;
  const nav = (newer || older) ? `<nav class="rp-nav" aria-label="${t('reports.navLabel')}">
      ${older ? `<a class="btn" href="${reportHref(older)}">${ICON.back}${esc(t('reports.older', { period: older.period }))}</a>` : '<span></span>'}
      ${newer ? `<a class="btn" href="${reportHref(newer)}">${esc(t('reports.newer', { period: newer.period }))}<span class="rot">${ICON.back}</span></a>` : ''}
    </nav>` : '';
  return `${backHtml('#/reports', t('reports.toList'))}
    <div class="rp-head">
      <h2 tabindex="-1">${esc(t('reports.title.' + rp.kind, { period: rp.period }))}</h2>
      <p class="rp-meta"><span class="mono">${esc(rp.start)} – ${esc(rp.end)}</span>
        ${rp.url ? `<a href="${esc(rp.url)}" target="_blank" rel="noopener noreferrer">${t('reports.source')} ↗<span class="sr">${t('newTab')}</span></a>` : ''}</p>
    </div>
    ${reportsNotice()}
    <div class="report" lang="zh-Hant">${rp.html}</div>
    ${nav}`;
}

/* ===== repo 詳細頁 ===== */
// 每日序列、近 N 日新增、爆紅倍數都用 calc.js，由 tests/test_calc.py 測試。
const RANGES = { '30': 30, '90': 90, '365': 365, all: Infinity };
// 每日序列的來源標記：b 回補、s 每日實測、i 快照缺日的線性補齊（calc.js）
const SRC_LABEL = { b: 'chart.backfill', s: 'chart.measured', i: 'chart.estimated' };
let detailSeries = null;

function backHtml(fallbackHref = '#/trending', fallbackLabel = t('repo.toTrending')) {
  const from = history.state?.from;
  if (from) {
    const label = from.label;
    const sep = LOCALE === 'en' || /^[A-Za-z0-9]/.test(label) ? ' ' : '';
    return `<button type="button" class="btn back" data-back>${ICON.back}${esc(t('repo.back', { sep, label }))}</button>`;
  }
  return `<a class="btn back" href="${fallbackHref}">${ICON.back}${fallbackLabel}</a>`;
}

async function viewRepo(route) {
  let det;
  try { det = await getData('repos/' + route.full); }
  catch (e) {
    if (e instanceof NotFound) return backHtml() + emptyHtml('quiet', t('repo.notFound'), t('repo.notFoundText', { name: `<b>${esc(route.full)}</b>` }), `<a class="btn" href="#/trending">${t('empty.viewTrending')}</a>`);
    throw e;
  }
  const full = det.full_name;
  const series = buildSeries(det);
  const total = det.stargazers ?? (series ? series[series.length - 1].c : null);
  // 合計裡含回補或推算（快照缺日補齊）的日子時，數字後面註明
  // 近 N 日以最新資料日期為準；序列較早結束的 repo（例如 gone）沒有這段期間的資料
  const end = META?.data_date ? toMs(META.data_date) : (series ? series[series.length - 1].t : 0);
  const tileNote = n => ['b', 'i'].filter(k => lastDays(series, n, end).some(p => p.n !== null && p.s === k)).map(k => t(k === 'b' ? 'tile.backfill' : 'tile.estimated')).join(LOCALE === 'en' ? ', ' : '、');
  const none = `<dd class="none">${t('tile.none')}</dd>`;
  const statTile = (label, n) => {
    const v = series ? sumLast(series, n, end) : null;
    return `<div class="kpi"><dt>${t(label)}</dt>${v === null ? none : `<dd class="acc">+${fmt(v)}${tileNote(n) ? `<small>${tileNote(n)}</small>` : ''}</dd>`}</div>`;
  };
  const days = new Set((det.trending || []).map(r => r[0])).size;
  const ghUrl = 'https://github.com/' + full.split('/').map(encodeURIComponent).join('/');
  const text = descText(det);
  const translated = isTranslated(det);
  const range = entry().range || 'all';

  const note = det.note ? `<section class="note-box" aria-labelledby="noteT"><h3 id="noteT">${ICON.note}${t('note.title')}</h3><p>${esc(det.note.text)}</p>
      <div class="note-foot">${det.note.updated ? `<span>${esc(t('note.updated', { date: det.note.updated }))}</span>` : ''}${det.note.article_url ? `<a href="${esc(det.note.article_url)}" target="_blank" rel="noopener noreferrer">${t('note.read')} ↗<span class="sr">${t('newTab')}</span></a>` : ''}</div></section>` : '';

  const tiles = `<dl class="tiles" aria-label="${t('tiles.label')}">
    <div class="kpi"><dt>${t('tile.total')}</dt>${total !== null ? `<dd>${fmt(total)}</dd>` : none}</div>
    ${statTile('tile.d7', 7)}${statTile('tile.d30', 30)}
    <div class="kpi"><dt>${t('tile.days')}</dt><dd>${fmt(days)}<small>${t(days === 1 ? 'tile.dayUnitOne' : 'tile.dayUnit')}</small></dd></div>
  </dl>`;

  const hasI = !!series?.some(p => p.s === 'i'); // 有快照缺日的補齊點時，圖例才列出「推算」
  const rangeSeg = Object.keys(RANGES).map(k => `<button type="button" data-range="${k}" aria-pressed="${range === k}">${t('chart.r' + k)}</button>`).join('');
  const charts = series && series.length > 1 ? `
    <div class="range-row"><h3 class="sr">${t('chart.history')}</h3>
      <div class="seg" role="group" aria-label="${t('chart.range')}">${rangeSeg}</div></div>
    <section class="panel" aria-labelledby="c1t">
      <div class="panel-head"><h3 id="c1t">${t('chart.cum')}</h3><span class="sub" id="c1sub"></span>
        <div class="legend"><span><i class="lg-line"></i>${t('chart.backfill')}</span><span><i class="lg-line s"></i>${t('chart.measured')}</span>${hasI ? `<span><i class="lg-line i"></i>${t('chart.estimated')}</span>` : ''}</div></div>
      <div class="chart" id="chartCum" tabindex="0" role="group" aria-roledescription="${t('chart.cumType')}" aria-label="${t('chart.cumAria')}"></div>
    </section>
    <section class="panel" aria-labelledby="c2t">
      <div class="panel-head"><h3 id="c2t">${t('chart.daily')}</h3><span class="sub" id="c2sub"></span>
        <div class="legend"><span><i class="lg-bar"></i>${t('chart.backfill')}</span><span><i class="lg-bar s"></i>${t('chart.measured')}</span>${hasI ? `<span><i class="lg-bar i"></i>${t('chart.estimated')}</span>` : ''}</div></div>
      <div class="chart" id="chartDaily" tabindex="0" role="group" aria-roledescription="${t('chart.barType')}" aria-label="${t('chart.dailyAria')}"></div>
    </section>`
    : `<section class="panel"><div class="panel-head"><h3>${t('chart.history')}</h3></div>
        <div class="chart-empty"><div><b>${t('chart.collecting')}</b><br>${t('chart.collectingText')}</div></div></section>`;
  route.series = series && series.length > 1 ? series : null;

  const trRows = [...(det.trending || [])].sort((a, b) => b[0].localeCompare(a[0]) || a[3] - b[3]);
  const table = `<h3 class="section-title">${t('hist.title')}</h3>` + (trRows.length
    ? `<div class="table-wrap"><table class="data"><caption class="sr">${t('hist.title')}</caption>
      <thead><tr><th scope="col">${t('hist.date')}</th><th scope="col">${t('hist.period')}</th><th scope="col">${t('hist.lang')}</th><th scope="col" class="num">${t('hist.rank')}</th></tr></thead>
      <tbody>${trRows.map(([dt, p, l, rk]) => `<tr><td data-label="${t('hist.date')}" class="mono">${esc(dt)}</td><td data-label="${t('hist.period')}">${PERIOD_ORDER.includes(p) ? t('period.' + p) : esc(p)}</td><td data-label="${t('hist.lang')}">${l ? esc(langName(l)) : t('hist.allLangs')}</td><td data-label="${t('hist.rank')}" class="num"><b>#${rk}</b></td></tr>`).join('')}</tbody></table></div>`
    : `<div class="chart-empty" style="height:120px">${t('hist.none')}</div>`);

  return `${backHtml()}
    <div class="d-head">
      <h2 tabindex="-1">${nameHtml(full)}</h2>
      <div class="d-badges">
        <span class="state ${esc(det.state)}">${t('state.' + det.state) === 'state.' + det.state ? esc(det.state) : t('state.' + det.state)}</span>
        ${det.archived ? `<span class="state archived">${t('repo.archived')}</span>` : ''}
        <a class="btn" href="${ghUrl}" target="_blank" rel="noopener noreferrer">${ICON.github}${t('repo.openGitHub')}${ICON.external}<span class="sr">${t('newTab')}</span></a>
      </div>
      <div class="d-desc">
        ${text ? `<p><span${translated ? ` lang="${esc(trLang())}"` : ''}>${esc(text)}</span>${translated ? ` <span class="ai" style="cursor:default" title="${esc(t('card.aiTitle', { lang: trLangName() }))}">${t('card.ai')}</span>` : ''}</p>` : `<p style="color:var(--muted)">${t('card.noDesc')}</p>`}
        ${translated && det.description ? `<p class="orig"><b>${t('repo.original')}</b>${esc(det.description)}</p>` : ''}
      </div>
      ${note}
      <dl class="d-meta">
        <div><dt>${t('repo.lang')}</dt><dd>${langHtml(det.language)}</dd></div>
        <div><dt>${t('repo.created')}</dt><dd class="mono">${esc(det.created_at || '—')}</dd></div>
        <div><dt>${t('repo.firstSeen')}</dt><dd class="mono">${esc(det.first_seen || '—')}</dd></div>
      </dl>
      ${det.topics?.length ? `<ul class="topics" aria-label="${t('repo.topics')}">${det.topics.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
    </div>
    ${tiles}${charts}${table}`;
}

/* =====================================================================
 * 圖表（原生 SVG）
 * ===================================================================== */
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
// 刻度縮寫：繁中用「萬」，英文用 k / M；間距不夠大時寫完整數字，避免相鄰刻度看起來一樣
function shortNum(v, step) {
  if (v === 0) return '0';
  if (LOCALE === 'en') {
    if (step >= 1e6) return (v / 1e6).toFixed(v % 1e6 ? 1 : 0) + 'M';
    if (step >= 1000) return (v / 1000).toFixed(v % 1000 ? 1 : 0) + 'k';
    return fmt(v);
  }
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
  const days = RANGES[rangeKey];
  const data = days === Infinity ? full : full.slice(-days);
  const W = box.clientWidth, H = box.clientHeight;
  if (!W || !H) return;
  const m = { l: 64, r: 12, t: 12, b: 28 };
  const pw = W - m.l - m.r, ph = H - m.t - m.b;
  box.innerHTML = '';
  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, 'aria-hidden': 'true' }, box);

  let items, binDays = 1;
  if (kind === 'bar') {
    // 點數太多時分組（7 日／30 日合計），從最新一天往回切。
    // 只在回補（b）與快照時期（s、i）的分界斷開：s 與 i 合在同一組，否則休眠 repo 每週會被切成
    // 「6 天推算」與「1 天實測」交錯，合計高度忽高忽低。組內有任何一天是 i 就整組標為推算。
    if (data.length > pw / 3) binDays = data.length / 7 <= pw / 3 ? 7 : 30;
    const era = p => (p.s === 'b' ? 'b' : 'snap');
    items = [];
    for (let end = data.length; end > 0;) {
      let start = Math.max(0, end - binDays);
      for (let k = end - 2; k >= start; k--) if (era(data[k]) !== era(data[end - 1])) { start = k + 1; break; }
      const chunk = data.slice(start, end);
      end = start;
      const known = chunk.filter(p => p.n !== null);
      const iDays = chunk.filter(p => p.s === 'i').length;
      items.unshift({ from: chunk[0].date, to: chunk[chunk.length - 1].date, days: chunk.length, iDays,
        v: known.length ? known.reduce((s, p) => s + p.n, 0) : null, s: iDays ? 'i' : chunk[0].s });
    }
    if (subEl) subEl.textContent = binDays === 1 ? t('chart.perDay') : t('chart.perN', { n: binDays });
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
      // 第一筆快照之後：相鄰兩點都是實測畫實線，其中有推算點（快照缺日的補齊）畫虛線
      const seg = i => (items[i].s === 'i' || items[i + 1].s === 'i' ? 'i' : 's');
      for (let a = Math.max(0, firstS - 1); a < n - 1;) {
        let b = a + 1;
        while (b < n - 1 && seg(b) === seg(a)) b++;
        el('path', { d: path(pick(a, b)), class: 'ln-' + seg(a) }, svg);
        a = b;
      }
      for (let i = firstS; i < n; i += stride) if (items[i].s === 's') el('circle', { cx: sx(i), cy: sy(items[i].c), r: 4, class: 'pt-s' }, svg);
    }
  } else {
    const bw = Math.max(1, pw / n - (pw / n > 4 ? 2 : 0));
    items.forEach((d, i) => {
      if (d.v === null) return;
      const h = Math.max(d.v > 0 ? 1 : 0, m.t + ph - sy(d.v));
      el('rect', { x: sx(i) - bw / 2, y: m.t + ph - h, width: bw, height: h, rx: bw > 6 ? 2 : 0, class: 'bar-' + d.s }, svg);
    });
  }

  // 游標互動
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
    // 多日合計的推算組，註明其中幾天是推算
    const src = t(SRC_LABEL[d.s] || 'chart.backfill') + (d.days > 1 && d.iDays ? t('chart.estDays', { n: d.iDays, total: d.days }) : '');
    const dateLabel = kind === 'bar' && d.from !== d.to ? `${d.from} – ${d.to}` : (d.date || d.from);
    const valLabel = kind === 'cum' ? `${fmt(d.c)} ★` : (d.v === null ? t('chart.noData') : `+${fmt(d.v)}`);
    tip.innerHTML = `<div class="t-date">${dateLabel}</div><div class="t-val">${valLabel}</div><div class="t-src ${d.s === 'b' ? '' : esc(d.s)}"><i></i>${src}</div>`;
    tip.hidden = false;
    const tw = tip.offsetWidth;
    let left = x + 12;
    if (left + tw > W) left = x - tw - 12;
    tip.style.left = Math.max(0, left) + 'px';
    $('#live').textContent = t(kind === 'cum' ? 'chart.liveCum' : 'chart.liveBar', { date: dateLabel, v: valLabel, src });
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

/* =====================================================================
 * 繪製流程
 * mode：'new' 新頁面（捲到頂、焦點移到標題）｜'restore' 返回（還原捲動與焦點）｜
 *       'update' 同一頁更新（保留捲動）｜'init' 第一次載入
 * ===================================================================== */
let renderToken = 0;
// 頁面函式把要記錄的 repo 與圖表序列放在這次 render 的 route 上，
// render 確認自己仍是最新的一次後才使用，被取代的 render 不會影響目前的頁面。
function recordSeenLater(route, items, meta) { route.seen = { names: items.map(i => i.full_name), date: meta.data_date || '' }; }

async function render(mode = 'update') {
  const token = ++renderToken;
  const route = parseRoute();
  currentRoute = route;
  detailSeries = null;
  chartRO.disconnect();
  const view = $('#view');
  document.title = (route.name === 'repo' ? route.full : route.name === 'report' ? routeLabel(route) : t('route.' + route.name)) + t('title.suffix');
  renderTabs(route, META);

  let skeletonShown = false;
  const skTimer = setTimeout(() => {
    if (token !== renderToken) return;
    skeletonShown = true;
    if (!META) renderKpis(undefined);
    view.innerHTML = skeletonHtml(route);
    view.setAttribute('aria-busy', 'true');
    $('#live').textContent = t('live.loading');
    afterPaint(mode, route, true);
  }, SKELETON_DELAY);

  try {
    const meta = await getData('meta');
    if (token !== renderToken) return;
    META = meta; // 卡片的名次變化等需要 meta，先存起來再組頁面
    const viewFn = { trending: viewTrending, new: viewNew, daily: viewDaily, alerts: viewAlerts, reports: viewReports, report: viewReport, repo: viewRepo }[route.name];
    const html = await viewFn(route, meta);
    if (token !== renderToken) return;
    clearTimeout(skTimer);
    renderHeaderAndNotice(meta);
    renderKpis(meta);
    renderTabs(route, meta);
    // 手機上分頁列可橫向捲動：只調整分頁列本身的水平位置，確保目前分頁看得到（不捲動整頁）
    const cur = $('#tabs [aria-current="page"]'), tabs = $('#tabs');
    if (cur && (cur.offsetLeft + cur.offsetWidth > tabs.scrollLeft + tabs.clientWidth || cur.offsetLeft < tabs.scrollLeft)) tabs.scrollLeft = cur.offsetLeft - 16;
    view.innerHTML = html;
    view.removeAttribute('aria-busy');
    if (skeletonShown) $('#live').textContent = t('live.loaded');
    if (route.seen) recordSeen(route.seen.names, route.seen.date);
    detailSeries = route.series || null;
  } catch (e) {
    if (token !== renderToken) return;
    clearTimeout(skTimer);
    console.error(e);
    if (!META) renderKpis(null);
    view.innerHTML = errorHtml();
    view.removeAttribute('aria-busy');
    $('#live').textContent = t('live.failed');
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
    requestAnimationFrame(() => window.scrollTo(0, st.scrollY || 0));
  } else if (mode === 'new') {
    window.scrollTo(0, 0);
    if (!isSkeleton) view.querySelector('h2')?.focus({ preventScroll: true });
  } else if (mode === 'init' && !isSkeleton && entry().scrollY) {
    window.scrollTo(0, entry().scrollY); // 重新整理：還原捲動
  }
}

/* ===== 吸頂高度 ===== */
function measureSticky() {
  const subH = $('#subnav').offsetHeight;
  const filters = $('#filters');
  const desktop = matchMedia('(min-width: 721px)').matches;
  document.documentElement.style.setProperty('--tabs-h', subH + 'px');
  document.documentElement.style.setProperty('--sticky-h', (subH + (filters && desktop ? filters.offsetHeight : 0) + 8) + 'px');
  updateStuck();
}
function updateStuck() {
  const f = $('#filters');
  if (!f) return;
  const top = parseFloat(getComputedStyle(f).top) || 0;
  f.classList.toggle('is-stuck', getComputedStyle(f).position === 'sticky' && f.getBoundingClientRect().top <= top + 0.5 && window.scrollY > 0);
}
window.addEventListener('resize', measureSticky);

/* =====================================================================
 * 導覽事件
 * ===================================================================== */
// 點連結前記下這一頁點的是哪個連結，返回時把焦點還給它
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
  if (prevEntryId) (entryStore[prevEntryId] ||= {}).scrollY = lastScrollY; // 離開的那一頁：保存捲動位置
  const prevRoute = parseRoute(new URL(e.oldURL).hash);
  if (!history.state?.id) {
    // 新的瀏覽：記錄來源頁，供「返回」使用
    history.replaceState({ id: newId(), from: { hash: prevRoute.hash, label: routeLabel(prevRoute) } }, '');
    prevEntryId = history.state.id;
    saveStore();
    render('new');
  } else {
    prevEntryId = history.state.id; // 上一頁／下一頁
    render('restore');
  }
});

window.addEventListener('pagehide', () => { entry().scrollY = window.scrollY; saveStore(); });
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') { entry().scrollY = window.scrollY; saveStore(); } });

document.addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b) return;
  if (b.dataset.lang || b.dataset.period || b.dataset.sort) {
    // 篩選寫進網址但不新增歷史紀錄，返回時直接回到上一個頁面
    const r = currentRoute.name === 'trending' ? { ...currentRoute } : parseRoute('#/trending');
    if (b.dataset.lang) r.lang = b.dataset.lang;
    if (b.dataset.period) r.period = b.dataset.period;
    if (b.dataset.sort) r.sort = b.dataset.sort;
    history.replaceState(history.state, '', trendingHash(r));
    const sel = b.dataset.lang ? `[data-lang="${CSS.escape(r.lang)}"]` : b.dataset.period ? `[data-period="${r.period}"]` : `[data-sort="${r.sort}"]`;
    render('update').then(() => $('#filters ' + sel)?.focus({ preventScroll: true }));
  } else if (b.dataset.rkind) {
    const kind = b.dataset.rkind;
    history.replaceState(history.state, '', '#/reports' + (kind === 'all' ? '' : '?kind=' + kind));
    render('update').then(() => $(`[data-rkind="${kind}"]`)?.focus({ preventScroll: true }));
  } else if (b.dataset.range) {
    entry().range = b.dataset.range;
    document.querySelectorAll('[data-range]').forEach(x => x.setAttribute('aria-pressed', x.dataset.range === b.dataset.range));
    drawCharts();
  } else if (b.id === 'moreNew') {
    const before = document.querySelectorAll('#view .card').length;
    entry().newShown = (entry().newShown || PAGE_SIZE) + PAGE_SIZE;
    render('update').then(() => document.querySelectorAll('#view .card .name a')[before]?.focus());
  } else if (b.hasAttribute('data-back')) {
    history.back();
  } else if (b.hasAttribute('data-retry')) {
    render('update');
  } else if (b.classList.contains('ai')) {
    const open = b.getAttribute('aria-expanded') !== 'true';
    document.querySelectorAll('.ai[aria-expanded="true"]').forEach(x => x.setAttribute('aria-expanded', 'false'));
    b.setAttribute('aria-expanded', String(open));
  }
});
document.addEventListener('click', e => {
  if (!e.target.closest('.ai')) document.querySelectorAll('.ai[aria-expanded="true"]').forEach(x => x.setAttribute('aria-expanded', 'false'));
}, true);
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') document.querySelectorAll('.ai[aria-expanded="true"]').forEach(x => x.setAttribute('aria-expanded', 'false'));
});

/* =====================================================================
 * 主題：跟隨系統 → 淺色 → 深色
 * ===================================================================== */
const THEMES = ['auto', 'light', 'dark'];
let theme = 'auto';
try { const v = localStorage.getItem('starstrata-theme'); if (THEMES.includes(v)) theme = v; } catch (e) {}
function applyTheme() {
  if (theme === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', theme);
  const name = t('theme.' + theme);
  const btn = $('#themeBtn');
  btn.innerHTML = ICON[theme] + `<span class="sr">${esc(t('theme.label', { name }))}</span>`;
  btn.title = t('theme.title', { name });
}
$('#themeBtn').addEventListener('click', () => {
  document.documentElement.classList.add('no-transition');
  requestAnimationFrame(() => requestAnimationFrame(() => document.documentElement.classList.remove('no-transition')));
  theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
  try { theme === 'auto' ? localStorage.removeItem('starstrata-theme') : localStorage.setItem('starstrata-theme', theme); } catch (e) {}
  applyTheme();
  $('#live').textContent = t('theme.label', { name: t('theme.' + theme) });
});

/* =====================================================================
 * 搜尋：先用 search-index（名稱），輸入 2 個字以上再載入 search-full（說明與 topics）
 * ===================================================================== */
const MAX_RESULTS = 8;
const q = $('#q'), panel = $('#qPanel'), listEl = $('#qList'), foot = $('#qFoot');
const idx = { basic: { state: 'idle', rows: null }, full: { state: 'idle', map: null } };
let results = [], active = -1;

function loadBasic() {
  if (idx.basic.state === 'loading' || idx.basic.state === 'ready') return;
  idx.basic.state = 'loading';
  getData('search-index').then(rows => {
    idx.basic.rows = rows.map(([full, lang, stars]) => ({ full, lang, stars, key: full.toLowerCase(), nameKey: (full.split('/')[1] || '').toLowerCase() }));
    idx.basic.state = 'ready';
    if (q.value.trim()) updateSearch();
  }, () => { idx.basic.state = 'error'; if (q.value.trim()) updateSearch(); });
}
function loadFull() {
  if (idx.full.state !== 'idle') return;
  idx.full.state = 'loading';
  getData('search-full').then(rows => {
    // 每筆 [full_name, description, description_tr, topics]；原文與譯文都可搜尋
    const texts = (...xs) => xs.filter(Boolean).map(x => ({ text: x, key: x.toLowerCase() }));
    idx.full.map = new Map(rows.map(([full, desc, tr, topics]) => {
      const all = texts(tr, desc);
      return [full, { tr: tr ? all[0] : null, texts: all, topics: (topics || []).map(x => x.toLowerCase()) }];
    }));
    idx.full.state = 'ready';
    if (q.value.trim()) updateSearch();
  }, () => { idx.full.state = 'idle'; }); // 失敗時只用名稱搜尋，下次輸入再試
}
function markText(text, term) {
  const i = text.toLowerCase().indexOf(term);
  if (i < 0) return esc(text);
  return esc(text.slice(0, i)) + '<mark>' + esc(text.slice(i, i + term.length)) + '</mark>' + esc(text.slice(i + term.length));
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
function snippet(desc, term) {
  const i = desc.toLowerCase().indexOf(term);
  const start = Math.max(0, i - 24);
  return (start > 0 ? '…' : '') + markText(desc.slice(start, start + 90), term);
}
function openPanel() { panel.hidden = false; q.setAttribute('aria-expanded', 'true'); }
function updateSearch() {
  const raw = q.value.trim(), term = raw.toLowerCase();
  if (!term) { closeSearch(); return; }
  results = []; active = -1;
  if (term.length >= 2) loadFull();
  if (idx.basic.state !== 'ready') {
    listEl.innerHTML = '';
    foot.innerHTML = idx.basic.state === 'error'
      ? `<div class="dd-empty"><span>${t('search.failed')}</span><button type="button" class="btn" id="qRetry">${t('error.retry')}</button></div>`
      : `<div class="dd-empty">${t('search.loading')}</div>`;
    if (idx.basic.state === 'idle') loadBasic();
    openPanel(); syncActive();
    return;
  }
  // 排序：名稱開頭 > 名稱包含 > topic 完全相同 > 說明包含；同級依總 star
  const full = idx.full.map;
  const scored = [];
  for (const r of idx.basic.rows) {
    let score = -1, snip = '';
    if (r.nameKey.startsWith(term)) score = 0;
    else if (r.key.includes(term)) score = 1;
    else if (full && term.length >= 2) {
      const f = full.get(r.full);
      if (f && f.topics.includes(term)) { score = 2; snip = '#' + esc(term); }
      else if (f) {
        // 先比對譯文再比對原文，片段顯示命中的那一段；譯文片段標上翻譯語言
        const hit = f.texts.find(x => x.key.includes(term));
        if (hit) { score = 3; snip = hit === f.tr && META?.translation_lang ? `<span lang="${esc(META.translation_lang)}">${snippet(hit.text, term)}</span>` : snippet(hit.text, term); }
      }
    }
    if (score >= 0) scored.push({ r, score, snip });
  }
  scored.sort((a, b) => a.score - b.score || b.r.stars - a.r.stars);
  results = scored.slice(0, MAX_RESULTS);
  active = results.length ? 0 : -1;
  listEl.innerHTML = results.map(({ r, snip }, i) => `<li class="dd-item" role="option" id="opt${i}" aria-selected="${i === active}" data-full="${esc(r.full)}">
      <span class="dd-name">${highlight(r.full, term)}</span><span class="dd-star">★ ${fmt(r.stars)}</span>
      <span class="dd-meta">${langHtml(r.lang)}</span>${snip ? `<span class="dd-snip">${snip}</span>` : ''}</li>`).join('');
  foot.innerHTML = !results.length
    ? `<div class="dd-empty">${esc(t('search.none', { q: raw }))}</div>`
    : `<div class="dd-foot">${t('search.count', { n: fmt(scored.length) })}${scored.length > MAX_RESULTS ? t('search.first', { m: MAX_RESULTS }) : ''}${t('search.hint')}</div>`;
  openPanel(); syncActive();
  $('#live').textContent = results.length ? t('search.live', { n: scored.length }) : t('search.liveNone');
}
function syncActive() {
  listEl.querySelectorAll('.dd-item').forEach((li, i) => li.setAttribute('aria-selected', i === active));
  if (active >= 0) { q.setAttribute('aria-activedescendant', 'opt' + active); $('#opt' + active).scrollIntoView({ block: 'nearest' }); }
  else q.removeAttribute('aria-activedescendant');
}
function closeSearch() { panel.hidden = true; q.setAttribute('aria-expanded', 'false'); q.removeAttribute('aria-activedescendant'); active = -1; }
function openRepo(full) { closeSearch(); q.value = ''; q.blur(); location.hash = repoHref(full); }

q.addEventListener('focus', () => { loadBasic(); if (q.value.trim()) updateSearch(); });
q.addEventListener('input', updateSearch);
q.addEventListener('keydown', e => {
  if (e.key === 'ArrowDown' && results.length) { active = (active + 1) % results.length; syncActive(); e.preventDefault(); }
  else if (e.key === 'ArrowUp' && results.length) { active = (active - 1 + results.length) % results.length; syncActive(); e.preventDefault(); }
  else if (e.key === 'Enter' && active >= 0) { openRepo(results[active].r.full); e.preventDefault(); }
  else if (e.key === 'Escape') { if (!panel.hidden) closeSearch(); else { q.value = ''; q.blur(); } }
});
panel.addEventListener('mousedown', e => {
  const li = e.target.closest('.dd-item');
  if (li) { e.preventDefault(); openRepo(li.dataset.full); return; }
  if (e.target.closest('#qRetry')) { e.preventDefault(); idx.basic.state = 'idle'; loadBasic(); updateSearch(); }
});
listEl.addEventListener('mousemove', e => { const li = e.target.closest('.dd-item'); if (li) { active = [...listEl.children].indexOf(li); syncActive(); } });
document.addEventListener('mousedown', e => { if (!e.target.closest('.search')) closeSearch(); });
document.addEventListener('keydown', e => {
  if (e.key === '/' && !e.ctrlKey && !e.metaKey && !e.altKey && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) && !document.activeElement.isContentEditable) { e.preventDefault(); q.focus(); }
});

/* =====================================================================
 * 啟動
 * ===================================================================== */
applyStaticText();
applyTheme();
if (!history.state?.id) history.replaceState({ id: newId(), from: null }, '');
prevEntryId = history.state.id;
render('init');
