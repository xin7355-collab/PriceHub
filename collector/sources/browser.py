"""瀏覽器採集（選用；預設只在本機跑）

**為什麼需要這條路**
momo / Yahoo / 蝦皮沒有像 PChome 那樣可直接打的公開 JSON 端點。在聯盟網
API 申請下來之前，這是唯一能先看到資料的方式。

**為什麼不放進 GitHub Actions**
README「通路優先序」說得很清楚：runner 的出口是 Azure 資料中心 IP，
用瀏覽器硬闖風控站台會被直接擋下。這條路是給你在自己機器上（住宅 IP）
手動補資料用的，`collect.yml` 的 matrix 請維持只有 API 型平台。

**為什麼選擇器不寫在程式裡**
電商站台的 DOM 隨改版而變，寫死在 .py 裡等於每次改版都要動程式。
選擇器放在 `collector/profiles/{platform}.json`，改版時只改 JSON。

    # 1. 把渲染後的 HTML 存下來，對著它調選擇器
    python -m collector.browse --platform momo --keyword "WH-1000XM5" --dump

    # 2. 用存下來的 HTML 反覆試選擇器，完全不碰網路
    python -m collector.browse --platform momo --from-dump dumps/momo.html

    # 3. 選擇器對了再正式採集，寫進與 pchome 相同的 data/
    python -m collector.run --platform momo

crawl4ai 採 lazy import：沒安裝時本模組仍可正常 import，pchome 那條主線
完全不受影響。安裝請用 `pip install -r requirements-browser.txt`。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from .base import Offer, register, to_int_price

if TYPE_CHECKING:
    from ..httpclient import RateLimitedClient

log = logging.getLogger("priceport.browser")

PROFILE_DIR = Path(__file__).resolve().parent.parent / "profiles"


# ---------------------------------------------------------------- profile
def load_profile(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        p = json.load(f)
    for required in ("platform", "label", "search_url", "schema"):
        if required not in p:
            raise ValueError(f"{path.name} 缺少必要欄位 {required!r}")
    return p


def load_profiles() -> list[dict]:
    if not PROFILE_DIR.exists():
        return []
    out = []
    for p in sorted(PROFILE_DIR.glob("*.json")):
        try:
            out.append(load_profile(p))
        except (ValueError, json.JSONDecodeError) as e:
            log.error("profile %s 無法載入：%s", p.name, e)
    return out


# ---------------------------------------------------------------- 解析
def rows_to_offers(rows: list[dict], profile: dict,
                   base_url: str | None = None) -> list[Offer]:
    """把抽取出來的原始 dict 轉成 Offer。

    純函式，不碰網路也不碰 crawl4ai —— 這一層才是最容易寫錯的地方，
    所以刻意切出來讓 tests/test_browser.py 能直接餵假資料測。

    解不出價格或網址的列直接丟掉。寧可少一筆，也不要把猜出來的價格
    寫進歷史曲線。
    """
    platform = profile["platform"]
    base = base_url or profile["search_url"]
    offers: list[Offer] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        title = (row.get("title") or "").strip()
        price = to_int_price(row.get("price"))
        href = (row.get("url") or "").strip()
        if not (title and price and href):
            continue

        url = urljoin(base, href)
        image = (row.get("image") or "").strip() or None
        if image:
            image = urljoin(base, image)

        o = Offer(platform=platform, title=title, price=price, url=url,
                  image=image, raw_id=(row.get("raw_id") or "").strip() or None)
        if o.valid():
            offers.append(o)

    if rows and not offers:
        # 有列卻一筆都轉不出來 = 選擇器對到了容器但欄位對錯，
        # 或站台改版了。這是要立刻知道的訊號。
        log.error("%s 抽出 %d 列但全部無法轉成報價，請用 --dump 重新檢查選擇器；"
                  "第一列的鍵為 %s", platform, len(rows),
                  list(rows[0].keys()) if isinstance(rows[0], dict) else type(rows[0]))
    return offers


def extract_rows(html: str, profile: dict, url: str = "") -> list[dict]:
    """用 profile 的 CSS schema 把 HTML 抽成一堆 dict。需要 crawl4ai。"""
    try:
        from crawl4ai.extraction_strategy import JsonCssExtractionStrategy
    except ImportError as e:                      # pragma: no cover
        raise RuntimeError(
            "瀏覽器採集需要 crawl4ai：pip install -r requirements-browser.txt"
        ) from e

    strategy = JsonCssExtractionStrategy(profile["schema"])
    data: Any = strategy.extract(url or profile["search_url"], html)
    if isinstance(data, str):                     # 少數版本回傳 JSON 字串
        data = json.loads(data)
    return [d for d in (data or []) if isinstance(d, dict)]


# ---------------------------------------------------------------- 取頁
async def fetch_html(url: str, profile: dict) -> str:
    """用 crawl4ai 取得「渲染後」的 HTML。需要 crawl4ai + playwright。"""
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
    except ImportError as e:                      # pragma: no cover
        raise RuntimeError(
            "瀏覽器採集需要 crawl4ai：pip install -r requirements-browser.txt "
            "&& python -m playwright install chromium"
        ) from e

    run_kwargs: dict[str, Any] = {"cache_mode": CacheMode.BYPASS}
    if profile.get("wait_for"):
        run_kwargs["wait_for"] = profile["wait_for"]
    if profile.get("delay_ms"):
        run_kwargs["delay_before_return_html"] = profile["delay_ms"] / 1000

    async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as crawler:
        result = await crawler.arun(url=url, config=CrawlerRunConfig(**run_kwargs))

    if not getattr(result, "success", False):
        raise RuntimeError(f"取頁失敗 {url}：{getattr(result, 'error_message', '未知錯誤')}")
    return result.html or ""


def search_url_for(profile: dict, keyword: str) -> str:
    from urllib.parse import quote

    return profile["search_url"].replace("{keyword}", quote(keyword))


# ---------------------------------------------------------------- Source
class BrowserSource:
    """由 profile JSON 定義的平台。形狀與 pchome.PChome 完全相同，
    因此 run.py 與 storage 這兩層完全不需要知道它是用瀏覽器抓的。"""

    def __init__(self, profile: dict):
        self.profile = profile
        self.name = profile["platform"]
        self.label = profile["label"]

    async def search(self, client: "RateLimitedClient", keyword: str,
                     limit: int = 20) -> list[Offer]:
        # client 刻意不使用：瀏覽器有自己的連線與節流，
        # 但簽章必須與其他 source 一致，run.py 才能一視同仁。
        url = search_url_for(self.profile, keyword)
        html = await fetch_html(url, self.profile)
        rows = extract_rows(html, self.profile, url)
        return rows_to_offers(rows, self.profile, url)[:limit]


for _profile in load_profiles():
    # 註冊本身不需要 crawl4ai —— 只有真的去 search() 才會用到。
    # 這樣沒裝 crawl4ai 的環境（例如 Actions）仍能正常 import run.py。
    register(BrowserSource(_profile))
