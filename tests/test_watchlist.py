"""採集清單與排除詞的離線測試。

排除詞這一層直接決定使用者搜到什麼。它壞掉不會有任何錯誤訊息，
只會讓「最低價」被一堆便宜配件霸佔，看起來還很正常。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector import config                      # noqa: E402
from collector.run import Query, is_excluded, load_queries   # noqa: E402

WATCHLIST = json.loads(config.WATCHLIST_FILE.read_text(encoding="utf-8"))


def test_watchlist_is_wellformed():
    items = WATCHLIST["items"]
    assert len(items) >= 20, f"清單太短，搜尋會常常沒東西：{len(items)}"
    for w in items:
        assert w.get("keyword", "").strip(), w
        assert w.get("tag"), w
    kws = [w["keyword"] for w in items]
    assert len(kws) == len(set(kws)), "有重複的關鍵字，會白跑一次請求"


def test_common_excludes_are_applied_by_default():
    qs = {q.keyword: q for q in load_queries(None)}
    airpods = next(q for k, q in qs.items() if "AirPods Pro 2" in k)
    assert "保護殼" in airpods.exclude


def test_accessory_titles_are_excluded():
    """這些都是真的從 PChome 採回來的標題。"""
    ex = tuple(WATCHLIST["exclude_common"])
    for title in [
        "AirPods Pro (第 2 代) 保護殼套 性感豹紋",
        "AirPods 3 AirPods Pro 1 2 EURO 職匠工藝 保護套(駝)",
        "AirPods Pro 2 1 ODYSSEY 抗衝擊磁扣手繩耳機殼(隨附15MM手繩)",
    ]:
        assert is_excluded(title, ex), f"應該被排除但沒有：{title}"


def test_real_products_survive():
    """排除詞不能誤殺真正的商品 —— 這比漏掉配件嚴重得多。"""
    ex = tuple(WATCHLIST["exclude_common"])
    for title in [
        "Apple AirPods Pro 2 USB-C 主動降噪耳機",
        "Sony WH-1000XM5 無線降噪耳機-黑色",
        "Logitech MX Master 3S 靜音無線滑鼠-石墨灰",
        "Kingston FURY Beast DDR5 32GB 5600 桌上型記憶體",
        "Dyson Supersonic HD15 吹風機",
        "MacBook Air 13吋 M4 16G/256G",
    ]:
        assert not is_excluded(title, ex), f"被誤殺了：{title}"


def test_empty_exclude_list_opts_out():
    """明確寫 "exclude": [] 代表這一筆不套用共用清單。"""
    assert is_excluded("保護殼", ()) is False
    assert is_excluded("任何標題", ()) is False


def test_exclude_is_case_insensitive():
    assert is_excluded("Silicone CASE for AirPods", ("case",))
    assert is_excluded("silicone case", ("CASE",))


def test_explicit_keywords_override_watchlist():
    qs = load_queries("AAA,BBB")
    assert [q.keyword for q in qs] == ["AAA", "BBB"]
    assert all(q.exclude == () for q in qs), "臨時關鍵字不該套用排除詞"


def test_query_defaults():
    q = Query("x")
    assert q.exclude == () and is_excluded("保護殼", q.exclude) is False


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
