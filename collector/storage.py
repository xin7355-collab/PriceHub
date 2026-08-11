"""資料層：分片 catalog + append-only 時序

設計約束來自 Git 而非資料庫：
  - 每次 commit 只能動到少數檔案，否則 repo 體積失控
  - 時序只存 [dayIndex, price] 兩個小整數
  - 全部原子寫入（temp + replace），Actions 中途被砍不會留下半個 JSON
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from . import config


# ---------------------------------------------------------------- 基礎 IO
def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _read(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # 壞檔不該讓整批採集陣亡，當成空的重建
        return default


def shard_of(fp: str) -> str:
    return fp[: config.SHARD_HEX_LEN]


# ---------------------------------------------------------------- Catalog
class Catalog:
    """商品主檔。以指紋為鍵，記錄各平台的最新報價。"""

    def __init__(self) -> None:
        self._shards: dict[str, dict[str, dict]] = {}
        self._dirty: set[str] = set()

    def _load_shard(self, sh: str) -> dict[str, dict]:
        if sh not in self._shards:
            self._shards[sh] = _read(config.CATALOG_DIR / f"{sh}.json", {})
        return self._shards[sh]

    def upsert(self, fp: str, meta: dict, offer: dict) -> None:
        """寫入一筆平台報價。

        offer: {platform, title, price, url, image, day}
        """
        sh = shard_of(fp)
        shard = self._load_shard(sh)
        entry = shard.setdefault(fp, {
            "fp": fp,
            "title": meta.get("clean") or offer["title"],
            "brand": meta.get("brand"),
            "model": meta.get("model"),
            "color": meta.get("color"),
            "level": meta.get("level"),
            "offers": {},
        })
        # 標題取最短的乾淨版本，通常雜訊最少
        cand = meta.get("clean") or offer["title"]
        if cand and len(cand) < len(entry["title"]):
            entry["title"] = cand

        entry["offers"][offer["platform"]] = {
            "price": offer["price"],
            "url": offer["url"],
            "image": offer.get("image"),
            "title": offer["title"],
            "day": offer["day"],
        }
        self._dirty.add(sh)

    def finalize(self) -> dict:
        """重算每筆的最低價，寫回所有髒分片，回傳統計。"""
        total_products = 0
        for sh in sorted(self._dirty):
            shard = self._shards[sh]
            for entry in shard.values():
                offers = entry.get("offers") or {}
                priced = [
                    (v["price"], k) for k, v in offers.items()
                    if isinstance(v.get("price"), (int, float)) and v["price"] > 0
                ]
                if priced:
                    price, platform = min(priced)
                    entry["best"] = {"platform": platform, "price": price}
                else:
                    entry.pop("best", None)
            _atomic_write(config.CATALOG_DIR / f"{sh}.json", shard)

        # 統計要掃全部分片，不只髒的
        shards = []
        for p in sorted(config.CATALOG_DIR.glob("*.json")):
            data = _read(p, {})
            total_products += len(data)
            shards.append(p.stem)
        return {"shards": shards, "products": total_products}


# ---------------------------------------------------------------- Series
def append_point(fp: str, platform: str, price: int, day: int) -> None:
    """在時序尾端追加一個價格點。

    去重規則：
      - 同一天重複採集 → 覆寫（取最後一次）
      - 與前一點價差小於門檻 → 不寫，避免時序被無意義的點灌爆
    """
    path = config.SERIES_DIR / shard_of(fp) / f"{fp}.json"
    doc = _read(path, {"fp": fp, "series": {}})
    pts: list[list[int]] = doc["series"].setdefault(platform, [])

    if pts and pts[-1][0] == day:
        # 最常見的情況：同一天重跑，覆寫當日價格點
        pts[-1][1] = price
    elif pts and day < pts[-1][0]:
        # 亂序重放：cron 補跑、手動重跑舊 job、或 seed_demo 回填歷史。
        # 這一天可能已經躺在序列中間，只比對最後一點會漏掉它，
        # 於是同一天被寫成兩個點，曲線就從此歪掉。
        for p in pts:
            if p[0] == day:
                p[1] = price
                break
        else:
            # 這一天還不在序列裡。找出它該插在哪，並比照順序寫入時的規則：
            # 與前一個點差距太小就不存 —— 否則重放會把當初被壓掉的點加回來，
            # 同一批資料跑兩次結果不同，就不算冪等了。
            idx = 0
            while idx < len(pts) and pts[idx][0] < day:
                idx += 1
            if idx > 0 and abs(pts[idx - 1][1] - price) < config.SERIES_MIN_DELTA:
                return
            pts.insert(idx, [day, price])
    elif pts and abs(pts[-1][1] - price) < config.SERIES_MIN_DELTA:
        # 價格沒動，不值得為它多存一個點
        return
    else:
        pts.append([day, price])

    if len(pts) > config.SERIES_MAX_POINTS:
        del pts[: len(pts) - config.SERIES_MAX_POINTS]

    _atomic_write(path, doc)


def read_series(fp: str) -> dict:
    return _read(config.SERIES_DIR / shard_of(fp) / f"{fp}.json",
                 {"fp": fp, "series": {}})


# ---------------------------------------------------------------- Manifest
def write_index(stats: dict) -> None:
    _atomic_write(config.INDEX_FILE, stats)
