/**
 * 比價通 — 即時搜尋代理（Cloudflare Workers）
 *
 * 為什麼需要它：GitHub Pages 是純靜態的，而各平台的搜尋端點都沒有送
 * CORS 標頭，瀏覽器不能直接打。這支 Worker 站在中間，對外送 CORS，
 * 對內併發打各平台，回傳統一形狀的報價清單。
 *
 * 為什麼不把它塞進採集排程：排程負責的是「長期價格曲線」，一天四班、
 * 只跑清單上的商品；這支負責的是「使用者現在打了什麼就查什麼」。
 * 兩者的頻率與失敗代價完全不同，混在一起會互相拖累。
 *
 *   GET /search?q=關鍵字&limit=20
 *   → { query, offers: [{platform,title,price,url,image}], errors: [] }
 *
 * 部署見 edge/README.md。
 */
import { SOURCES, isExcluded } from './sources.js';

const MAX_LIMIT = 40;
const CACHE_SECONDS = 300; // 同一個關鍵字 5 分鐘內只真的打一次平台

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, OPTIONS',
  'Access-Control-Max-Age': '86400',
};

const json = (obj, status = 200, extra = {}) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': `public, max-age=${CACHE_SECONDS}`,
      ...CORS,
      ...extra,
    },
  });

/** 一個平台掛掉不該讓整個查詢失敗 —— 回報它，其他平台照常回。 */
async function searchAll(keyword, limit, exclude, fetchImpl) {
  const names = Object.keys(SOURCES);
  const settled = await Promise.allSettled(
    names.map((n) => SOURCES[n].search(keyword, limit, fetchImpl)),
  );

  const offers = [];
  const errors = [];
  settled.forEach((r, i) => {
    if (r.status === 'fulfilled') {
      for (const o of r.value) {
        if (!isExcluded(o.title, exclude)) offers.push(o);
      }
    } else {
      errors.push({ platform: names[i], error: String(r.reason && r.reason.message || r.reason) });
    }
  });

  offers.sort((a, b) => a.price - b.price); // 比價網站的預設就該是最便宜的在前
  return { offers, errors };
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === 'OPTIONS') return new Response(null, { headers: CORS });

    const url = new URL(request.url);
    if (url.pathname === '/health') {
      return json({ ok: true, platforms: Object.keys(SOURCES) });
    }
    if (url.pathname !== '/search') {
      return json({ error: 'not found', usage: '/search?q=關鍵字' }, 404);
    }

    const q = (url.searchParams.get('q') || '').trim();
    if (q.length < 2) {
      return json({ error: '關鍵字至少兩個字' }, 400);
    }

    const limit = Math.min(
      MAX_LIMIT,
      Math.max(1, parseInt(url.searchParams.get('limit') || '20', 10) || 20),
    );
    // 前端把 watchlist 的 exclude_common 帶過來，兩邊用同一套排除規則
    const exclude = (url.searchParams.get('exclude') || '')
      .split(',').map((s) => s.trim()).filter(Boolean);

    // 邊緣快取：熱門關鍵字不會每次都真的去打平台，
    // 既省 Worker 用量，也降低被對方限流的機率。
    const cache = caches.default;
    const cacheKey = new Request(
      `${url.origin}/search?q=${encodeURIComponent(q)}&limit=${limit}&exclude=${encodeURIComponent(exclude.join(','))}`,
      { method: 'GET' },
    );
    const hit = await cache.match(cacheKey);
    if (hit) return hit;

    let payload;
    try {
      const { offers, errors } = await searchAll(q, limit, exclude, fetch);
      payload = { query: q, count: offers.length, offers, errors };
    } catch (e) {
      return json({ error: String(e && e.message || e), query: q }, 502);
    }

    const res = json(payload);
    // 全部平台都掛了就不要把失敗結果快取住，否則使用者要等 5 分鐘才會恢復
    if (payload.count > 0) ctx.waitUntil(cache.put(cacheKey, res.clone()));
    return res;
  },
};
