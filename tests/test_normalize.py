"""離線單元測試 —— 不需網路。

跨平台商品比對是本專案最容易默默壞掉的一層，
每遇到一個對不上的真實案例，就在這裡補一組測資。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector.normalize import (  # noqa: E402
    clean_title, extract_brand, extract_model, fingerprint, similarity,
)
from collector.sources.pchome import parse_item  # noqa: E402


def test_clean_strips_promo():
    raw = "【現貨免運】Logitech 羅技 MX Master 3S 無線滑鼠 石墨灰 台灣公司貨"
    c = clean_title(raw)
    assert "現貨" not in c and "免運" not in c and "公司貨" not in c
    assert "MX" in c and "Master" in c


def test_brand_alias():
    assert extract_brand("羅技 MX Master 3S") == "logitech"
    assert extract_brand("Logitech MX Master 3S") == "logitech"
    assert extract_brand("國際牌 吹風機") == "panasonic"


def test_model_prefers_alphanumeric():
    assert extract_model("Logitech MX Master 3S 靜音滑鼠 2024") == "3s"
    assert extract_model("Samsung Galaxy S24 Ultra 512GB") in ("s24", "512gb")


def test_cross_platform_match():
    """核心驗收：兩個平台的不同寫法必須產生同一個指紋。"""
    shopee = "【現貨免運】Logitech 羅技 MX Master 3S 無線滑鼠 石墨灰 台灣公司貨"
    pchome = "Logitech MX Master 3S 靜音無線滑鼠-石墨灰"
    fa, ma = fingerprint(shopee)
    fb, mb = fingerprint(pchome)
    assert fa == fb, f"指紋不一致 {ma} vs {mb}"
    assert ma["level"] == "strong"


def test_different_color_is_different_sku():
    a, _ = fingerprint("Logitech MX Master 3S 無線滑鼠 石墨灰")
    b, _ = fingerprint("Logitech MX Master 3S 無線滑鼠 白色")
    assert a != b


def test_brand_inside_brackets():
    """回歸：momo 的【SONY 索尼】格式會被雜訊清理整段剝除。
    品牌必須從原始標題抽取，否則同商品在各平台指紋不一致。"""
    a, _ = fingerprint("Sony WH-1000XM5 無線降噪耳機-黑色")
    b, mb = fingerprint("【SONY 索尼】WH-1000XM5 無線藍牙降噪耳機 黑色")
    assert a == b
    assert mb["brand"] == "sony"


def test_model_with_trailing_digit():
    """回歸：DDR5、XM5 這類字母結尾接數字的型號原本抓不到。"""
    assert extract_model("Kingston FURY Beast DDR5 32GB 5600 桌上型記憶體") == "ddr5"
    a, _ = fingerprint("Kingston FURY Beast DDR5 32GB 5600 桌上型記憶體")
    b, _ = fingerprint("【Kingston 金士頓】FURY Beast DDR5 5600 32GB 記憶體")
    assert a == b


def test_no_model_falls_back_to_brand_tokens():
    """回歸：AirPods Pro 2 沒有可辨識型號，需靠品牌 + 英數詞合併。"""
    fps = {
        fingerprint("Apple AirPods Pro 2 USB-C 主動降噪耳機")[0],
        fingerprint("【Apple 蘋果】AirPods Pro 2 USB-C 版本")[0],
        fingerprint("Apple AirPods Pro 2 (USB-C) 原廠公司貨 現貨")[0],
    }
    assert len(fps) == 1, "三個平台的 AirPods 應合併為同一指紋"


def test_similarity_range():
    s = similarity("Logitech MX Master 3S 滑鼠", "羅技 MX Master 3S 無線滑鼠")
    assert 0.0 < s <= 1.0


def test_pchome_parse_tolerates_missing_fields():
    assert parse_item({}) is None
    assert parse_item({"Id": "X", "name": "測試"}) is None          # 缺價格
    assert parse_item({"Id": "X", "price": 100}) is None            # 缺名稱
    o = parse_item({"Id": "DYAJ2S-A900", "name": "測試商品",
                    "price": 3290, "picB": "/items/x/000001.jpg"})
    assert o is not None and o.price == 3290 and o.valid()
    assert o.url.endswith("DYAJ2S-A900")
    assert o.image.startswith("https://")


def test_pchome_parse_string_price():
    o = parse_item({"Id": "A", "name": "N", "price": "1,299"})
    assert o and o.price == 1299


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
