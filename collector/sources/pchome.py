"""PChome 24h 介接

用公開搜尋端點（前端自己在打的那支），無需金鑰，是 v1 唯一
「今天就能看到資料」的通路，因此作為第一條打通的管線。

⚠️ 欄位名稱請以第一次實跑的輸出為準。parse_item 刻意寫成寬容比對，
   欄位改名時會退回 None 而不是整批崩潰。跑 `--probe` 可傾印原始回應。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import Offer, register, to_int_price

if TYPE_CHECKING:                      # 解析層不得在執行期依賴網路層，
    from ..httpclient import RateLimitedClient   # 否則單元測試會被 aiohttp 綁架

log = logging.getLogger("priceport.pchome")

SEARCH_URL = "https://ecshweb.pchome.com.tw/search/v3.3/all/results"
PROD_URL = "https://24h.pchome.com.tw/prod/{pid}"


def _pick(d: dict, *keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def parse_item(item: dict) -> Offer | None:
    if not isinstance(item, dict):
        return None
    pid = _pick(item, "Id", "id", "prodId")
    name = _pick(item, "name", "Name", "prodName")
    price = to_int_price(_pick(item, "price", "Price", "salePrice"))
    if not (pid and name and price):
        return None

    pic = _pick(item, "picB", "picS", "picM", "img")
    image = f"https://cs-a.ecimg.tw{pic}" if isinstance(pic, str) and pic.startswith("/") else pic

    return Offer(
        platform="pchome",
        title=str(name),
        price=price,
        url=PROD_URL.format(pid=pid),
        image=image,
        raw_id=str(pid),
        extra={"origin_price": to_int_price(_pick(item, "originPrice", "OriginPrice"))},
    )


class PChome:
    name = "pchome"
    label = "PChome 24h"

    async def search(self, client: "RateLimitedClient", keyword: str,
                     limit: int = 20) -> list[Offer]:
        data = await client.get_json(SEARCH_URL, params={
            "q": keyword, "page": 1, "sort": "sale/dc",
        })
        items = []
        if isinstance(data, dict):
            items = data.get("prods") or data.get("Prods") or []
        elif isinstance(data, list):
            items = data

        offers: list[Offer] = []
        for it in items[:limit]:
            o = parse_item(it)
            if o and o.valid():
                offers.append(o)

        if items and not offers:
            # 有資料卻一筆都解不出來 = 欄位改了，這是要立刻知道的訊號
            log.error("PChome 回傳 %d 筆但解析全失敗，欄位可能已變更：%s",
                      len(items), list(items[0].keys())[:12] if isinstance(items[0], dict) else type(items[0]))
        return offers


register(PChome())
