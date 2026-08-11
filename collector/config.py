"""比價通 PRICEPORT — 全域設定

所有可調參數集中在此。修改行為請只動這個檔案，不要散落在各模組。
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------- 路徑
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PRICEPORT_DATA", ROOT / "data"))
CATALOG_DIR = DATA_DIR / "catalog"
SERIES_DIR = DATA_DIR / "series"
INDEX_FILE = DATA_DIR / "index.json"
WATCHLIST_FILE = ROOT / "watchlist.json"

# ---------------------------------------------------------------- 分片
# catalog 依指紋前兩碼分 256 片。單片超過 ~200KB 前端載入才會有感，
# 以目前規模一片約數十筆，遠低於門檻，但結構先留好。
SHARD_HEX_LEN = 2

# ---------------------------------------------------------------- 時序
# 以 2020-01-01 為第 0 天，價格點存 [dayIndex, price]，
# 兩個小整數而已 —— 這是能把歷史曲線塞進 Git 而不爆炸的關鍵。
EPOCH = date(2020, 1, 1)
SERIES_MAX_POINTS = 400        # 約一年多，超過從頭截斷
SERIES_MIN_DELTA = 1           # 價格變動小於此值不新增點（同日覆寫除外）

# ---------------------------------------------------------------- 網路
HTTP_TIMEOUT = 15.0
MAX_CONCURRENCY = 3            # 單平台同時請求數，寧可慢也不要被封
MAX_RETRIES = 4
BACKOFF_BASE = 1.5             # 退避基數（秒）：1.5, 3, 6, 12
BACKOFF_JITTER = 0.6           # 隨機抖動上限（秒），避免整批請求同步撞牆
POLITE_DELAY = 0.8             # 每次成功請求後的固定間隔

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------- 品質閘門
# 單平台失敗率超過此比例 → 該 job 直接 fail，不要產出髒資料。
# 故障隔離靠 workflow 的 matrix，這裡負責「寧缺勿濫」。
MAX_FAILURE_RATIO = 0.4
MIN_QUERIES_FOR_GATE = 3       # 查詢數太少時不套用比例閘門

# 同一個指紋底下，各平台報價的最高／最低比值超過這個數，就標記為可疑。
# 真實案例：PChome 的「犀牛盾手機殼組 Galaxy S25」$656 與 momo 的
# Galaxy S25 手機本體 $23,590，因為型號都抽到 s25 而被合併，
# 畫面上會顯示「省 $22,934」—— 錯誤的比價結果比沒有比價更糟。
# 同款商品跨平台價差極少超過兩倍，4 倍留了很寬的餘裕。
SUSPECT_PRICE_RATIO = 4.0


def day_index(d: date | None = None) -> int:
    """把日期壓成小整數。"""
    return ((d or date.today()) - EPOCH).days


def index_to_date(idx: int) -> date:
    from datetime import timedelta
    return EPOCH + timedelta(days=idx)
