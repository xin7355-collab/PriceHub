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
    # "3s" 只有兩碼，不足以單獨指認一款商品，要靠品牌撐 → medium
    assert ma["level"] == "medium"


def test_same_model_different_color_merges():
    """刻意的行為反轉。

    原本顏色是指紋的一部分，不同顏色算不同 SKU。但實測真實標題後發現，
    這條規則讓跨平台比對大量失敗 —— momo 寫「【SONY 索尼】WH-1000XM5 …」
    不帶顏色，PChome 寫「WH-1000XM5 黑色 …」帶顏色，同一件商品因此拆成
    兩張卡片。

    改成顏色不列入身分。顏色仍然存在 meta 裡可供顯示，只是不再切開商品。
    同型號不同顏色的價差通常很小，把它們併在一起顯示最低價，
    比讓使用者看到兩張各自只有一個通路的卡片有用得多。
    """
    a, ma = fingerprint("Logitech MX Master 3S 無線滑鼠 石墨灰")
    b, _ = fingerprint("Logitech MX Master 3S 無線滑鼠 白色")
    assert a == b
    assert ma["color"] == "石墨灰", "顏色不進指紋，但仍要抽出來"


def test_different_capacity_is_different_product():
    """顏色可以合併，容量不行 —— 差別在於它直接影響價格。
    這是 CSE 那份專案的 LLM 驗證規則第 3 條，也符合直覺。"""
    a, _ = fingerprint("Kingston FURY Beast DDR5 32GB 桌上型記憶體")
    b, _ = fingerprint("Kingston FURY Beast DDR5 16GB 桌上型記憶體")
    assert a != b

    c, _ = fingerprint("Samsung 990 PRO 2TB M.2 SSD")
    d, _ = fingerprint("Samsung 990 PRO 4TB M.2 SSD")
    assert c != d


def test_short_model_must_not_merge_across_brands():
    """回歸：DDR5 是規格標準不是型號，只有 4 碼。若讓它單獨成為合併依據，
    Kingston 與 Crucial 的記憶體會被併成同一件商品 —— 而且兩者價格接近，
    價差防線也攔不住。這種靜默的錯誤合併最危險。"""
    a, _ = fingerprint("Kingston FURY Beast DDR5 16GB 桌上型記憶體")
    b, _ = fingerprint("Crucial DDR5 16GB 桌上型記憶體")
    assert a != b


def test_distinctive_model_merges_without_brand():
    """關鍵修正：PChome 常把品牌放在獨立欄位，標題裡沒有品牌字樣。
    型號夠獨特時就不該再要求品牌一致。"""
    momo = "【SONY 索尼】WH-1000XM5 主動式降噪旗艦藍芽耳機(公司貨 保固12+6個月)"
    pchome = "WH-1000XM5 黑色 主動式降噪旗艦 藍牙耳機(頂級降噪 極真音質 配戴舒適)"
    fa, ma = fingerprint(momo)
    fb, mb = fingerprint(pchome)
    assert mb["brand"] is None, "PChome 這個標題確實沒有品牌字樣"
    assert fa == fb, f"應合併：{ma} vs {mb}"


def test_specs_order_does_not_matter():
    """兩個平台把規格寫在標題不同位置，集合相同就該得到相同的鍵。"""
    a, _ = fingerprint("Kingston FURY DDR5 5600 32GB 記憶體")
    b, _ = fingerprint("Kingston FURY 32GB DDR5 5600 記憶體")
    assert a == b


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
