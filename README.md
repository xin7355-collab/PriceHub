# 比價通 PRICEPORT

台灣網購跨平台比價系統。GitHub Actions 排程採集，靜態 JSON 供應，零伺服器成本。

## 60 秒跑起來

```bash
pip install -r requirements.txt
python tests/test_normalize.py     # 離線測試，不需網路
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
  sources/       每個平台一個檔案
  run.py         單平台採集入口，含品質閘門
web/index.html   單檔前端，無外部相依
tools/seed_demo.py  離線示範資料
```

## 三分支架構

| 分支 | 內容 | 為什麼分開 |
|---|---|---|
| `main` | 程式碼 + workflow | 程式碼歷史要乾淨 |
| `data` | 價格 JSON | 高頻 commit，不該污染程式碼歷史 |
| `gh-pages` | 前端（由 workflow 產出） | 部署產物與原始碼分離 |

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

1. 在 `collector/sources/` 新增檔案，實作 `search()` 回傳 `list[Offer]`
2. 檔尾呼叫 `register(YourSource())`
3. 在 `run.py` 的 import 加一行
4. 在 `collect.yml` 的 `matrix.platform` 加一個名字

`run.py` 與 workflow 的其他部分完全不用改。

### 通路優先序

| 平台 | 取數方式 | 狀態 |
|---|---|---|
| PChome | 公開搜尋端點，無需金鑰 | ✅ 已實作 |
| momo | 聯盟網 Affiliates.One 商品 API | 待申請 |
| 蝦皮 | 蝦皮分潤計畫（需社群 300 好友 + 審核） | 待申請 |
| Yahoo | 聯盟網 / 通路王 | 待申請 |
| 酷澎 | Coupang Open API 是**賣家**用，非商品搜尋 | v2 再議 |
| 淘寶 | 淘寶客申請門檻高；跨境運費關稅使比價失真 | v2 再議 |

不建議在 GitHub Actions 上用瀏覽器硬爬蝦皮／淘寶：runner 的出口是 Azure 資料中心 IP，
會被風控直接擋下，成功率低到無法當產品用。走官方通路是唯一能穩定運轉的路。

## 設計約束

- **限流**：`MAX_CONCURRENCY=3`，退避 1.5s → 3s → 6s → 12s，帶隨機抖動
- **冪等**：同日重跑覆寫當日價格點。Actions 的 cron 會延遲、會補跑，程式必須假設隨時被重放
- **品質閘門**：單平台失敗率 > 40% 直接 fail，不寫索引。半殘的資料比沒有資料更糟
- **故障隔離**：`matrix` + `fail-fast: false`，蝦皮掛掉不影響 momo
- **原子寫入**：temp + `os.replace`，job 被砍不會留下半個 JSON

## 免責

價格為排程快照，非即時報價。下單前請至各平台確認。
