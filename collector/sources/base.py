"""平台介接介面

每新增一個平台，只要實作 Source 子類別並註冊到 REGISTRY。
run.py 與 workflow 完全不需要修改 —— 這是能安全長出六個平台的前提。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..httpclient import RateLimitedClient


@dataclass
class Offer:
    """單一平台的一筆報價。所有 source 都必須產出這個形狀。"""
    platform: str
    title: str
    price: int
    url: str
    image: str | None = None
    raw_id: str | None = None
    extra: dict = field(default_factory=dict)

    def valid(self) -> bool:
        return bool(self.title) and isinstance(self.price, int) and self.price > 0 and bool(self.url)


class Source(Protocol):
    name: str
    label: str

    async def search(self, client: "RateLimitedClient", keyword: str,
                     limit: int) -> list[Offer]:
        ...


REGISTRY: dict[str, Source] = {}


def register(src: Source) -> Source:
    REGISTRY[src.name] = src
    return src


def to_int_price(v) -> int | None:
    """把各平台五花八門的價格寫法壓成整數，看不懂就回 None。

    JSON API 給數字，HTML 給的是 "$3,290" / "3,290元" / "NT$3,290"。
    解析不出來一律當成沒有這筆，不要猜 —— 猜錯的價格會直接寫進歷史曲線。
    """
    if isinstance(v, bool):            # bool 是 int 的子類，必須先擋掉
        return None
    if isinstance(v, (int, float)):
        return int(v) if v > 0 else None
    if isinstance(v, str):
        # 只留數字，避免 "特價$3,290起" 這種夾雜文字的寫法整筆丟掉
        m = re.search(r"\d[\d,]*", v.replace(",", ""))
        if not m:
            return None
        try:
            n = int(m.group(0))
        except ValueError:
            return None
        return n if n > 0 else None
    return None
