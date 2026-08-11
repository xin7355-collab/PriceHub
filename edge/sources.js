/**
 * 各平台的即時搜尋介接。
 *
 * 這裡刻意把「解析」與「取數」切開：parse* 是純函式，不碰網路，
 * 可以用固定的假回應在 node 裡直接測（edge/test_worker.mjs）。
 * 爬蟲類的程式碼最容易默默壞掉的就是解析層，而它壞掉時通常不會拋錯，
 * 只是回 0 筆 —— 沒有測試就只能等使用者回報。
 *
 * 欄位形狀刻意與 Python 端的 collector/sources/base.py::Offer 一致，
 * 兩邊要是長歪了，前端就得寫兩套顯示邏輯。
 */

/** 把各平台五花八門的價格寫法壓成整數；看不懂就回 null，不要猜。 */
export function toIntPrice(v) {
  if (typeof v === 'boolean') return null;
  if (typeof v === 'number') return v > 0 ? Math.trunc(v) : null;
  if (typeof v === 'string') {
    const m = v.replace(/,/g, '').match(/\d+/);
    if (!m) return null;
    const n = parseInt(m[0], 10);
    return Number.isFinite(n) && n > 0 ? n : null;
  }
  return null;
}

const pick = (o, ...keys) => {
  for (const k of keys) {
    if (o && o[k] !== undefined && o[k] !== null && o[k] !== '') return o[k];
  }
  return null;
};

/* ------------------------------------------------------------------ PChome */
export const PCHOME_SEARCH =
  'https://ecshweb.pchome.com.tw/search/v3.3/all/results';

/**
 * 與 collector/sources/pchome.py::parse_item 同一套規則。
 * 欄位改名時回 null 而不是整批崩潰。
 */
export function parsePchomeItem(item) {
  if (!item || typeof item !== 'object') return null;
  const id = pick(item, 'Id', 'id', 'prodId');
  const name = pick(item, 'name', 'Name', 'prodName');
  const price = toIntPrice(pick(item, 'price', 'Price', 'salePrice'));
  if (!id || !name || !price) return null;

  const pic = pick(item, 'picB', 'picS', 'picM', 'img');
  const image =
    typeof pic === 'string' && pic.startsWith('/') ? `https://cs-a.ecimg.tw${pic}` : pic;

  return {
    platform: 'pchome',
    title: String(name).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim(),
    price,
    url: `https://24h.pchome.com.tw/prod/${id}`,
    image: image || null,
  };
}

export function parsePchome(data, limit) {
  let items = [];
  if (Array.isArray(data)) items = data;
  else if (data && typeof data === 'object') items = data.prods || data.Prods || [];
  const out = [];
  for (const it of items.slice(0, limit)) {
    const o = parsePchomeItem(it);
    if (o) out.push(o);
  }
  return out;
}

async function searchPchome(keyword, limit, fetchImpl) {
  const url = `${PCHOME_SEARCH}?q=${encodeURIComponent(keyword)}&page=1&sort=sale/dc`;
  const r = await fetchImpl(url, {
    headers: {
      // 帶瀏覽器 UA，否則部分端點會直接回 403
      'User-Agent':
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
        '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
      Accept: 'application/json, text/plain, */*',
      'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
    },
  });
  if (!r.ok) throw new Error(`pchome HTTP ${r.status}`);
  // PChome 回的 Content-Type 是 text/html，不能用 r.json() 直接信它
  return parsePchome(JSON.parse(await r.text()), limit);
}

/* ---------------------------------------------------------------- registry */
/**
 * 加平台就在這裡多一筆。momo / 蝦皮的聯盟 API 核准後，
 * 照這個形狀補上 search() 即可，worker.js 完全不用改。
 */
export const SOURCES = {
  pchome: { label: 'PChome 24h', search: searchPchome },
};

/** 排除詞：與 watchlist.json 的 exclude_common 同一套用途。 */
export function isExcluded(title, exclude) {
  if (!exclude || !exclude.length) return false;
  const low = String(title).toLowerCase();
  return exclude.some((w) => low.includes(String(w).toLowerCase()));
}
