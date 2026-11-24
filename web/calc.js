'use strict';

// 純計算函式，由 app.js 與 tests/test_calc.py（node）共用，不可存取 DOM。

const DAY = 86400000;
const toMs = d => Date.UTC(+d.slice(0, 4), +d.slice(5, 7) - 1, +d.slice(8, 10));
const toDate = ms => new Date(ms).toISOString().slice(0, 10);

// 由 points 與 daily 組出每日序列，n 為 null 表示當天新增量未知。
// 回補只記有新增的日子，所以回補期間缺日的新增為 0；快照缺日時線性補齊累計，新增取累計差。
// 第一筆快照沒有前一天可比較，新增未知。
function buildSeries(d) {
  if (!d.points?.length) return null;
  const dailyMap = new Map((d.daily || []).map(([dt, n, s]) => [dt, { n, s }]));
  const points = d.points.map(([dt, c, s]) => ({ t: toMs(dt), c, s }));
  const firstSnap = points.find(p => p.s === 's')?.t ?? Infinity;
  const out = [];
  let pi = 0;
  for (let t = points[0].t; t <= points[points.length - 1].t; t += DAY) {
    while (points[pi + 1] && points[pi + 1].t <= t) pi++;
    const p = points[pi], date = toDate(t), prev = out[out.length - 1];
    let c, n, s;
    if (p.t === t) {
      const dm = dailyMap.get(date);
      c = p.c; s = dm ? dm.s : p.s;
      n = dm ? dm.n : (prev && t !== firstSnap ? Math.max(0, c - prev.c) : null);
    } else if (t < firstSnap) {
      c = p.c; s = 'b'; n = 0;
    } else {
      const q = points[pi + 1];
      c = Math.round(p.c + (q.c - p.c) * (t - p.t) / (q.t - p.t));
      s = 'b'; n = Math.max(0, c - prev.c);
    }
    out.push({ t, date, c, n, s });
  }
  return out;
}

// series 末 k 天已知新增量的合計；全部未知時為 null。
function sumLast(series, k) {
  const known = series.slice(-k).filter(p => p.n !== null);
  return known.length ? known.reduce((s, p) => s + p.n, 0) : null;
}

// 警示當日新增相對基準的倍數；基準 ≤ 0 或資料不足時為 null。
const alertRatio = a => (a.baseline > 0 ? a.added / a.baseline : null);

if (typeof module === 'object') module.exports = { DAY, toMs, toDate, buildSeries, sumLast, alertRatio };
