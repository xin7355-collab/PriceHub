"""產生離線示範資料

讓你在還沒接通任何平台之前，就能先驗證前端與資料格式。
真實採集會覆蓋這些資料，指紋規則完全相同。

  python tools/seed_demo.py
"""
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector import config, storage          # noqa: E402
from collector.normalize import fingerprint    # noqa: E402

random.seed(20260811)

# 同一件商品在不同平台的真實寫法差異
PRODUCTS = [
    {
        "base": 3290,
        "titles": {
            "pchome": "Logitech MX Master 3S 靜音無線滑鼠-石墨灰",
            "momo": "【Logitech 羅技】MX Master 3S 無線靜音滑鼠 石墨灰",
            "shopee": "【現貨免運】Logitech 羅技 MX Master 3S 無線滑鼠 石墨灰 台灣公司貨",
        },
    },
    {
        "base": 7490,
        "titles": {
            "pchome": "Apple AirPods Pro 2 USB-C 主動降噪耳機",
            "momo": "【Apple 蘋果】AirPods Pro 2 USB-C 版本",
            "yahoo": "Apple AirPods Pro 2 (USB-C) 原廠公司貨 現貨",
        },
    },
    {
        "base": 1490,
        "titles": {
            "pchome": "Anker 737 Power Bank 24000mAh 行動電源-黑色",
            "shopee": "【下殺】Anker 737 行動電源 24000mAh 黑色 免運",
        },
    },
    {
        "base": 12900,
        "titles": {
            "pchome": "Sony WH-1000XM5 無線降噪耳機-黑色",
            "momo": "【SONY 索尼】WH-1000XM5 無線藍牙降噪耳機 黑色",
            "yahoo": "【現貨】Sony WH-1000XM5 降噪耳機 黑色 公司貨保固1年",
        },
    },
    {
        "base": 2190,
        "titles": {
            "pchome": "Kingston FURY Beast DDR5 32GB 5600 桌上型記憶體",
            "momo": "【Kingston 金士頓】FURY Beast DDR5 5600 32GB 記憶體",
        },
    },
]

PLATFORM_BIAS = {"pchome": 1.00, "momo": 0.97, "shopee": 0.93,
                 "yahoo": 1.03, "coupang": 0.95}


def main() -> None:
    today = date.today()
    catalog = storage.Catalog()
    n_offers = 0

    for prod in PRODUCTS:
        for platform, title in prod["titles"].items():
            fp, meta = fingerprint(title)
            price = int(prod["base"] * PLATFORM_BIAS[platform])

            # 90 天歷史，帶溫和波動與偶發促銷
            for back in range(90, -1, -3):
                d = today - timedelta(days=back)
                drift = 1 + random.uniform(-0.04, 0.05)
                if random.random() < 0.08:
                    drift *= 0.88          # 檔期特價
                storage.append_point(fp, platform,
                                     int(price * drift), config.day_index(d))

            catalog.upsert(fp, meta, {
                "platform": platform, "title": title, "price": price,
                "url": f"https://example.invalid/{platform}/{fp}",
                "image": None, "day": config.day_index(today),
            })
            n_offers += 1

    stats = catalog.finalize()
    # 通路清單由 finalize() 從實際報價算出，不要在這裡覆寫成 PLATFORM_BIAS ——
    # 那份表列了 coupang，但沒有任何一件示範商品有 coupang 的報價，
    # 覆寫的話前端就會顯示「5 個通路」而其實只有 4 個。
    stats.update(updated_day=config.day_index(today),
                 updated_at=today.isoformat(),
                 demo=True)
    storage.write_index(stats)
    print(f"示範資料已產生：{stats['products']} 件商品 / "
          f"{n_offers} 筆報價 / {len(stats['shards'])} 個分片")


if __name__ == "__main__":
    main()
