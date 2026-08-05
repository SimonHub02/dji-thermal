from __future__ import annotations

from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from config.settings import CacheSettings
from service.thermal_service import CoordinateOutOfBoundsError, ThermalService


class FakeDownloader:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls = 0
        self.closed = False

    async def download(self, _file_url: str) -> bytes:
        self.calls += 1
        return self.content

    async def close(self) -> None:
        self.closed = True


class FakeHandle:
    pass


class FakeSDK:
    def __init__(self) -> None:
        self.create_calls = 0
        self.destroy_calls = 0

    def create_from_rjpeg(self, _data: bytes) -> FakeHandle:
        self.create_calls += 1
        return FakeHandle()

    def measure(self, _handle: FakeHandle) -> np.ndarray:
        return np.array([[12.3, -5.0, 30.0], [4.0, 10.0, 8.0]], dtype=np.float32)

    def destroy(self, _handle: FakeHandle) -> None:
        self.destroy_calls += 1


def jpeg_bytes(image_format: str = "JPEG") -> bytes:
    output = BytesIO()
    image = Image.new("RGB", (3, 2), color=(0, 0, 0))
    if image_format == "MPO":
        image.save(
            output,
            format="MPO",
            save_all=True,
            append_images=[Image.new("RGB", (3, 2), color=(0, 0, 0))],
        )
    else:
        image.save(output, format=image_format)
    return output.getvalue()


@pytest.mark.asyncio
async def test_analyze_calculates_statistics_and_caches_by_url() -> None:
    downloader = FakeDownloader(jpeg_bytes())
    sdk = FakeSDK()
    service = ThermalService(sdk, downloader, CacheSettings(4, 60))  # type: ignore[arg-type]

    first = await service.analyze("http://objects.example/image_R.JPG")
    second = await service.analyze("http://objects.example/image_R.JPG")

    assert first is second
    assert first.width == 3
    assert first.height == 2
    assert first.max_temperature == 30.0
    assert first.min_temperature == -5.0
    assert first.average_temperature == 9.9
    assert (first.max_x, first.max_y) == (2, 0)
    assert (first.min_x, first.min_y) == (1, 0)
    assert downloader.calls == 1
    assert sdk.create_calls == 1
    assert sdk.destroy_calls == 1


@pytest.mark.asyncio
async def test_point_uses_y_x_matrix_order_and_validates_bounds() -> None:
    service = ThermalService(  # type: ignore[arg-type]
        FakeSDK(), FakeDownloader(jpeg_bytes()), CacheSettings(4, 60)
    )

    assert await service.point_temperature("http://objects.example/a.jpg", 0, 1) == 4.0
    with pytest.raises(CoordinateOutOfBoundsError):
        await service.point_temperature("http://objects.example/a.jpg", 3, 1)


@pytest.mark.asyncio
async def test_close_closes_downloader() -> None:
    downloader = FakeDownloader(jpeg_bytes())
    service = ThermalService(  # type: ignore[arg-type]
        FakeSDK(), downloader, CacheSettings(4, 60)
    )
    await service.close()
    assert downloader.closed is True


@pytest.mark.asyncio
async def test_concurrent_same_url_requests_are_single_flight() -> None:
    import asyncio

    downloader = FakeDownloader(jpeg_bytes())
    sdk = FakeSDK()
    service = ThermalService(sdk, downloader, CacheSettings(4, 60))  # type: ignore[arg-type]

    first, second = await asyncio.gather(
        service.analyze("http://objects.example/same.jpg"),
        service.analyze("http://objects.example/same.jpg"),
    )

    assert first is second
    assert downloader.calls == 1
    assert sdk.create_calls == 1


@pytest.mark.asyncio
async def test_analyze_accepts_mpo_container_used_by_dji_rjpeg() -> None:
    service = ThermalService(  # type: ignore[arg-type]
        FakeSDK(), FakeDownloader(jpeg_bytes("MPO")), CacheSettings(4, 60)
    )

    analysis = await service.analyze("http://objects.example/dji-rjpeg.jpg")

    assert (analysis.width, analysis.height) == (3, 2)
