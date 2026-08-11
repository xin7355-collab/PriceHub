"""瀏覽器採集層的離線測試。

分兩段：
  - rows_to_offers 是純函式，永遠會跑。它是最容易寫錯的一層
    （相對網址、髒價格、缺欄位的廣告列）。
  - extract_rows 需要 crawl4ai。沒安裝就跳過 —— Actions 上不裝 crawl4ai，
    這裡硬性 import 會讓主管線的 CI 無謂變紅。

    pip install -r requirements-browser.txt   # 想連 extract 一起測就裝這個
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector.sources.base import to_int_price          # noqa: E402
from collector.sources.browser import (                  # noqa: E402
    load_profiles, rows_to_offers, search_url_for,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "momo_like.html"

# 目前沒有任何 profile 隨程式碼出貨 —— momo 已改走 sources/momo.py 的
# ld+json，比 CSS 選擇器穩定得多。但瀏覽器採集的機制要留著：Yahoo 與蝦皮
# 實測都是純 JS 渲染（0 商品連結、0 結構化資料），將來要接只剩這條路。
# 因此測試自帶一份 profile，不依賴 collector/profiles/ 裡有沒有檔案。
MOMO = {
    "platform": "momo",
    "label": "測試用 profile",
    "search_url": "https://www.momoshop.com.tw/search/searchShop.jsp?keyword={keyword}",
    "schema": {
        "baseSelector": "li.goodsItemLi",
        "fields": [
            {"name": "title", "selector": "h3.prdName", "type": "text"},
            {"name": "price", "selector": "span.price b", "type": "text"},
            {"name": "url", "selector": "a.goods-img-url",
             "type": "attribute", "attribute": "href"},
            {"name": "image", "selector": "img.prdImg",
             "type": "attribute", "attribute": "src"},
        ],
    },
}


# ---------------------------------------------------------------- 價格
def test_price_parsing():
    assert to_int_price(3290) == 3290
    assert to_int_price("3,290") == 3290
    assert to_int_price("$12,900") == 12900
    assert to_int_price("特價$3,190起") == 3190
    assert to_int_price("NT$1,299元") == 1299
    assert to_int_price("") is None
    assert to_int_price("缺貨") is None
    assert to_int_price(0) is None
    assert to_int_price(-5) is None
    assert to_int_price(True) is None      # bool 是 int 的子類，必須擋掉


# ---------------------------------------------------------------- profile
def test_every_profile_is_wellformed():
    """profile 壞掉會在採集時才炸，成本高，這裡先擋下來。
    目前沒有 profile 出貨，所以這個測試多半是空跑 —— 但只要有人放了一個
    進去，它就會馬上被檢查。"""
    for p in load_profiles() + [MOMO]:
        assert "{keyword}" in p["search_url"], f"{p['platform']} 的 search_url 沒有 {{keyword}}"
        assert p["schema"]["baseSelector"], p["platform"]
        names = {f["name"] for f in p["schema"]["fields"]}
        # 少了這三個，rows_to_offers 一定整批丟掉，等於白跑
        assert {"title", "price", "url"} <= names, f"{p['platform']} 缺必要欄位 {names}"


def test_search_url_encodes_keyword():
    url = search_url_for(MOMO, "WH-1000XM5 耳機")
    assert "{keyword}" not in url
    assert " " not in url                  # 空白必須編碼，否則整條網址壞掉
    assert "%E8%80%B3%E6%A9%9F" in url     # 中文有被編碼


# ---------------------------------------------------------------- 轉換
def test_rows_to_offers_happy_path():
    rows = [{"title": "【SONY 索尼】WH-1000XM5 黑色", "price": "12,900",
             "url": "/goods/GoodsDetail.jsp?i_code=1",
             "image": "//img1.momoshop.com.tw/a.jpg"}]
    offers = rows_to_offers(rows, MOMO, "https://www.momoshop.com.tw/search/searchShop.jsp?keyword=x")
    assert len(offers) == 1
    o = offers[0]
    assert o.platform == "momo" and o.price == 12900 and o.valid()
    # 相對網址必須補成絕對網址，否則前端點擊會連到自己的網域
    assert o.url == "https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code=1"
    # 協定相對網址（//host/...）也要補上協定
    assert o.image.startswith("https://")


def test_rows_to_offers_drops_unusable():
    """缺價格 / 缺連結 / 缺標題的列必須丟掉，不能猜。"""
    rows = [
        {"title": "沒有價格的廣告", "url": "/goods/1"},
        {"title": "缺連結", "price": "999"},
        {"price": "999", "url": "/goods/2"},
        {"title": "價格看不懂", "price": "電洽", "url": "/goods/3"},
        "這根本不是 dict",
    ]
    assert rows_to_offers(rows, MOMO) == []


def test_rows_to_offers_keeps_only_valid_among_mixed():
    rows = [
        {"title": "好的商品", "price": "1,000", "url": "/goods/1"},
        {"title": "壞的商品", "price": "電洽", "url": "/goods/2"},
    ]
    offers = rows_to_offers(rows, MOMO)
    assert len(offers) == 1 and offers[0].title == "好的商品"


# ---------------------------------------------------------------- 端對端
def test_extract_from_fixture_html():
    """profile → CSS 抽取 → Offer 的完整管線。需要 crawl4ai。"""
    try:
        import crawl4ai  # noqa: F401
    except ImportError:
        print("      SKIP  未安裝 crawl4ai（pip install -r requirements-browser.txt）")
        return

    from collector.sources.browser import extract_rows

    html = FIXTURE.read_text(encoding="utf-8")
    url = "https://www.momoshop.com.tw/search/searchShop.jsp?keyword=test"
    rows = extract_rows(html, MOMO, url)
    assert len(rows) == 4, f"baseSelector 應對到 4 個區塊，實際 {len(rows)}"

    offers = rows_to_offers(rows, MOMO, url)
    # 4 列中只有 2 列是可用的商品（另兩列缺價格 / 缺連結）
    assert len(offers) == 2, [json.dumps(r, ensure_ascii=False) for r in rows]
    assert offers[0].price == 12900
    assert offers[1].price == 3190          # "特價$3,190起" 要能解出來
    assert all(o.url.startswith("https://www.momoshop.com.tw/") for o in offers)


def test_fixture_titles_match_pchome_fingerprints():
    """真正的驗收：momo 抽出來的標題，要能跟 PChome 的寫法合併成同一個指紋。
    抽得到資料但對不上商品，這個平台就等於沒接。"""
    try:
        import crawl4ai  # noqa: F401
    except ImportError:
        print("      SKIP  未安裝 crawl4ai")
        return

    from collector.normalize import fingerprint
    from collector.sources.browser import extract_rows

    html = FIXTURE.read_text(encoding="utf-8")
    offers = rows_to_offers(extract_rows(html, MOMO, ""), MOMO,
                            "https://www.momoshop.com.tw/")
    got = {o.title: fingerprint(o.title)[0] for o in offers}

    for momo_title, pchome_title in [
        ("【SONY 索尼】WH-1000XM5 無線藍牙降噪耳機 黑色",
         "Sony WH-1000XM5 無線降噪耳機-黑色"),
        ("【Logitech 羅技】MX Master 3S 無線靜音滑鼠 石墨灰",
         "Logitech MX Master 3S 靜音無線滑鼠-石墨灰"),
    ]:
        assert got[momo_title] == fingerprint(pchome_title)[0], \
            f"跨平台指紋對不上：{momo_title}"


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} 通過")
    raise SystemExit(1 if failed else 0)
