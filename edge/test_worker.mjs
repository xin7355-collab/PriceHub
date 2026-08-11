/**
 * Worker 的離線測試 —— 不需網路，也不需要 wrangler。
 *
 *   node edge/test_worker.mjs
 *
 * 解析層壞掉時通常不會拋錯，只是回 0 筆，沒有測試就只能等使用者回報。
 * fetch 用假的，回傳固定的 PChome 形狀，因此連 worker 的匯總邏輯
 * （多平台併發、單一平台失敗要隔離、排序、快取鍵）都測得到。
 */
import assert from 'node:assert';
import worker from './worker.js';
import { SOURCES, parseMomo, parsePchome, parsePchomeItem, toIntPrice, isExcluded } from './sources.js';

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

/* ------------------------------------------------------------ 價格解析 */
test('價格：各種寫法', () => {
  assert.equal(toIntPrice(3290), 3290);
  assert.equal(toIntPrice('3,290'), 3290);
  assert.equal(toIntPrice('$12,900'), 12900);
  assert.equal(toIntPrice('特價$3,190起'), 3190);
  assert.equal(toIntPrice(''), null);
  assert.equal(toIntPrice('電洽'), null);
  assert.equal(toIntPrice(0), null);
  assert.equal(toIntPrice(-5), null);
  assert.equal(toIntPrice(true), null, 'true 不是價格');
});

/* ------------------------------------------------------------ PChome 解析 */
test('PChome：缺欄位就回 null，不要猜', () => {
  assert.equal(parsePchomeItem({}), null);
  assert.equal(parsePchomeItem({ Id: 'X', name: '測試' }), null);   // 缺價格
  assert.equal(parsePchomeItem({ Id: 'X', price: 100 }), null);      // 缺名稱
  assert.equal(parsePchomeItem(null), null);
});

test('PChome：正常一筆', () => {
  const o = parsePchomeItem({
    Id: 'DYAJ2S-A900', name: '測試商品', price: 3290, picB: '/items/x/000001.jpg',
  });
  assert.equal(o.platform, 'pchome');
  assert.equal(o.price, 3290);
  assert.ok(o.url.endsWith('DYAJ2S-A900'));
  assert.ok(o.image.startsWith('https://'), '相對圖片路徑要補成絕對網址');
});

test('PChome：標題的 <b> 標記要清掉', () => {
  const o = parsePchomeItem({ Id: 'A', name: '<b>Sony</b> WH-1000XM5  耳機', price: 8900 });
  assert.equal(o.title, 'Sony WH-1000XM5 耳機');
});

test('PChome：字串價格', () => {
  assert.equal(parsePchomeItem({ Id: 'A', name: 'N', price: '1,299' }).price, 1299);
});

test('PChome：limit 有效，且解不出來的整筆丟掉', () => {
  const data = { prods: [
    { Id: '1', name: 'A', price: 100 },
    { Id: '2', name: 'B' },                 // 缺價格 → 丟掉
    { Id: '3', name: 'C', price: 300 },
  ] };
  assert.equal(parsePchome(data, 20).length, 2);
  assert.equal(parsePchome(data, 1).length, 1);
  assert.equal(parsePchome({}, 20).length, 0);
  assert.equal(parsePchome(null, 20).length, 0);
});

/* ------------------------------------------------------------ momo 解析 */
// 結構照 tools/probe_platforms.py --deep momo 實測到的形狀，不是猜的
const MOMO_HTML = `<html><script type="application/ld+json">
{"@context":"https://schema.org","@type":"WebPage","@graph":[
 {"@type":"BreadcrumbList","itemListElement":[{"@type":"ListItem","name":"首頁"}]},
 {"@type":"ItemList","itemListElement":[
  {"@type":"Product","name":"【SONY 索尼】WH-1000XM5 主動式降噪旗艦藍芽耳機","position":1,
   "image":"https://img3.momoshop.com.tw/goodsimg/0010/201/974/10201974_OL.jpg",
   "url":"https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code=10201974&Area=search&kw=x",
   "offers":{"@type":"Offer","price":9900,"priceCurrency":"TWD"}},
  {"@type":"Product","name":"多賣家商品","position":2,
   "url":"https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code=222",
   "offers":[{"price":"3,290"},{"price":3190}]},
  {"@type":"Product","name":"缺價格","position":3,
   "url":"https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code=333","offers":{}},
  {"@type":"Product","name":"重複的同一件","position":4,
   "url":"https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code=10201974",
   "offers":{"price":9900}}
 ]}]}
</script></html>`;

