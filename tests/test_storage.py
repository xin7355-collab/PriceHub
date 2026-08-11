"""資料層的離線測試 —— 不需網路。

重點在冪等。Actions 的 cron 會延遲、會補跑、可以手動重跑舊的 job，
所以 append_point 必須假設自己隨時被重放，而且不保證按時間順序。
時序一旦被寫歪，前端的走勢曲線就永久歪掉，而且不會有任何錯誤訊息。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _fresh_storage(tmp: Path):
    """把資料目錄指到暫存區後重新載入模組，避免污染真正的 data/。"""
    import importlib

    from collector import config
    config.DATA_DIR = tmp
    config.CATALOG_DIR = tmp / "catalog"
    config.SERIES_DIR = tmp / "series"
    config.INDEX_FILE = tmp / "index.json"

    from collector import storage
    importlib.reload(storage)
    return storage


def _points(storage, fp="abc123456789", platform="pchome"):
    return storage.read_series(fp)["series"].get(platform, [])


def test_same_day_rerun_overwrites():
    with tempfile.TemporaryDirectory() as td:
        st = _fresh_storage(Path(td))
        st.append_point("abc123456789", "pchome", 1000, 2400)
        st.append_point("abc123456789", "pchome", 950, 2400)      # 同日重跑
        assert _points(st) == [[2400, 950]]


def test_out_of_order_replay_does_not_duplicate():
    """回歸：補跑一個較舊的日子時，原本只比對最後一點，
    導致同一天被寫成兩個點。"""
    with tempfile.TemporaryDirectory() as td:
        st = _fresh_storage(Path(td))
        for day, price in [(2400, 1000), (2401, 1010), (2402, 1020)]:
            st.append_point("abc123456789", "pchome", price, day)
        st.append_point("abc123456789", "pchome", 999, 2401)      # 補跑中間那天

        pts = _points(st)
        assert len(pts) == 3, f"不該多出點：{pts}"
        assert pts == [[2400, 1000], [2401, 999], [2402, 1020]]


def test_backfill_older_day_inserts_in_order():
    """補一個比整段序列都早的日子，必須插在正確位置而不是接在尾巴。"""
    with tempfile.TemporaryDirectory() as td:
        st = _fresh_storage(Path(td))
        st.append_point("abc123456789", "pchome", 1000, 2405)
        st.append_point("abc123456789", "pchome", 900, 2400)

        pts = _points(st)
        assert pts == [[2400, 900], [2405, 1000]], pts
        assert [p[0] for p in pts] == sorted(p[0] for p in pts), "時序必須遞增"


def test_whole_series_replay_is_idempotent():
    """整段歷史重放一次（seed_demo 重跑就是這個情況），結果必須完全相同。"""
    with tempfile.TemporaryDirectory() as td:
        st = _fresh_storage(Path(td))
        history = [(2400 + i, 1000 + i * 7) for i in range(20)]
        for day, price in history:
            st.append_point("abc123456789", "pchome", price, day)
        first = json.dumps(_points(st))

        for day, price in history:                                 # 再放一次
            st.append_point("abc123456789", "pchome", price, day)
        assert json.dumps(_points(st)) == first


def test_replay_with_flat_prices_is_idempotent():
    """回歸：價格沒動的那幾天在順序寫入時被門檻壓掉，沒有存進序列。
    重放時若不套用同一條規則，就會把它們加回來 —— 同一批資料跑兩次
    結果不同，那就不叫冪等。"""
    with tempfile.TemporaryDirectory() as td:
        st = _fresh_storage(Path(td))
        # 2401、2403 與前一天同價，順序寫入時會被壓掉
        history = [(2400, 1000), (2401, 1000), (2402, 1200),
                   (2403, 1200), (2404, 1500)]
        for day, price in history:
            st.append_point("abc123456789", "pchome", price, day)
        first = json.dumps(_points(st))
        assert _points(st) == [[2400, 1000], [2402, 1200], [2404, 1500]], _points(st)

        for day, price in history:                                 # 再放一次
            st.append_point("abc123456789", "pchome", price, day)
        assert json.dumps(_points(st)) == first, f"重放後變了：{_points(st)}"


def test_unchanged_price_does_not_add_point():
    with tempfile.TemporaryDirectory() as td:
        st = _fresh_storage(Path(td))
        st.append_point("abc123456789", "pchome", 1000, 2400)
        st.append_point("abc123456789", "pchome", 1000, 2401)      # 價格沒動
        assert _points(st) == [[2400, 1000]]


def test_platforms_are_independent():
    """一個平台的補跑不該碰到另一個平台的時序。"""
    with tempfile.TemporaryDirectory() as td:
        st = _fresh_storage(Path(td))
        st.append_point("abc123456789", "pchome", 1000, 2400)
        st.append_point("abc123456789", "momo", 900, 2400)
        st.append_point("abc123456789", "pchome", 1100, 2401)
        assert _points(st, platform="pchome") == [[2400, 1000], [2401, 1100]]
        assert _points(st, platform="momo") == [[2400, 900]]


def test_atomic_write_leaves_no_temp_files():
    """job 被砍不該留下半個 JSON，也不該留下 .tmp 殘骸。"""
    with tempfile.TemporaryDirectory() as td:
        st = _fresh_storage(Path(td))
        st.append_point("abc123456789", "pchome", 1000, 2400)
        leftovers = list(Path(td).rglob("*.tmp"))
        assert not leftovers, leftovers


def test_corrupt_series_file_does_not_kill_the_run():
    """壞檔當成空的重建，不該讓整批採集陣亡。"""
    with tempfile.TemporaryDirectory() as td:
        st = _fresh_storage(Path(td))
        path = Path(td) / "series" / "ab" / "abc123456789.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{壞掉的 JSON", encoding="utf-8")
        st.append_point("abc123456789", "pchome", 1000, 2400)
        assert _points(st) == [[2400, 1000]]


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
