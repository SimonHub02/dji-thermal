from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import httpx

from config.settings import DownloadSettings


class DownloadError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502) -> None:
        self.status_code = status_code
        super().__init__(message)


class FileTooLargeError(DownloadError):
    def __init__(self, maximum_bytes: int) -> None:
        super().__init__(
            f"remote file exceeds the {maximum_bytes} byte size limit",
            status_code=413,
        )


class HTTPDownloader:
    def __init__(self, settings: DownloadSettings) -> None:
        self._settings = settings
        timeout = httpx.Timeout(
            connect=settings.connect_timeout_seconds,
            read=settings.read_timeout_seconds,
            write=settings.read_timeout_seconds,
            pool=settings.connect_timeout_seconds,
        )
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=settings.follow_redirects,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )

    async def download(self, file_url: str) -> bytes:
        parsed = urlsplit(file_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise DownloadError("fileUrl must be an absolute HTTP(S) URL", 400)

        last_error: Exception | None = None
        attempts = self._settings.retries + 1
        for attempt in range(attempts):
            try:
                return await self._download_once(file_url)
            except FileTooLargeError:
                raise
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code not in {408, 425, 429} and not (
                    500 <= exc.response.status_code <= 599
                ):
                    raise DownloadError(
                        f"remote server returned HTTP {exc.response.status_code}"
                    ) from exc
            except httpx.RequestError as exc:
                last_error = exc

            if attempt + 1 < attempts:
                await asyncio.sleep(
                    self._settings.retry_backoff_seconds * (2**attempt)
                )

        raise DownloadError(
            f"failed to download file after {attempts} attempt(s): {last_error}"
        ) from last_error

    async def _download_once(self, file_url: str) -> bytes:
        maximum = self._settings.max_file_size_bytes
        chunks: list[bytes] = []
        total = 0
        async with self._client.stream("GET", file_url) as response:
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > maximum:
                        raise FileTooLargeError(maximum)
                except ValueError:
                    pass

            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > maximum:
                    raise FileTooLargeError(maximum)
                chunks.append(chunk)
        return b"".join(chunks)

    async def close(self) -> None:
        await self._client.aclose()