test('momo：從巢狀 @graph 抽出商品', () => {
  const offers = parseMomo(MOMO_HTML, 20);
  assert.equal(offers.length, 2, JSON.stringify(offers.map((o) => o.title)));
  assert.equal(offers[0].platform, 'momo');
  assert.equal(offers[0].price, 9900);
});

test('momo：offers 是陣列時取最低價', () => {
  assert.equal(parseMomo(MOMO_HTML, 20)[1].price, 3190);
});

test('momo：追蹤參數要剝掉，否則同商品每次網址都不同', () => {
  const o = parseMomo(MOMO_HTML, 20)[0];
  assert.equal(o.url,
    'https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code=10201974');
  assert.ok(!o.url.includes('Area='));
});

test('momo：limit 有效', () => {
  assert.equal(parseMomo(MOMO_HTML, 1).length, 1);
});

test('momo：壞輸入只能回空陣列，不能爆', () => {
  for (const bad of ['', '<html></html>', null, undefined,
                     '<script type="application/ld+json">{壞</script>',
                     '<script type="application/ld+json">null</script>']) {
    assert.deepEqual(parseMomo(bad, 20), []);
  }
});

/* ------------------------------------------------------------ 排除詞 */
test('排除詞：與 Python 端同一套語意', () => {
  assert.equal(isExcluded('AirPods Pro 2 保護殼套', ['保護殼']), true);
  assert.equal(isExcluded('Apple AirPods Pro 2 耳機', ['保護殼']), false);
  assert.equal(isExcluded('Silicone CASE', ['case']), true, '應不分大小寫');
  assert.equal(isExcluded('任何標題', []), false);
});

/* ------------------------------------------------------------ Worker 匯總 */
const ctx = { waitUntil() {} };

// Workers 執行環境才有 caches.default，node 沒有 —— 補一個不快取的替身，
// 這樣測到的就是「每次都真的算一遍」的路徑。
globalThis.caches = { default: { match: async () => undefined, put: async () => {} } };

function fakeFetch(body, { ok = true, status = 200 } = {}) {
  return async () => ({ ok, status, text: async () => JSON.stringify(body) });
}
// 必須 await 完才還原 globalThis.fetch。worker.fetch 是 async，
// 在 finally 直接還原會在它真正打 fetch 之前就換掉，測到的是上一個假 fetch。
const call = async (url, fetchImpl) => {
  const saved = globalThis.fetch;
  globalThis.fetch = fetchImpl;
  try { return await worker.fetch(new Request(url), {}, ctx); }
  finally { globalThis.fetch = saved; }
};

const SAMPLE = { prods: [
  { Id: 'a', name: '貴的商品', price: 9000, picB: '/a.jpg' },
  { Id: 'b', name: '便宜的商品', price: 1000, picB: '/b.jpg' },
  { Id: 'c', name: 'AirPods 保護殼套', price: 199 },
] };

test('Worker：/search 回 CORS 標頭並依價格排序', async () => {
  const res = await call('https://w.dev/search?q=測試', fakeFetch(SAMPLE));
  assert.equal(res.status, 200);
  assert.equal(res.headers.get('Access-Control-Allow-Origin'), '*',
    '沒有這個標頭，瀏覽器根本讀不到回應');
  const d = await res.json();
  assert.equal(d.count, 3);
  assert.deepEqual(d.offers.map((o) => o.price), [199, 1000, 9000], '要由低到高');
});

