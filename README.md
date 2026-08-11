# 比價通 PRICEPORT

台灣網購跨平台比價系統。GitHub Actions 排程採集，靜態 JSON 供應，零伺服器成本。

## 60 秒跑起來

```bash
pip install -r requirements.txt
python tests/test_normalize.py     # 離線測試，不需網路
python tests/test_momo.py          # 同上（momo 解析層）
python tests/test_storage.py tests/test_watchlist.py tests/test_browser.py
python tools/seed_demo.py          # 產生示範資料
python -m http.server 8000
# 開啟 http://localhost:8000/web/
```

確認前端跑起來後，接真實資料：

```bash
# 先確認 PChome 回傳欄位（第一次接平台必跑）
python -m collector.run --platform pchome --probe "Logitech MX Master 3S"

# 正式採集
rm -rf data/catalog data/series data/index.json
python -m collector.run --platform pchome
```

`--probe` 會印出每筆商品的指紋、品牌、型號與清理後標題。**如果 level 大量是 `weak`，就代表比對層需要調整**，不要急著往下做。

## 專案結構

```
collector/
  config.py      所有可調參數集中於此
  httpclient.py  Semaphore 限流 + 指數退避 + jitter
  normalize.py   標題清理、品牌型號抽取、跨平台指紋  ← 核心
  storage.py     分片 catalog + append-only 時序，原子寫入
  sources/       每個平台一個檔案（pchome=JSON API、momo=ld+json）
  profiles/      瀏覽器採集的 CSS 選擇器（目前是空的，見下方說明）
  run.py         單平台採集入口，含品質閘門
  browse.py      調選擇器用的除錯工具（本機）
web/index.html   單檔前端
edge/            即時搜尋代理（Cloudflare Worker，選用）
tools/seed_demo.py       離線示範資料
tools/probe_platforms.py 通路可行性偵察
```

## 三分支架構

| 分支 | 內容 | 為什麼分開 |
|---|---|---|
| `main` | 程式碼 + workflow | 程式碼歷史要乾淨 |
| `data` | 價格 JSON | 高頻 commit，不該污染程式碼歷史 |
| `gh-pages` | 前端 + 資料快照（由 workflow 產出） | 部署產物與原始碼分離 |

`data` 與 `gh-pages` 都不需要手動建立 —— workflow 第一次跑時偵測不到就會自己開。
`main` 的 `.gitignore` 擋掉 `/data/`，所以本機跑採集或 `seed_demo.py` 不會弄髒程式碼歷史。

`gh-pages` 每次都被覆寫成一個全新的單一 commit。部署產物不需要歷史，
留著只會讓 repo 一直長大。

### 開啟網站（只需要做一次）

**Settings → Pages → Source** 選 **Deploy from a branch**，分支選 `gh-pages`、目錄選 `/ (root)`。

網址會是 `https://<帳號>.github.io/<repo>/`。

這一步沒辦法自動化：建立 Pages 站台需要 `administration` 權限，而 Actions 的
`GITHUB_TOKEN` 沒有這個 scope（可用的只有 `contents` / `pages` / `id-token` 那幾個）。
所以 `actions/configure-pages` 的 `enablement: true` 一定會回
`Resource not accessible by integration`。推 `gh-pages` 分支只需要 `contents: write`，
這個拿得到 —— 但要讓 GitHub 真的把那個分支端出來，還是得有人按一次。

開啟之後就完全自動：每次採集完 workflow 會重推 `gh-pages`，網站跟著更新。

## 即時搜尋（選用）

排程採集只跑 `watchlist.json` 上的商品，所以搜尋框預設只在已採集的商品裡找。
想做到「輸入任何關鍵字都能當場查各平台」，部署 `edge/` 那支 Cloudflare Worker：

```bash
cd edge && wrangler deploy
# 把印出來的網址填進 web/config.json 的 searchApi
```

沒部署也完全不影響 —— `searchApi` 是 `null` 時前端根本不會去打它。
細節見 [`edge/README.md`](edge/README.md)。

**為什麼非得有一層代理**：GitHub Pages 是純靜態的，而各平台的搜尋端點都沒有
送 CORS 標頭，瀏覽器直接打會被同源政策擋掉，連錯誤訊息都讀不到。

## 資料格式

**catalog**（`data/catalog/{2碼}.json`）— 商品主檔，各平台最新報價：

```json
{ "025c897d": { "fp":"025c897d", "title":"WH-1000XM5 無線降噪耳機 黑色",
  "brand":"sony", "model":"wh-1000xm5", "level":"strong",
  "offers": { "pchome": {"price":12900,"url":"…","day":2414} },
  "best": {"platform":"momo","price":12513} } }
```

**series**（`data/series/{2碼}/{指紋}.json`）— 歷史價格：

```json
{ "fp":"025c897d", "series": { "momo": [[2324,12687],[2327,12501]] } }
```

只存 `[天數序號, 價格]` 兩個小整數。天數以 2020-01-01 為第 0 天。
一件商品一個平台跑滿一年約 4KB —— **這是能把歷史曲線塞進 Git 而不爆炸的關鍵**。

## 商品比對（本專案真正的難點）

爬蟲只是把資料搬進來。決定這是不是一個比價系統的，是能否判斷

- 蝦皮：`【現貨免運】Logitech 羅技 MX Master 3S 無線滑鼠 石墨灰 台灣公司貨`
- PChome：`Logitech MX Master 3S 靜音無線滑鼠-石墨灰`

是同一件商品。指紋分四級：

| level | 依據 | 可信度 |
|---|---|---|
| `strong` | 品牌 + 型號 + 顏色 | 跨平台可直接合併 |
| `medium` | 品牌 + 型號 | 可合併 |
| `loose` | 品牌 + 英數詞（AirPods 這類無型號商品） | 需抽查 |
| `weak` | 標題詞集雜湊 | 幾乎只能追蹤同平台歷史價 |

