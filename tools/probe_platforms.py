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
# PChome 那條管線就是這樣來的。第一版只找 /api/ 與 /ajax/，太窄了，
# momo 掃出 0 個；放寬成常見的資料端點命名。
API_HINT_RE = re.compile(
    r'["\'`]((?:https?://[A-Za-z0-9.\-]+)?/(?:api|ajax|rest|graphql|service|data|'
    r'search|product|goods)[A-Za-z0-9_\-/\.]{0,60})["\'`]')

# 商品連結的樣子。HTML 裡找得到這些，就代表不用瀏覽器也解得出商品 ——
# 這比「有沒有 ld+json」更直接。
PRODUCT_LINK_RE = {
    "momo": re.compile(r'i_code=(\d+)'),
    "yahoo": re.compile(r'/p/[A-Za-z0-9\-]{6,}'),
    "shopee": re.compile(r'-i\.(\d+)\.(\d+)'),
    "friday": re.compile(r'/product/(\d+)'),
    "rakuten": re.compile(r'/product/[A-Za-z0-9\-]+'),
    "pchome": re.compile(r'/prod/([A-Z0-9\-]+)'),
}


def walk_types(node, acc, depth=0):
    """ld+json 常常把商品埋在 @graph 裡，只看最外層會什麼都看不到。"""
    if depth > 6:
        return
    if isinstance(node, dict):
        t = node.get("@type")
        if isinstance(t, str):
            acc[t] = acc.get(t, 0) + 1
        elif isinstance(t, list):
            for x in t:
                acc[str(x)] = acc.get(str(x), 0) + 1
        for v in node.values():
            walk_types(v, acc, depth + 1)
    elif isinstance(node, list):
        for v in node:
            walk_types(v, acc, depth + 1)


def find_products(node, out, depth=0):
    """撈出任何看起來像商品的節點（有 name，且有 offers 或 price）。"""
    if depth > 6 or len(out) >= 3:
        return
    if isinstance(node, dict):
        has_name = isinstance(node.get("name"), str)
        if has_name and ("offers" in node or "price" in node
                         or node.get("@type") == "Product"):
            out.append(node)
        for v in node.values():
            find_products(v, out, depth + 1)
    elif isinstance(node, list):
        for v in node:
            find_products(v, out, depth + 1)


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

    # ---- 1. 商品連結：最直接的問題 —— HTML 裡到底有沒有商品？
    rx = PRODUCT_LINK_RE.get(name)
    if rx:
        ids = rx.findall(text)
        uniq = sorted(set(map(str, ids)))
        print(f"── 商品連結（{rx.pattern}）：{len(ids)} 個，去重後 {len(uniq)}")
        for s in uniq[:5]:
            print(f"   {s}")
        print()

    # ---- 2. ld+json：要往 @graph 裡面走，只看最外層會什麼都看不到
    blocks = LD_JSON_RE.findall(text)
    print(f"── application/ld+json：{len(blocks)} 塊")
    for i, raw in enumerate(blocks):
        try:
            data = json.loads(raw.strip())
        except Exception as e:
            print(f"   [{i}] 解不開（{type(e).__name__}: {str(e)[:40]}）")
            continue
        types: dict[str, int] = {}
        walk_types(data, types)
        print(f"   [{i}] 遞迴找到的 @type：{types}")
        prods: list = []
        find_products(data, prods)
        if prods:
            print(f"        像商品的節點：{len(prods)} 個，第一個：")
            print("        " + json.dumps(prods[0], ensure_ascii=False)[:500])
        else:
            print("        沒有帶價格的商品節點")
            print("        原始內容（前 400 字）："
                  + json.dumps(data, ensure_ascii=False)[:400])

    # ---- 3. 端點候選
    hints = sorted(set(API_HINT_RE.findall(text)))
    print(f"\n── 候選端點：{len(hints)} 個")
    for h in hints[:30]:
        print(f"   {h}")

    # ---- 4. 價格字樣在不在 HTML 裡（在的話就有得解，不用瀏覽器）
    # 千分位逗號要吃得下來，否則 "NT$ 8,690" 這種最常見的寫法會整個漏掉
    prices = [m.replace(",", "")
              for m in re.findall(r'(?:NT\$|\$)\s?([1-9][\d,]{2,9})', text)]
    print(f"\n── HTML 裡的價格字樣：{len(prices)} 個"
          + (f"，前幾個：{sorted(set(prices), key=int)[:8]}" if prices else ""))
    return 0


