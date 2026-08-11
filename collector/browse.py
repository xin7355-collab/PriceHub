"""瀏覽器採集的除錯工具

正式採集走 `collector.run`。這支是專門用來「把選擇器調對」的：
站台改版時，你需要的是看見渲染後的 HTML，而不是猜。

    # 存下渲染後的 HTML（會寫到 dumps/momo.html）
    python -m collector.browse --platform momo --keyword "WH-1000XM5" --dump

    # 拿存下來的 HTML 反覆試選擇器 —— 不碰網路，改一次 JSON 試一次，幾秒一輪
    python -m collector.browse --platform momo --from-dump dumps/momo.html

    # 確認線上頁面現在能抽到什麼（會連線）
    python -m collector.browse --platform momo --keyword "WH-1000XM5"

輸出會一併印出指紋與比對層級，因為「抽到了」不等於「能跨平台合併」。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from .normalize import fingerprint
from .sources.browser import (
    PROFILE_DIR, extract_rows, fetch_html, load_profile, rows_to_offers,
    search_url_for,
)

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)-7s %(name)s | %(message)s")
log = logging.getLogger("priceport.browse")

DUMP_DIR = Path(__file__).resolve().parent.parent / "dumps"


def report(offers, profile) -> int:
    for o in offers:
        fp, meta = fingerprint(o.title)
        print(json.dumps({
            "fp": fp, "level": meta["level"], "brand": meta["brand"],
            "model": meta["model"], "color": meta["color"],
            "price": o.price, "clean": meta["clean"],
            "raw": o.title, "url": o.url,
        }, ensure_ascii=False, indent=2))

    print(f"\n共 {len(offers)} 筆", file=sys.stderr)
    if not offers:
        print(
            f"抽到 0 筆。先確認 {PROFILE_DIR / (profile['platform'] + '.json')} 的\n"
            f"baseSelector（{profile['schema']['baseSelector']}）真的有對到搜尋結果的每一個商品區塊，\n"
            "再逐一核對 fields。用 --dump 存下 HTML 對照最快。",
            file=sys.stderr,
        )
        return 1
    if not profile.get("verified"):
        print("提醒：這個 profile 還沒標記 verified。確認結果正確後，"
              "把 JSON 裡的 \"verified\" 改成 true。", file=sys.stderr)
    return 0


async def main_async(args) -> int:
    profile = load_profile(PROFILE_DIR / f"{args.platform}.json")

    if args.from_dump:
        html = Path(args.from_dump).read_text(encoding="utf-8")
        log.info("讀取 %s（%d KB），不連線", args.from_dump, len(html) // 1024)
        url = search_url_for(profile, args.keyword or "")
    else:
        if not args.keyword:
            log.error("需要 --keyword（或改用 --from-dump）")
            return 1
        url = search_url_for(profile, args.keyword)
        log.info("取頁 %s", url)
        html = await fetch_html(url, profile)
        log.info("取得 %d KB", len(html) // 1024)
        if args.dump:
            DUMP_DIR.mkdir(parents=True, exist_ok=True)
            out = DUMP_DIR / f"{args.platform}.html"
            out.write_text(html, encoding="utf-8")
            log.info("已存 %s —— 之後用 --from-dump %s 離線調選擇器", out, out)

    rows = extract_rows(html, profile, url)
    log.info("baseSelector 抽到 %d 列", len(rows))
    if rows:
        log.info("第一列原始內容：%s", json.dumps(rows[0], ensure_ascii=False))

    return report(rows_to_offers(rows, profile, url), profile)


def main() -> int:
    available = sorted(p.stem for p in PROFILE_DIR.glob("*.json")) or ["（無）"]
    ap = argparse.ArgumentParser(prog="priceport-browse")
    ap.add_argument("--platform", required=True,
                    help=f"profile 名稱，目前有：{', '.join(available)}")
    ap.add_argument("--keyword", help="搜尋關鍵字")
    ap.add_argument("--dump", action="store_true", help="把渲染後的 HTML 存到 dumps/")
    ap.add_argument("--from-dump", metavar="PATH",
                    help="改讀本機 HTML 檔，完全不連線")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