**遇到對不上的真實案例，請進 `tests/test_normalize.py` 補一組測資。** 目前已固化三個實際踩到的坑：

- momo 的 `【SONY 索尼】…` 格式 —— 品牌藏在會被清理掉的括號裡
- `DDR5`、`XM5` —— 字母結尾接數字的型號原本抓不到
- `AirPods Pro 2` —— 完全沒有可辨識型號

## 加平台

**有 API 的平台**（首選）：

1. 在 `collector/sources/` 新增檔案，實作 `search()` 回傳 `list[Offer]`
2. 檔尾呼叫 `register(YourSource())`
3. 在 `run.py` 的 import 加一行
4. 在 `collect.yml` 的 `matrix.platform` 加一個名字

`run.py` 與 workflow 的其他部分完全不用改。

**HTML 裡有結構化資料的平台**（如 momo 的 `ld+json`）：一樣寫在
`collector/sources/`，用 `client.get_text()` 取頁再解，不需要瀏覽器。

**純 JS 渲染的平台**（Yahoo / 蝦皮）：不用寫 Python，在 `collector/profiles/`
放一個 CSS 選擇器 JSON 就會自動註冊成一個平台，但只能在本機跑。詳見下節。

新增通路前先跑 `python tools/probe_platforms.py` 確認它屬於哪一種。

### 通路優先序

| 平台 | 取數方式 | 狀態 |
|---|---|---|
| PChome | 公開搜尋端點，無需金鑰 | ✅ 已實作 |
| momo | 搜尋頁內嵌的 schema.org `ld+json` | ✅ 已實作 |
| Yahoo | 純 JS 渲染，HTML 裡沒有商品 | 需瀏覽器 |
| 蝦皮 | 純 JS 渲染，HTML 裡沒有商品 | 需瀏覽器 |
| friDay | 搜尋頁只回 2.8KB（導頁） | 需瀏覽器 |
| 台灣樂天 | 連線逾時 | 待查 |
| 酷澎 | Coupang Open API 是**賣家**用，非商品搜尋 | v2 再議 |
| 淘寶 | 淘寶客申請門檻高；跨境運費關稅使比價失真 | v2 再議 |

以上不是查資料查來的，是 `tools/probe_platforms.py` 從 GitHub Actions 實測的結果
（Actions → 通路偵察，隨時可重跑）。

**原本寫在這裡的「runner 出口是資料中心 IP，會被風控直接擋下」並不成立** ——
實測 6 個平台有 5 個回 200，包含蝦皮。真正的分野不是 IP 被不被擋，而是
**商品資料在不在 HTML 裡**：

- PChome 有公開 JSON 端點 → 直接打
- momo 把商品放在 `ld+json`（一次 30 筆，含名稱／價格／圖片／網址）→ 直接解
- Yahoo／蝦皮／friDay 的 HTML 是空殼，商品由 JS 事後抓 → 只能用瀏覽器

所以 momo 不需要聯盟網 API，也不需要瀏覽器。順帶一提，聯盟網是**分潤制**，
本來就不用付費；門檻是審核不是費用。

加通路之前先跑一次偵察：

```bash
python tools/probe_platforms.py            # 全部掃一遍
python tools/probe_platforms.py --deep momo # 把內嵌 JSON 的結構攤開
```

## 瀏覽器採集（選用，只在本機跑）

Yahoo 與蝦皮的 HTML 是空殼（實測 0 個商品連結），只能用瀏覽器把頁面跑起來再解。
[crawl4ai](https://github.com/unclecode/crawl4ai) 負責這一段。

**不要加進 `collect.yml` 的 matrix**：瀏覽器很慢很重，而且這類站台的風控
針對的正是自動化瀏覽器。這條路是給你在自己機器上臨時補資料用的。

```bash
pip install -r requirements-browser.txt
python -m playwright install chromium

# 0. 先在 collector/profiles/ 放一個 yahoo.json（照 momo 那種形狀）

# 1. 把渲染後的 HTML 存下來
python -m collector.browse --platform yahoo --keyword "WH-1000XM5" --dump

# 2. 對著 dumps/yahoo.html 調選擇器，不碰網路，改一次試一次
python -m collector.browse --platform yahoo --from-dump dumps/yahoo.html

# 3. 選擇器對了再正式採集，寫進與 pchome 完全相同的 data/
python -m collector.run --platform yahoo
```

選擇器放在 `collector/profiles/{平台}.json`，站台改版時只要改 JSON，不用動 Python。
放一個新的 JSON 進去就等於多一個平台。

> momo 已經改走 `collector/sources/momo.py` 的 `ld+json`，不需要瀏覽器，
> 因此不再附任何 profile。這條路留給 Yahoo／蝦皮這種純 JS 渲染的平台 ——
> 實測它們的 HTML 裡連一個商品連結都沒有，只剩瀏覽器一途。

## 設計約束

- **限流**：`MAX_CONCURRENCY=3`，退避 1.5s → 3s → 6s → 12s，帶隨機抖動
- **冪等**：同日重跑覆寫當日價格點。Actions 的 cron 會延遲、會補跑，程式必須假設隨時被重放
- **品質閘門**：單平台失敗率 > 40% 直接 fail，不寫索引。半殘的資料比沒有資料更糟
- **故障隔離**：`matrix` + `fail-fast: false`，蝦皮掛掉不影響 momo
- **原子寫入**：temp + `os.replace`，job 被砍不會留下半個 JSON

## 免責

價格為排程快照，非即時報價。下單前請至各平台確認。
