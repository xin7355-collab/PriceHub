"""採集入口

用法：
  python -m collector.run --platform pchome
  python -m collector.run --platform pchome --probe "羅技 MX Master 3S"
  python -m collector.run --platform pchome --keywords "AirPods Pro,Switch 2"

設計要點：
  - 一次只跑一個平台。故障隔離交給 workflow 的 matrix，
    蝦皮掛掉不該讓 momo 的資料一起消失。
  - 冪等：同一天重跑會覆寫當日價格點，不會產生重複資料。
    GitHub Actions 的 cron 會延遲、會補跑，程式必須假設自己隨時被重放。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date

from . import config, storage
from .httpclient import RateLimitedClient
from .normalize import fingerprint
from .sources import browser  # noqa: F401  註冊 profiles/*.json 定義的瀏覽器平台
from .sources import pchome  # noqa: F401  註冊用
from .sources.base import REGISTRY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("priceport")


def load_keywords(explicit: str | None) -> list[str]:
    if explicit:
        return [k.strip() for k in explicit.split(",") if k.strip()]
    if not config.WATCHLIST_FILE.exists():
        log.error("找不到 %s", config.WATCHLIST_FILE)
        return []
    with config.WATCHLIST_FILE.open(encoding="utf-8") as f:
        wl = json.load(f)
    return [w["keyword"] for w in wl.get("items", []) if w.get("keyword")]


async def probe(platform: str, keyword: str) -> int:
    """傾印單次原始回應，用來確認欄位名稱。第一次接新平台必跑。"""
    src = REGISTRY[platform]
    async with RateLimitedClient(concurrency=1) as client:
        offers = await src.search(client, keyword, limit=5)
    for o in offers:
        fp, meta = fingerprint(o.title)
        print(json.dumps({
            "fp": fp, "level": meta["level"], "brand": meta["brand"],
            "model": meta["model"], "color": meta["color"],
            "price": o.price, "clean": meta["clean"], "raw": o.title,
        }, ensure_ascii=False, indent=2))
    print(f"\n共 {len(offers)} 筆", file=sys.stderr)
    return 0 if offers else 1


async def collect(platform: str, keywords: list[str], limit: int) -> int:
    src = REGISTRY[platform]
    today = config.day_index(date.today())
    catalog = storage.Catalog()

    ok = fail = written = 0

    async with RateLimitedClient() as client:
        async def one(kw: str) -> None:
            nonlocal ok, fail, written
            try:
                offers = await src.search(client, kw, limit=limit)
            except Exception as e:
                fail += 1
                log.warning("關鍵字失敗「%s」— %s", kw, e)
                return
            ok += 1
            for o in offers:
                fp, meta = fingerprint(o.title)
                catalog.upsert(fp, meta, {
                    "platform": o.platform, "title": o.title,
                    "price": o.price, "url": o.url,
                    "image": o.image, "day": today,
                })
                storage.append_point(fp, o.platform, o.price, today)
                written += 1
            log.info("「%s」→ %d 筆", kw, len(offers))

        # Semaphore 在 client 內部把關，這裡放心全部排入
        await asyncio.gather(*(one(k) for k in keywords))

    total = ok + fail
    log.info("採集完成：成功 %d / 失敗 %d，寫入 %d 筆報價", ok, fail, written)

    # 品質閘門：寧缺勿濫。半殘的資料比沒有資料更糟。
    if total >= config.MIN_QUERIES_FOR_GATE:
        ratio = fail / total
        if ratio > config.MAX_FAILURE_RATIO:
            log.error("失敗率 %.0f%% 超過門檻 %.0f%%，中止並不寫入索引",
                      ratio * 100, config.MAX_FAILURE_RATIO * 100)
            return 2
    if written == 0:
        log.error("零筆寫入，視為失敗")
        return 2

    stats = catalog.finalize()
    stats["updated_day"] = today
    stats["updated_at"] = date.today().isoformat()
    storage.write_index(stats)
    log.info("索引已更新：%d 件商品 / %d 個分片",
             stats["products"], len(stats["shards"]))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="priceport")
    ap.add_argument("--platform", required=True, choices=sorted(REGISTRY))
    ap.add_argument("--keywords", help="逗號分隔，覆寫 watchlist.json")
    ap.add_argument("--limit", type=int, default=20, help="每關鍵字取幾筆")
    ap.add_argument("--probe", metavar="KEYWORD", help="傾印解析結果，不寫檔")
    args = ap.parse_args()

    if args.probe:
        return asyncio.run(probe(args.platform, args.probe))

    kws = load_keywords(args.keywords)
    if not kws:
        log.error("沒有任何關鍵字")
        return 1
    return asyncio.run(collect(args.platform, kws, args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
