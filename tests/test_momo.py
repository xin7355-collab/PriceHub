"""momo 解析層的離線測試 —— 不需網路。

fixture 的結構直接照 tools/probe_platforms.py --deep momo 實測到的形狀寫，
不是猜的。解析層壞掉時不會拋錯，只會回 0 筆，所以每一條規則都要有測試釘住。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector.normalize import fingerprint          # noqa: E402
from collector.sources.momo import (                 # noqa: E402
    _clean_url, _price_of, parse_products,
)

FIXTURE = (Path(__file__).resolve().parent / "fixtures" / "momo_search.html")
HTML = FIXTURE.read_text(encoding="utf-8")


def test_extracts_products_from_embedded_json():
    offers = parse_products(HTML)
    # 4 個 Product：1 個缺價格、1 個重複 → 應剩 2
    assert len(offers) == 2, [o.title for o in offers]
    assert all(o.platform == "momo" and o.valid() for o in offers)


def test_price_and_image_and_url():
    o = parse_products(HTML)[0]
    assert o.price == 9900
    assert o.image.startswith("https://img3.momoshop.com.tw/")
    assert o.raw_id == "10201974"


def test_offers_array_takes_lowest():
    """offers 可能是陣列（多賣家）。取最低價，且要吃得下 "3,290" 這種寫法。"""
    o = next(x for x in parse_products(HTML) if "MX Master" in x.title)
    assert o.price == 3190
    assert _price_of({"offers": [{"price": "1,500"}, {"price": 900}]}) == 900
    assert _price_of({"offers": {"price": 0}}) is None
    assert _price_of({"offers": {}}) is None
    assert _price_of({}) is None


def test_tracking_params_are_stripped():
    """搜尋來源參數會讓同一件商品每次產生不同網址，catalog 就會天天在變動，
    明明價格沒動也照樣 commit。"""
    o = parse_products(HTML)[0]
    assert o.url == "https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code=10201974"
    assert "Area=" not in o.url and "kw=" not in o.url
    assert _clean_url("https://x/y?i_code=42&a=b") \
        == "https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code=42"
    # 認不出來的網址原樣保留，不要硬湊一個假的出來
    assert _clean_url("https://other/thing") == "https://other/thing"


def test_duplicate_i_code_is_dropped():
    offers = parse_products(HTML)
    assert len({o.raw_id for o in offers}) == len(offers)


def test_missing_price_is_dropped():
    assert not any("缺價格" in o.title for o in parse_products(HTML))


def test_garbage_never_raises():
    """壞掉的輸入只能回空清單，不能把整批採集拖垮。"""
    for bad in ["", "<html></html>", None,
                '<script type="application/ld+json">{壞掉</script>',
                '<script type="application/ld+json">null</script>',
                '<script type="application/ld+json">[1,2,3]</script>']:
        assert parse_products(bad) == []


def test_survives_unexpected_nesting():
    """刻意不寫死 @graph → ItemList 這條路徑：包裝方式改了還要能運作。"""
    html = ('<script type="application/ld+json">'
            '{"a":{"b":[{"c":{"@type":"Product","name":"深處的商品",'
            '"url":"https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code=1",'
            '"offers":{"price":100}}}]}}</script>')
    offers = parse_products(html)
    assert len(offers) == 1 and offers[0].price == 100


def test_cross_platform_fingerprint_matches_when_titles_align():
    """兩邊寫法一致時要合併成同一件商品。抽得到資料但對不上，
    這個通路就等於沒接。"""
    logi = next(o for o in parse_products(HTML) if "MX Master" in o.title)
    assert fingerprint(logi.title)[0] == \
        fingerprint("Logitech MX Master 3S 靜音無線滑鼠-石墨灰")[0], "羅技對不上"


def test_pchome_titles_without_brand_still_merge():
    """這一組原本是「已知缺口」—— 接上 momo 才暴露出來的：

    momo    【SONY 索尼】WH-1000XM5 …          → brand=sony  color=None
    PChome  WH-1000XM5 黑色 主動式降噪旗艦 …    → brand=None  color=黑色

    PChome 把品牌放在獨立欄位而不是標題裡，所以同一件商品在品牌與顏色
    兩處都對不上，兩個平台各自成為一張卡片。

    指紋規則改成「型號夠獨特就不再要求品牌一致，顏色不列入身分」之後，
    這一組終於合併了。缺口補上，測試也跟著反過來。
    """
    momo_t = "【SONY 索尼】WH-1000XM5 主動式降噪旗艦藍芽耳機(公司貨 保固12+6個月)"
    pchome_t = "WH-1000XM5 黑色 主動式降噪旗艦 藍牙耳機(頂級降噪 極真音質 配戴舒適)"
    assert fingerprint(pchome_t)[1]["brand"] is None, "PChome 標題確實沒有品牌字樣"
    assert fingerprint(momo_t)[0] == fingerprint(pchome_t)[0], "應該要合併了"


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