test('Worker：exclude 參數會濾掉配件', async () => {
  const res = await call('https://w.dev/search?q=測試&exclude=保護殼', fakeFetch(SAMPLE));
  const d = await res.json();
  assert.equal(d.count, 2);
  assert.ok(!d.offers.some((o) => o.title.includes('保護殼')));
});

test('Worker：limit 有效且有上限', async () => {
  const d = await (await call('https://w.dev/search?q=測試&limit=1', fakeFetch(SAMPLE))).json();
  assert.equal(d.count, 1);
});

test('Worker：關鍵字太短回 400', async () => {
  const res = await call('https://w.dev/search?q=a', fakeFetch(SAMPLE));
  assert.equal(res.status, 400);
  assert.equal(res.headers.get('Access-Control-Allow-Origin'), '*',
    '錯誤回應同樣要有 CORS，否則前端連錯誤訊息都讀不到');
});

// 平台數會隨著接通新通路而變，斷言不要寫死數字，否則每加一個通路
// 就得回來改測試 —— 那種測試很快就會被改成「怎樣都會過」。
const N_PLATFORMS = Object.keys(SOURCES).length;

test('Worker：平台掛掉要隔離，不能整個查詢失敗', async () => {
  const boom = async () => { throw new Error('平台爆炸'); };
  const res = await call('https://w.dev/search?q=測試', boom);
  assert.equal(res.status, 200, '單一平台失敗不該回 5xx');
  const d = await res.json();
  assert.equal(d.count, 0);
  assert.equal(d.errors.length, N_PLATFORMS, '每個平台各自回報一次失敗');
  assert.ok(d.errors.every((e) => /平台爆炸/.test(e.error)));
});

test('Worker：一個平台掛掉，另一個仍要照常回', async () => {
  if (N_PLATFORMS < 2) return;                 // 只有一個平台時這題不成立
  // 假 fetch 回的是 PChome 形狀的 JSON，所以要留著 pchome 當倖存者 ——
  // 弄壞 pchome 而留 momo，momo 拿到 JSON 也解不出東西，就分不清
  // 「被隔離成功」和「根本沒解出來」。
  const victim = Object.keys(SOURCES).find((n) => n !== 'pchome');
  const saved = SOURCES[victim].search;
  SOURCES[victim].search = async () => { throw new Error('只有我掛'); };
  try {
    const d = await (await call('https://w.dev/search?q=測試', fakeFetch(SAMPLE))).json();
    assert.equal(d.errors.length, 1, '只有壞掉的那個進 errors');
    assert.equal(d.errors[0].platform, victim);
    assert.ok(d.count > 0, '倖存平台的結果必須照常回來');
  } finally {
    SOURCES[victim].search = saved;
  }
});

test('Worker：平台回非 200 也要被當成該平台的錯誤', async () => {
  const res = await call('https://w.dev/search?q=測試',
    fakeFetch({}, { ok: false, status: 403 }));
  const d = await res.json();
  assert.equal(d.errors.length, N_PLATFORMS);
  assert.ok(d.errors.every((e) => /403/.test(e.error)));
});

test('Worker：OPTIONS 預檢', async () => {
  const res = await worker.fetch(
    new Request('https://w.dev/search', { method: 'OPTIONS' }), {}, ctx);
  assert.equal(res.headers.get('Access-Control-Allow-Origin'), '*');
});

test('Worker：/health', async () => {
  const d = await (await call('https://w.dev/health', fakeFetch(SAMPLE))).json();
  assert.equal(d.ok, true);
  assert.ok(d.platforms.includes('pchome'));
});

test('Worker：未知路徑回 404', async () => {
  assert.equal((await call('https://w.dev/nope', fakeFetch(SAMPLE))).status, 404);
});

/* ------------------------------------------------------------------ 執行 */
let failed = 0;
for (const [name, fn] of tests) {
  try {
    await fn();
    console.log(`  PASS  ${name}`);
  } catch (e) {
    failed++;
    console.log(`  FAIL  ${name}`);
    console.log(`        ${e.message}`);
  }
}
console.log(`\n${tests.length - failed}/${tests.length} 通過`);
process.exit(failed ? 1 : 0);
