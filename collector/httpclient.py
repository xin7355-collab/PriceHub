"""受控 HTTP 客戶端

三道防線：
  1. Semaphore  —— 嚴禁瞬間併發風暴
  2. 指數退避 + jitter —— 被限流時退讓，且不讓整批請求同步重試
  3. 硬性禮貌延遲 —— 就算沒被擋也主動放慢

只依賴標準庫 + aiohttp，不引入 Playwright。GitHub Actions 的出口 IP
是資料中心位址，用瀏覽器渲染硬闖風控站台只會提高被封機率而非降低。
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import aiohttp

from . import config

log = logging.getLogger("priceport.http")


class RateLimitedClient:
    def __init__(self, concurrency: int | None = None):
        self._sem = asyncio.Semaphore(concurrency or config.MAX_CONCURRENCY)
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "RateLimitedClient":
        timeout = aiohttp.ClientTimeout(total=config.HTTP_TIMEOUT)
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            headers={
                "User-Agent": config.USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            },
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._session:
            await self._session.close()

    async def _get(self, url: str, params: dict | None, as_json: bool) -> Any:
        """三道防線共用的取數路徑。JSON 與 HTML 只差最後怎麼讀 body ——
        限流、退避、禮貌延遲的邏輯不該複製兩份，那種重複遲早會走樣。"""
        assert self._session is not None, "client 必須在 async with 內使用"
        last_err: Exception | None = None

        async with self._sem:
            for attempt in range(config.MAX_RETRIES):
                try:
                    async with self._session.get(url, params=params) as resp:
                        # 429 / 5xx 才退避重試；4xx 其他狀況重試沒意義
                        if resp.status == 429 or resp.status >= 500:
                            raise aiohttp.ClientResponseError(
                                resp.request_info, resp.history,
                                status=resp.status, message="retryable",
                            )
                        resp.raise_for_status()
                        # PChome 回傳 Content-Type 是 text/html，不能信 content_type
                        data = (await resp.json(content_type=None) if as_json
                                else await resp.text())
                        await asyncio.sleep(config.POLITE_DELAY)
                        return data

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    last_err = e
                    if attempt == config.MAX_RETRIES - 1:
                        break
                    delay = config.BACKOFF_BASE * (2 ** attempt)
                    delay += random.uniform(0, config.BACKOFF_JITTER)
                    log.warning(
                        "請求失敗 (%s/%s) %s — %s，%.1fs 後重試",
                        attempt + 1, config.MAX_RETRIES, url, e, delay,
                    )
                    await asyncio.sleep(delay)

        raise RuntimeError(f"重試耗盡: {url}") from last_err

    async def get_json(self, url: str, params: dict | None = None) -> Any:
        """取回 JSON。全部重試用盡才拋出，讓上層決定要不要算失敗。"""
        return await self._get(url, params, as_json=True)

    async def get_text(self, url: str, params: dict | None = None) -> str:
        """取回原始文字。給 momo 這種要從 HTML 抽內嵌 JSON 的平台用。"""
        return await self._get(url, params, as_json=False)
