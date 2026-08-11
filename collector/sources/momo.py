"""momo購物網介接（走頁面內嵌的 schema.org 結構化資料）

**為什麼是這條路**

搜尋頁的 HTML 裡有一塊 `<script type="application/ld+json">`，內容是
schema.org 的 `ItemList`，一次含 30 個 `Product`，每個都帶 name / image /
url / offers.price。這是 momo 給搜尋引擎看的資料，實測（tools/probe_platforms.py）
確認：

  - 從 GitHub Actions 的 IP 打得到（HTTP 200，178KB）
  - robots.txt 未禁止 /search/
  - 不需要瀏覽器、不需要金鑰、不需要聯盟計畫審核

**為什麼不用 CSS 選擇器**

本專案原本準備用 crawl4ai + CSS 選擇器抓 momo。結構化資料勝過選擇器：
它是給機器讀的公開契約，欄位名稱穩定；CSS class 是實作細節，改版就換。
`ld+json` 壞掉會整塊消失（一眼看得出來），選擇器壞掉則是靜默回 0 筆。

**脆弱點**

momo 哪天把這塊 ld+json 拿掉就會回 0 筆。`search()` 在「有 HTML 卻抽不到
任何商品」時會 log.error —— 那是要立刻知道的訊號，不是慢慢發現。
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from .base import Offer, register, to_int_price

if TYPE_CHECKING:                      # 解析層不得在執行期依賴網路層
    from ..httpclient import RateLimitedClient

log = logging.getLogger("priceport.momo")

SEARCH_URL = "https://www.momoshop.com.tw/search/searchShop.jsp"

LD_JSON_RE = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
    re.S | re.I)

MAX_DEPTH = 8          # @graph 巢狀不會太深，設個上限免得畸形資料把自己繞死


def _iter_products(node: Any, out: list[dict], depth: int = 0) -> None:
    """遞迴撈出所有 Product 節點。

    momo 把東西包成 WebPage → @graph → ItemList → itemListElement[]，
    只看最外層會什麼都拿不到。刻意不寫死這條路徑：只要是帶 name 與
    offers 的節點就收，之後 momo 改變包裝方式也還能運作。
    """
    if depth > MAX_DEPTH:
        return
    if isinstance(node, dict):
        t = node.get("@type")
        types = t if isinstance(t, list) else [t]
        if "Product" in types and isinstance(node.get("name"), str):
            out.append(node)
        for v in node.values():
            _iter_products(v, out, depth + 1)
    elif isinstance(node, list):
        for v in node:
            _iter_products(v, out, depth + 1)


def _price_of(product: dict) -> int | None:
    """offers 可能是物件，也可能是陣列（多賣家）。取得到的最低價。"""
    offers = product.get("offers")
    cands = offers if isinstance(offers, list) else [offers]
    prices = []
    for o in cands:
        if isinstance(o, dict):
            p = to_int_price(o.get("price") or o.get("lowPrice"))
            if p:
                prices.append(p)
    return min(prices) if prices else None


def _clean_url(u: str) -> str:
    """把搜尋來源的追蹤參數剝掉，只留 i_code。

    momo 的商品網址帶了 Area/mdiv/oid/cid/kw 一串來源標記，同一件商品
    從不同關鍵字搜到就會產生不同網址 —— 那會讓 catalog 每天都在變動，
    明明價格沒動也照樣 commit。
    """
    m = re.search(r"i_code=(\d+)", u or "")
    if m:
        return f"https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code={m.group(1)}"
    return u


def parse_products(html: str) -> list[Offer]:
    """把搜尋頁 HTML 轉成 Offer。純函式，不碰網路 —— 這層才是會壞的地方。"""
    offers: list[Offer] = []
    seen: set[str] = set()

    for raw in LD_JSON_RE.findall(html or ""):
        try:
            data = json.loads(raw.strip())
        except (json.JSONDecodeError, ValueError):
            continue                      # 一塊壞掉不該讓其他塊跟著陣亡

        products: list[dict] = []
        _iter_products(data, products)

        for p in products:
            title = (p.get("name") or "").strip()
            price = _price_of(p)
            url = _clean_url(str(p.get("url") or ""))
            if not (title and price and url):
                continue

            raw_id = None
            m = re.search(r"i_code=(\d+)", url)
            if m:
                raw_id = m.group(1)
                if raw_id in seen:        # 同一件商品在頁面上可能出現兩次
                    continue
                seen.add(raw_id)

            image = p.get("image")
            if isinstance(image, list):
                image = image[0] if image else None

            o = Offer(
                platform="momo", title=title, price=price, url=url,
                image=str(image) if image else None, raw_id=raw_id,
            )
            if o.valid():
                offers.append(o)
    return offers


class Momo:
    name = "momo"
    label = "momo購物網"

    async def search(self, client: "RateLimitedClient", keyword: str,
                     limit: int = 20) -> list[Offer]:
        html = await client.get_text(SEARCH_URL, params={"keyword": keyword})
        offers = parse_products(html)

        if html and not offers:
            # 有頁面卻一筆都抽不到 = momo 把 ld+json 拿掉或改了格式。
            # 這是要立刻知道的訊號，不是等使用者回報。
            log.error("momo 回傳 %d KB 但抽不到任何商品，"
                      "頁面內嵌的 ld+json 可能已變更", len(html) // 1024)
        return offers[:limit]


register(Momo())
