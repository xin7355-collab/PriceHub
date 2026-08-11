# 即時搜尋代理

排程採集負責的是**長期價格曲線**：一天四班、只跑 `watchlist.json` 上的商品。
這支 Worker 負責的是**使用者現在打了什麼就查什麼**。兩者頻率與失敗代價完全不同，
所以分開。

## 為什麼非要有一層代理

GitHub Pages 是純靜態的，而各平台的搜尋端點都沒有送 CORS 標頭 ——
瀏覽器直接打會被擋在同源政策外，連錯誤訊息都讀不到。代理站在中間：
對外送 CORS，對內併發打各平台，回傳統一形狀的報價清單。

## 部署（約 3 分鐘）

```bash
npm install -g wrangler
wrangler login                 # 會開瀏覽器登入 Cloudflare
cd edge
wrangler deploy
```

部署完會印出網址，像 `https://priceport-search.<你的帳號>.workers.dev`。

先確認它活著：

```bash
curl "https://priceport-search.<你的帳號>.workers.dev/health"
curl "https://priceport-search.<你的帳號>.workers.dev/search?q=WH-1000XM5" | head -c 400
```

## 讓前端用它

把網址填進 `web/config.json` 的 `searchApi`，push 到 `main` 就會自動部署：

```json
{ "searchApi": "https://priceport-search.你的帳號.workers.dev" }
```

沒填（`null`）時前端完全不會去打它，只搜尋已採集的商品 —— 所以這個檔
留白也不會壞掉。

想先試而不動 repo，可以用網址參數覆寫：

```
https://你的站台/?api=https://priceport-search.你的帳號.workers.dev
```

## API

```
GET /search?q=關鍵字&limit=20&exclude=保護殼,保護套
→ { query, count, offers: [{platform,title,price,url,image}], errors: [] }

GET /health
→ { ok: true, platforms: ["pchome"] }
```

`offers` 已依價格由低到高排好。單一平台掛掉不會讓整個查詢失敗 ——
它會出現在 `errors`，其他平台照常回。

## 加平台

`sources.js` 的 `SOURCES` 多一筆即可，`worker.js` 完全不用改。
momo / 蝦皮的聯盟 API 核准後，照 `searchPchome` 的形狀補上 `search()`。
金鑰用 `wrangler secret put MOMO_TOKEN` 存，不要寫進程式碼。

## 測試

```bash
node test_worker.mjs        # 不需網路、不需 wrangler
```

解析層壞掉時通常不會拋錯，只是回 0 筆 —— 沒有測試就只能等使用者回報，
所以 `parse*` 都寫成純函式，用固定的假回應直接測。CI 每次都會跑。
