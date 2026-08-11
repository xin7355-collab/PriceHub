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
from typing import NamedTuple

from . import config, storage
from .httpclient import RateLimitedClient
from .normalize import fingerprint
from .sources import browser  # noqa: F401  註冊 profiles/*.json 定義的瀏覽器平台
from .sources import momo  # noqa: F401  註冊用
from .sources import pchome  # noqa: F401  註冊用
from .sources.base import REGISTRY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("priceport")


class Query(NamedTuple):
    """一個關鍵字，加上它自己的排除詞。"""
    keyword: str
    exclude: tuple[str, ...] = ()


def is_excluded(title: str, exclude: tuple[str, ...]) -> bool:
    """標題含有任一排除詞就丟掉。

    存在的理由很實際：搜「AirPods Pro 2」，PChome 回來的前幾十筆幾乎都是
    保護殼、貼膜、掛繩 —— 它們確實是商品，但不是使用者要比價的那一件，
    而且因為便宜，會直接霸佔「最低價」排序的最前面。

    用明確的排除詞而不是猜測式的相似度：規則看得懂、可以單元測試，
    對不上的時候使用者自己改 watchlist.json 就能修，不用動程式。
    """
    if not exclude:
        return False
    low = title.lower()
    return any(w.lower() in low for w in exclude)


def load_exclude_common() -> tuple[str, ...]:
    if not config.WATCHLIST_FILE.exists():
        return ()
    with config.WATCHLIST_FILE.open(encoding="utf-8") as f:
        return tuple(json.load(f).get("exclude_common") or ())


def load_queries(explicit: str | None) -> list[Query]:
    if explicit:
        return [Query(k.strip()) for k in explicit.split(",") if k.strip()]
    if not config.WATCHLIST_FILE.exists():
        log.error("找不到 %s", config.WATCHLIST_FILE)
        return []
    with config.WATCHLIST_FILE.open(encoding="utf-8") as f:
        wl = json.load(f)

    # 大部分商品都想擋掉同一批配件詞，逐筆重寫太囉嗦，因此支援共用清單。
    common = tuple(wl.get("exclude_common") or ())
    out: list[Query] = []
    for w in wl.get("items", []):
        kw = (w.get("keyword") or "").strip()
        if not kw:
            continue
        # 明確寫 "exclude": [] 代表「這一筆不要套共用清單」
        own = w.get("exclude")
        ex = tuple(own) if own is not None else common
        out.append(Query(kw, ex))
    return out


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


async def collect(platform: str, queries: list[Query], limit: int) -> int:
    src = REGISTRY[platform]
    today = config.day_index(date.today())
    catalog = storage.Catalog()

    ok = fail = written = dropped = 0

    async with RateLimitedClient() as client:
        async def one(q: Query) -> None:
            nonlocal ok, fail, written, dropped
            try:
                offers = await src.search(client, q.keyword, limit=limit)
            except Exception as e:
                fail += 1
                log.warning("關鍵字失敗「%s」— %s", q.keyword, e)
                return
            ok += 1
            kept = 0
            for o in offers:
                if is_excluded(o.title, q.exclude):
                    dropped += 1
                    continue
                fp, meta = fingerprint(o.title)
                catalog.upsert(fp, meta, {
                    "platform": o.platform, "title": o.title,
                    "price": o.price, "url": o.url,
                    "image": o.image, "day": today,
                })
                storage.append_point(fp, o.platform, o.price, today)
                written += 1
                kept += 1
            log.info("「%s」→ %d 筆（排除 %d）", q.keyword, kept, len(offers) - kept)

        # Semaphore 在 client 內部把關，這裡放心全部排入
        await asyncio.gather(*(one(q) for q in queries))

    if dropped:
        log.info("排除詞共濾掉 %d 筆配件／週邊", dropped)

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
    # 前端即時查詢時要套用同一套排除詞，否則畫面上半部濾掉了配件、
    # 下半部的即時結果又全是保護殼。放進 index.json 讓兩邊共用一份來源。
    stats["exclude_common"] = list(load_exclude_common())
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

    kws = load_queries(args.keywords)
    if not kws:
        log.error("沒有任何關鍵字")
        return 1
    return asyncio.run(collect(args.platform, kws, args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