# ================================================================ 大範圍探勘
# --discover：拿一份網域清單（tools/tw_sites.json，取自 BigGo 擴充功能的
# 開源站台表），逐站找出「它自己公開的搜尋網址」，再用跟 momo 同一套方法
# 檢查搜尋結果頁裡有沒有現成的結構化商品資料。
#
# 只用站台自己公開的搜尋入口（OpenSearch 描述檔、首頁上 method=GET 的搜尋表單），
# 不猜網址。猜出來的網址即使剛好打得通，也不代表對方願意被這樣用。
# 每站最多 5 個請求，彼此之間有間隔；不同站台才平行。

DISCOVER_KEYWORD = "耳機"        # 3C 電商幾乎都有，非 3C 的站台搜不到也正常
DISCOVER_WORKERS = 8
DISCOVER_TIMEOUT = 12
DISCOVER_GAP = 0.6               # 同一站台兩個請求之間至少隔這麼久

OPENSEARCH_LINK_RE = re.compile(
    r'<link[^>]+type=["\']application/opensearchdescription\+xml["\'][^>]*>', re.I)
HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)
OS_TEMPLATE_RE = re.compile(
    r'<Url[^>]+type=["\']text/html["\'][^>]*template=["\']([^"\']+)["\']'
    r'|<Url[^>]+template=["\']([^"\']+)["\'][^>]*type=["\']text/html["\']', re.I)
FORM_RE = re.compile(r"<form\b([^>]*)>(.*?)</form>", re.I | re.S)
ATTR_RE = lambda name: re.compile(name + r'\s*=\s*["\']([^"\']*)["\']', re.I)
INPUT_NAME_RE = re.compile(
    r'<input\b[^>]*\bname\s*=\s*["\'](q|keyword|keywords|kw|query|search|'
    r'searchword|key|k|s|w|word|text|term)["\']', re.I)


def _norm_home(dom: str) -> str:
    dom = dom.strip()
    if not dom.startswith("http"):
        dom = "https://" + dom
    u = urllib.parse.urlparse(dom)
    return f"{u.scheme}://{u.netloc}/"


def _get(url: str):
    """回 (status, text)；任何錯誤都吞掉並回報，一站失敗不該拖垮整批。"""
    try:
        status, ctype, body = fetch(url, timeout=DISCOVER_TIMEOUT)
        return status, body[:3_000_000].decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return type(e).__name__, ""


def find_search_url(home: str, html: str, gap) -> tuple[str, str] | None:
    """找站台自己公開的搜尋入口。回 (網址模板, 來源)，模板以 {q} 代表關鍵字。"""
    # 1) OpenSearch：站台明確宣告「我的搜尋網址長這樣」，最可靠
    for tag in OPENSEARCH_LINK_RE.findall(html):
        m = HREF_RE.search(tag)
        if not m:
            continue
        gap()
        st, xml = _get(urllib.parse.urljoin(home, m.group(1)))
        if st == 200:
            t = OS_TEMPLATE_RE.search(xml)
            if t:
                tpl = (t.group(1) or t.group(2)).replace("&amp;", "&")
                tpl = re.sub(r"\{searchTerms\}", "{q}", tpl)
                tpl = re.sub(r"[?&][^=&]+=\{[^}]*\?\}", "", tpl)   # 丟掉選填參數
                return urllib.parse.urljoin(home, tpl), "opensearch"
    # 2) 首頁上的 GET 搜尋表單
    for attrs, inner in FORM_RE.findall(html):
        method = (ATTR_RE("method").search(attrs) or [None, "get"])[1].lower()
        if method != "get":
            continue
        m = INPUT_NAME_RE.search(inner)
        if not m:
            continue
        action = (ATTR_RE("action").search(attrs) or [None, ""])[1]
        base = urllib.parse.urljoin(home, action or "/")
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}{m.group(1)}={{q}}", "form"
    return None


