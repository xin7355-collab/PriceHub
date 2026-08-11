"""平台介接介面

每新增一個平台，只要實作 Source 子類別並註冊到 REGISTRY。
run.py 與 workflow 完全不需要修改 —— 這是能安全長出六個平台的前提。
"""
from __future__ import annotations

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
