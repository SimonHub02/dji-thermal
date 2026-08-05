from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.thermal_controller import router as thermal_router
from config.settings import Settings, load_settings
from sdk.dji_thermal_sdk import DJIThermalSDKWrapper, SDKError, SDKLoadError
from service.http_downloader import DownloadError, FileTooLargeError, HTTPDownloader
from service.thermal_service import (
    CoordinateOutOfBoundsError,
    InvalidRJPEGError,
    ThermalService,
)

logger = logging.getLogger(__name__)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "error": {"code": code, "message": message}},
    )


def create_app(
    settings: Settings | None = None,
    thermal_service: ThermalService | None = None,
) -> FastAPI:
    configured_settings = settings or load_settings()
    _configure_logging(configured_settings.app.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        service = thermal_service
        if service is None:
            sdk = DJIThermalSDKWrapper(configured_settings.sdk.library_path)
            downloader = HTTPDownloader(configured_settings.download)
            service = ThermalService(
                sdk=sdk,
                downloader=downloader,
                cache_settings=configured_settings.cache,
                max_concurrent_sdk_calls=configured_settings.sdk.max_concurrent_calls,
            )
        app.state.thermal_service = service
        logger.info("thermal-service started")
        try:
            yield
        finally:
            await service.close()
            logger.info("thermal-service stopped")

    application = FastAPI(
        title="DJI Thermal Service",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.include_router(thermal_router)

    @application.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.exception_handler(FileTooLargeError)
    async def file_too_large_handler(
        _request: Request, exc: FileTooLargeError
    ) -> JSONResponse:
        return _error_response(413, "FILE_TOO_LARGE", str(exc))

    @application.exception_handler(DownloadError)
    async def download_error_handler(
        _request: Request, exc: DownloadError
    ) -> JSONResponse:
        return _error_response(exc.status_code, "DOWNLOAD_FAILED", str(exc))

    @application.exception_handler(InvalidRJPEGError)
    async def invalid_rjpeg_handler(
        _request: Request, exc: InvalidRJPEGError
    ) -> JSONResponse:
        return _error_response(422, "INVALID_DJI_RJPEG", str(exc))

    @application.exception_handler(CoordinateOutOfBoundsError)
    async def coordinate_handler(
        _request: Request, exc: CoordinateOutOfBoundsError
    ) -> JSONResponse:
        return _error_response(422, "COORDINATE_OUT_OF_BOUNDS", str(exc))

    @application.exception_handler(SDKLoadError)
    async def sdk_load_handler(_request: Request, exc: SDKLoadError) -> JSONResponse:
        return _error_response(503, "SDK_UNAVAILABLE", str(exc))

    @application.exception_handler(SDKError)
    async def sdk_error_handler(_request: Request, exc: SDKError) -> JSONResponse:
        return _error_response(500, "SDK_ERROR", str(exc))

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    current_settings = load_settings()
    uvicorn.run(
        "app:app",
        host=current_settings.app.host,
        port=current_settings.app.port,
        log_level=current_settings.app.log_level,
    )
