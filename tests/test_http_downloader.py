from __future__ import annotations

import httpx
import pytest

from config.settings import DownloadSettings
from service.http_downloader import FileTooLargeError, HTTPDownloader


@pytest.mark.asyncio
async def test_retries_transient_response() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, content=b"jpeg")

    downloader = HTTPDownloader(
        DownloadSettings(retries=1, retry_backoff_seconds=0, max_file_size_bytes=10)
    )
    await downloader._client.aclose()
    downloader._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        assert await downloader.download("http://objects.example/a.jpg") == b"jpeg"
        assert calls == 2
    finally:
        await downloader.close()


@pytest.mark.asyncio
async def test_streaming_size_limit() -> None:
    downloader = HTTPDownloader(
        DownloadSettings(retries=0, max_file_size_bytes=3)
    )
    await downloader._client.aclose()
    downloader._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"1234"))
    )
    try:
        with pytest.raises(FileTooLargeError):
            await downloader.download("https://objects.example/a.jpg")
    finally:
        await downloader.close()
