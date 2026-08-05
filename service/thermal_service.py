from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
from typing import Awaitable, Callable, Protocol

import numpy as np
import numpy.typing as npt
from PIL import Image, UnidentifiedImageError

from config.settings import CacheSettings
from sdk.dji_thermal_sdk import DJIThermalSDKWrapper, SDKError
from service.http_downloader import HTTPDownloader

logger = logging.getLogger(__name__)


class InvalidRJPEGError(RuntimeError):
    """Input is not a valid JPEG or is rejected by the DJI SDK."""


class CoordinateOutOfBoundsError(ValueError):
    pass


class Downloader(Protocol):
    async def download(self, file_url: str) -> bytes: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ThermalAnalysis:
    file_url: str
    width: int
    height: int
    max_temperature: float
    min_temperature: float
    average_temperature: float
    max_x: int
    max_y: int
    min_x: int
    min_y: int
    temperatures: npt.NDArray[np.float32]


@dataclass(slots=True)
class _CacheEntry:
    value: ThermalAnalysis
    expires_at: float


class ThermalAnalysisCache:
    """In-process TTL/LRU cache with per-URL single-flight parsing."""

    def __init__(self, max_entries: int, ttl_seconds: float) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[ThermalAnalysis]] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(
        self,
        key: str,
        factory: Callable[[], Awaitable[ThermalAnalysis]],
    ) -> tuple[ThermalAnalysis, bool]:
        async with self._lock:
            now = time.monotonic()
            entry = self._entries.get(key)
            if entry is not None and entry.expires_at > now:
                self._entries.move_to_end(key)
                return entry.value, True
            if entry is not None:
                del self._entries[key]

            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(self._produce(key, factory))
                self._inflight[key] = task

        return await asyncio.shield(task), False

    async def _produce(
        self,
        key: str,
        factory: Callable[[], Awaitable[ThermalAnalysis]],
    ) -> ThermalAnalysis:
        try:
            value = await factory()
            async with self._lock:
                self._entries[key] = _CacheEntry(
                    value=value,
                    expires_at=time.monotonic() + self._ttl_seconds,
                )
                self._entries.move_to_end(key)
                while len(self._entries) > self._max_entries:
                    self._entries.popitem(last=False)
            return value
        finally:
            async with self._lock:
                current = asyncio.current_task()
                if self._inflight.get(key) is current:
                    del self._inflight[key]


class ThermalService:
    def __init__(
        self,
        sdk: DJIThermalSDKWrapper,
        downloader: Downloader,
        cache_settings: CacheSettings,
        max_concurrent_sdk_calls: int = 4,
    ) -> None:
        self._sdk = sdk
        self._downloader = downloader
        self._cache = ThermalAnalysisCache(
            cache_settings.max_entries, cache_settings.ttl_seconds
        )
        self._sdk_semaphore = asyncio.Semaphore(max_concurrent_sdk_calls)

    async def analyze(self, file_url: str) -> ThermalAnalysis:
        analysis, cache_hit = await self._cache.get_or_create(
            file_url, lambda: self._download_and_parse(file_url)
        )
        if cache_hit:
            logger.info("thermal cache hit fileUrl=%s", file_url)
        return analysis

    async def point_temperature(self, file_url: str, x: int, y: int) -> float:
        analysis = await self.analyze(file_url)
        if x < 0 or y < 0 or x >= analysis.width or y >= analysis.height:
            raise CoordinateOutOfBoundsError(
                f"point ({x}, {y}) is outside image bounds "
                f"width={analysis.width}, height={analysis.height}"
            )
        return round(float(analysis.temperatures[y, x]), 1)

    async def _download_and_parse(self, file_url: str) -> ThermalAnalysis:
        download_started = time.perf_counter()
        data = await self._downloader.download(file_url)
        download_ms = (time.perf_counter() - download_started) * 1000
        logger.info(
            "thermal download fileUrl=%s durationMs=%.2f sizeBytes=%d",
            file_url,
            download_ms,
            len(data),
        )
        self._validate_jpeg(data)

        sdk_started = time.perf_counter()
        async with self._sdk_semaphore:
            try:
                temperatures = await asyncio.to_thread(self._measure_sync, data)
            except SDKError as exc:
                if exc.function == "dirp_create_from_rjpeg":
                    raise InvalidRJPEGError(
                        f"file is not a DJI radiometric JPEG: {exc}"
                    ) from exc
                raise
        sdk_ms = (time.perf_counter() - sdk_started) * 1000

        height, width = temperatures.shape
        max_flat_index = int(np.argmax(temperatures))
        min_flat_index = int(np.argmin(temperatures))
        max_y, max_x = np.unravel_index(max_flat_index, temperatures.shape)
        min_y, min_x = np.unravel_index(min_flat_index, temperatures.shape)

        analysis = ThermalAnalysis(
            file_url=file_url,
            width=width,
            height=height,
            max_temperature=round(float(np.max(temperatures)), 1),
            min_temperature=round(float(np.min(temperatures)), 1),
            average_temperature=round(float(np.mean(temperatures)), 1),
            max_x=int(max_x),
            max_y=int(max_y),
            min_x=int(min_x),
            min_y=int(min_y),
            temperatures=temperatures,
        )
        logger.info(
            "thermal parsed fileUrl=%s sdkDurationMs=%.2f width=%d height=%d "
            "minTemperature=%.1f maxTemperature=%.1f averageTemperature=%.1f",
            file_url,
            sdk_ms,
            width,
            height,
            analysis.min_temperature,
            analysis.max_temperature,
            analysis.average_temperature,
        )
        return analysis

    def _measure_sync(self, data: bytes) -> npt.NDArray[np.float32]:
        handle = self._sdk.create_from_rjpeg(data)
        try:
            return self._sdk.measure(handle)
        finally:
            self._sdk.destroy(handle)

    @staticmethod
    def _validate_jpeg(data: bytes) -> None:
        if len(data) < 4 or not data.startswith(b"\xff\xd8"):
            raise InvalidRJPEGError("downloaded file is not a JPEG image")
        try:
            with Image.open(BytesIO(data)) as image:
                if image.format != "JPEG":
                    raise InvalidRJPEGError("downloaded file is not a JPEG image")
                image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise InvalidRJPEGError("downloaded JPEG is corrupt or incomplete") from exc

    async def close(self) -> None:
        result = self._downloader.close()
        if inspect.isawaitable(result):
            await result
