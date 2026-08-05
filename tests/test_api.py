from __future__ import annotations

from io import BytesIO

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from app import create_app
from config.settings import load_settings
from sdk.dji_thermal_sdk import SDKError
from service.thermal_service import ThermalService


class FakeDownloader:
    async def download(self, _file_url: str) -> bytes:
        output = BytesIO()
        Image.new("RGB", (2, 2)).save(output, "JPEG")
        return output.getvalue()

    async def close(self) -> None:
        pass


class FakeSDK:
    def create_from_rjpeg(self, _data: bytes) -> object:
        return object()

    def measure(self, _handle: object) -> np.ndarray:
        return np.array([[20.0, 40.0], [10.0, 30.0]], dtype=np.float32)

    def destroy(self, _handle: object) -> None:
        pass


class InvalidRJPEGFakeSDK(FakeSDK):
    def create_from_rjpeg(self, _data: bytes) -> object:
        raise SDKError("dirp_create_from_rjpeg", -7)


def test_analyze_and_point_contracts() -> None:
    settings = load_settings()
    service = ThermalService(  # type: ignore[arg-type]
        FakeSDK(), FakeDownloader(), settings.cache
    )
    with TestClient(create_app(settings, service)) as client:
        analyze = client.post(
            "/api/thermal/analyze",
            json={"fileUrl": "http://objects.example/image_R.JPG"},
        )
        point = client.post(
            "/api/thermal/point",
            json={
                "fileUrl": "http://objects.example/image_R.JPG",
                "x": 1,
                "y": 0,
            },
        )

    assert analyze.status_code == 200
    assert analyze.json() == {
        "success": True,
        "fileUrl": "http://objects.example/image_R.JPG",
        "width": 2,
        "height": 2,
        "maxTemperature": 40.0,
        "minTemperature": 10.0,
        "averageTemperature": 25.0,
        "maxPoint": {"x": 1, "y": 0},
        "minPoint": {"x": 0, "y": 1},
    }
    assert point.status_code == 200
    assert point.json() == {"x": 1, "y": 0, "temperature": 40.0}


def test_point_out_of_bounds_returns_structured_error() -> None:
    settings = load_settings()
    service = ThermalService(  # type: ignore[arg-type]
        FakeSDK(), FakeDownloader(), settings.cache
    )
    with TestClient(create_app(settings, service)) as client:
        response = client.post(
            "/api/thermal/point",
            json={"fileUrl": "https://objects.example/image_R.JPG", "x": 2, "y": 0},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "COORDINATE_OUT_OF_BOUNDS"


def test_sdk_rejection_returns_invalid_rjpeg_error() -> None:
    settings = load_settings()
    service = ThermalService(  # type: ignore[arg-type]
        InvalidRJPEGFakeSDK(), FakeDownloader(), settings.cache
    )
    with TestClient(create_app(settings, service)) as client:
        response = client.post(
            "/api/thermal/analyze",
            json={"fileUrl": "https://objects.example/ordinary.jpg"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_DJI_RJPEG"
