"""通路可行性偵察

回答一個很具體的問題：**從 GitHub Actions 的 IP，哪些台灣電商打得到？**

README 斷言「runner 出口是 Azure 資料中心 IP，會被風控直接擋下」，
但那從來沒被驗證過 —— PChome 就打得好好的。在花時間寫任何一個平台的
解析器之前，先把這件事測出來，比讀十篇部落格有用。

每個平台只送 **一次** 請求，附正常 UA，並先讀 robots.txt。這是相容性
探測，不是採集：不取資料、不重試、不併發。

    python tools/probe_platforms.py

輸出四件事：
  reachable  連得到嗎？被擋的話是什麼狀態碼
  robots     搜尋路徑有沒有被 robots.txt 擋
  parseable  HTML 裡直接看得到商品嗎（不用瀏覽器就能解）
  embedded   有沒有內嵌 JSON（__NEXT_DATA__ 這類）—— 那是最好解的形式
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector.config import USER_AGENT  # noqa: E402

KEYWORD = "WH-1000XM5"
TIMEOUT = 20

# 每個平台一組候選。search 用 {kw} 當佔位符。
PLATFORMS = [
    {"name": "pchome", "label": "PChome 24h（對照組，已知可用）",
     "search": "https://ecshweb.pchome.com.tw/search/v3.3/all/results?q={kw}&page=1&sort=sale/dc",
     "kind": "json"},
    {"name": "momo", "label": "momo購物網",
     "search": "https://www.momoshop.com.tw/search/searchShop.jsp?keyword={kw}",
     "kind": "html"},
    {"name": "yahoo", "label": "Yahoo奇摩購物中心",
     "search": "https://tw.buy.yahoo.com/search/product?p={kw}",
     "kind": "html"},
    {"name": "rakuten", "label": "台灣樂天市場",
     "search": "https://www.rakuten.com.tw/search/{kw}/",
     "kind": "html"},
    {"name": "friday", "label": "friDay購物",
     "search": "https://shopping.friday.tw/search?keyword={kw}",
     "kind": "html"},
    {"name": "shopee", "label": "蝦皮購物（預期會被擋）",
     "search": "https://shopee.tw/search?keyword={kw}",
     "kind": "html"},
]

# 內嵌 JSON 是最好解的形式：不用瀏覽器，也不會因為改版換 class 就整批壞掉
EMBEDDED = [
    ("__NEXT_DATA__", re.compile(r'id="__NEXT_DATA__"')),
    ("__NUXT__", re.compile(r'window\.__NUXT__')),
    ("__INITIAL_STATE__", re.compile(r'window\.__INITIAL_STATE__')),
    ("application/ld+json", re.compile(r'type="application/ld\+json"')),
]

# 頁面上「看起來像商品」的痕跡。抓不到不代表沒有商品，
# 而是代表它多半靠 JS 才渲染得出來 —— 那就需要瀏覽器，成本完全不同。
PRODUCT_HINTS = [
    re.compile(r'goodsItemLi|prdName|goods-img'),       # momo 風格
    re.compile(r'data-(product|item)-id'),
    re.compile(r'class="[^"]*\b(product|item|goods)[-_]?(card|item|name|title)'),
    re.compile(r'itemprop="(price|name)"'),
]


def fetch(url: str, timeout: int = TIMEOUT):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/json,*/*",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def robots_blocks(base: str, path: str) -> str:
    """粗略判讀 robots.txt。只看 User-agent: * 那一段的 Disallow。"""
    try:
        parsed = urllib.parse.urlparse(base)
        status, _, body = fetch(f"{parsed.scheme}://{parsed.netloc}/robots.txt", timeout=10)
        if status != 200:
            return f"讀不到（HTTP {status}）"
        text = body.decode("utf-8", "replace")
    except Exception as e:
        return f"讀不到（{type(e).__name__}）"

    star, rules = False, []
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        k, _, v = line.partition(":")
        k, v = k.strip().lower(), v.strip()
        if k == "user-agent":
            star = v == "*"
        elif k == "disallow" and star and v:
            rules.append(v)
    hit = [r for r in rules if r != "/" and path.startswith(r)]
    if any(r == "/" for r in rules):
        return "全站 Disallow"
    return f"擋住 {hit[0]}" if hit else "未禁止"


def probe(p: dict) -> dict:
    url = p["search"].replace("{kw}", urllib.parse.quote(KEYWORD))
    out = {"name": p["name"], "label": p["label"], "url": url}
    out["robots"] = robots_blocks(url, urllib.parse.urlparse(url).path)

    try:
        status, ctype, body = fetch(url)
    except urllib.error.HTTPError as e:
        out.update(reachable=f"HTTP {e.code}", note="被拒絕")
        return out
    except Exception as e:
        out.update(reachable=type(e).__name__, note=str(e)[:60])
        return out

    out["reachable"] = f"HTTP {status}"
    out["bytes"] = len(body)
    text = body.decode("utf-8", "replace")

    if p["kind"] == "json":
        try:
            data = json.loads(text)
            n = len(data.get("prods") or data.get("Prods") or []) if isinstance(data, dict) else len(data)
            out["parseable"] = f"JSON，{n} 筆"
        except Exception:
            out["parseable"] = "宣稱 JSON 但解不開"
        return out

    out["embedded"] = ", ".join(n for n, rx in EMBEDDED if rx.search(text)) or "無"
    hits = sum(1 for rx in PRODUCT_HINTS if rx.search(text))
    if hits:
        out["parseable"] = f"HTML 有商品痕跡（{hits}/{len(PRODUCT_HINTS)} 種）"
    elif len(body) < 60_000:
        out["parseable"] = "頁面很小，多半是 JS 渲染或導頁"
    else:
        out["parseable"] = "抓不到商品痕跡，多半需要瀏覽器"
    return out


LD_JSON_RE = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
    re.S | re.I)

# 站台自己的前端在打哪些端點。找得到的話就走它，比解 HTML 穩定得多 ——
# PChome 那條管線就是這樣來的。
API_HINT_RE = re.compile(
    r'["\'](/(?:api|ajax)/[A-Za-z0-9_\-/\.]{3,60})["\']')


def deep(name: str) -> int:
    """挖單一平台：把內嵌 JSON 的結構攤開，並列出頁面裡出現的候選 API 路徑。

    偵察回報「有 ld+json」只代表有這個標籤，不代表裡面就是商品清單 ——
    也可能只是麵包屑或網站資訊。要寫解析器之前得先看清楚。
    """
    p = next((x for x in PLATFORMS if x["name"] == name), None)
    if not p:
        print(f"沒有這個平台：{name}；可用的有 "
              + "、".join(x["name"] for x in PLATFORMS))
        return 1

    url = p["search"].replace("{kw}", urllib.parse.quote(KEYWORD))
    print(f"深入偵察 {p['label']}\n{url}\n")
    try:
        status, ctype, body = fetch(url)
    except Exception as e:
        print(f"取頁失敗：{e}")
        return 1
    text = body.decode("utf-8", "replace")
    print(f"HTTP {status}　{len(body):,} bytes　{ctype}\n")

    blocks = LD_JSON_RE.findall(text)
    print(f"── application/ld+json：{len(blocks)} 塊")
    for i, raw in enumerate(blocks):
        try:
            data = json.loads(raw.strip())
        except Exception as e:
            print(f"   [{i}] 解不開（{type(e).__name__}: {str(e)[:40]}）")
            continue
        items = data if isinstance(data, list) else [data]
        for d in items:
            if not isinstance(d, dict):
                continue
            t = d.get("@type")
            print(f"   [{i}] @type={t}　鍵：{sorted(d.keys())[:8]}")
            # 商品清單長這樣：ItemList → itemListElement[] → 各自帶 offers
            lst = d.get("itemListElement")
            if isinstance(lst, list) and lst:
                print(f"        itemListElement：{len(lst)} 筆")
                print("        第一筆："
                      + json.dumps(lst[0], ensure_ascii=False)[:400])
            elif t in ("Product", "Offer"):
                print("        內容："
                      + json.dumps(d, ensure_ascii=False)[:400])

    hints = sorted(set(API_HINT_RE.findall(text)))
    print(f"\n── 頁面中出現的候選 API 路徑：{len(hints)} 個")
    for h in hints[:25]:
        print(f"   {h}")
    return 0


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--deep":
        return deep(sys.argv[2])

    print(f"關鍵字：{KEYWORD}　每個平台只送一次請求\n")
    rows = []
    for p in PLATFORMS:
        r = probe(p)
        rows.append(r)
        print(f"── {r['label']}")
        print(f"   連線     {r.get('reachable')}"
              + (f"　({r['bytes']:,} bytes)" if r.get("bytes") else ""))
        print(f"   robots   {r.get('robots')}")
        if r.get("parseable"):
            print(f"   解析     {r['parseable']}")
        if r.get("embedded"):
            print(f"   內嵌JSON {r['embedded']}")
        if r.get("note"):
            print(f"   備註     {r['note']}")
        print()

    ok = [r for r in rows if str(r.get("reachable", "")).startswith("HTTP 2")]
    print(f"結論：{len(ok)}/{len(rows)} 個平台從這個 IP 連得到 —— "
          + "、".join(r["name"] for r in ok))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