def analyze_results(html: str) -> dict:
    """搜尋結果頁裡有什麼現成的資料。判準與 momo 那次完全相同。"""
    # 不能用 find_products：它為了 --deep 的顯示只收前 3 個就停，
    # 拿來計數會把 30 個商品報成 3 個。
    def count(node, depth=0):
        if depth > 8:
            return 0
        if isinstance(node, list):
            return sum(count(v, depth + 1) for v in node)
        if not isinstance(node, dict):
            return 0
        t = node.get("@type")
        own = 1 if ((t == "Product" or (isinstance(t, list) and "Product" in t))
                    and isinstance(node.get("name"), str)
                    and ("offers" in node or "price" in node)) else 0
        return own + sum(count(v, depth + 1) for v in node.values())

    n_products = 0
    for raw in LD_JSON_RE.findall(html):
        try:
            n_products += count(json.loads(raw.strip()))
        except Exception:
            pass
    embedded = [n for n, rx in EMBEDDED if n != "application/ld+json" and rx.search(html)]
    prices = re.findall(r"(?:NT\$|\$)\s?[1-9][\d,]{2,9}", html)
    return {"ld_products": n_products, "embedded": embedded, "prices": len(prices),
            "bytes": len(html)}


def discover_one(key: str, dom: str) -> dict:
    import time
    last = [0.0]

    def gap():
        wait = DISCOVER_GAP - (time.monotonic() - last[0])
        if wait > 0:
            time.sleep(wait)
        last[0] = time.monotonic()

    home = _norm_home(dom)
    r = {"key": key, "home": home}
    gap()
    st, html = _get(home)
    r["home_status"] = st
    if st != 200:
        r["verdict"] = "連不上"
        return r

    found = find_search_url(home, html, gap)
    if not found:
        r["verdict"] = "找不到公開的搜尋入口"
        return r
    tpl, src = found
    url = tpl.replace("{q}", urllib.parse.quote(DISCOVER_KEYWORD))
    r.update(search_url=url, search_src=src)

    rb = robots_blocks(url, urllib.parse.urlparse(url).path)
    r["robots"] = rb
    if rb.startswith("擋住") or rb.startswith("全站"):
        r["verdict"] = "robots.txt 禁止"
        return r

    gap()
    st, res = _get(url)
    r["search_status"] = st
    if st != 200:
        r["verdict"] = f"搜尋頁 HTTP {st}"
        return r
    a = analyze_results(res)
    r.update(a)
    if a["ld_products"] >= 3:
        r["verdict"] = "A 結構化商品資料（同 momo，可直接接）"
    elif a["embedded"]:
        r["verdict"] = "B 內嵌前端狀態 JSON（可接，要寫解析）"
    elif a["prices"] >= 5:
        r["verdict"] = "C HTML 裡有價格（要解 HTML，較脆弱）"
    else:
        r["verdict"] = "D 搜尋頁沒有可用資料（多半是 JS 渲染）"
    return r


def discover(path: str) -> int:
    from concurrent.futures import ThreadPoolExecutor

    sites = json.loads(Path(path).read_text(encoding="utf-8"))["sites"]
    print(f"探勘 {len(sites)} 個站台，關鍵字「{DISCOVER_KEYWORD}」，"
          f"{DISCOVER_WORKERS} 站平行、同站請求間隔 {DISCOVER_GAP}s\n")
    with ThreadPoolExecutor(DISCOVER_WORKERS) as ex:
        rows = list(ex.map(lambda kv: discover_one(*kv), sites.items()))

    order = {"A": 0, "B": 1, "C": 2, "D": 3}
    rows.sort(key=lambda r: (order.get(r["verdict"][0], 9), r["key"]))
    for r in rows:
        extra = ""
        if "ld_products" in r:
            extra = (f"  ld+json 商品 {r['ld_products']}　內嵌 {','.join(r['embedded']) or '-'}"
                     f"　價格字樣 {r['prices']}")
        tag = r["verdict"][0] if r["verdict"][0] in "ABCD" else "-"
        note = "" if tag != "-" else f"  {r['verdict']}"
        print(f"{tag}  {r['key']:34}{extra}{note}")
        if r.get("search_url") and r["verdict"][0] in "ABC":
            print(f"    {r['search_src']:10} {r['search_url']}")

    import collections
    c = collections.Counter(r["verdict"] for r in rows)
    print("\n── 結論")
    for k, v in sorted(c.items(), key=lambda kv: order.get(kv[0][0], 9)):
        print(f"   {v:4}  {k}")
    return 0

def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--deep":
        return deep(sys.argv[2])
    if len(sys.argv) > 2 and sys.argv[1] == "--discover":
        return discover(sys.argv[2])

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
