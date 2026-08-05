from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


@dataclass(frozen=True, slots=True)
class AppSettings:
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"


@dataclass(frozen=True, slots=True)
class SDKSettings:
    library_path: Path
    max_concurrent_calls: int = 4


@dataclass(frozen=True, slots=True)
class DownloadSettings:
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 30.0
    retries: int = 2
    retry_backoff_seconds: float = 0.5
    max_file_size_bytes: int = 50 * 1024 * 1024
    follow_redirects: bool = True


@dataclass(frozen=True, slots=True)
class CacheSettings:
    max_entries: int = 128
    ttl_seconds: float = 3600.0


@dataclass(frozen=True, slots=True)
class Settings:
    app: AppSettings
    sdk: SDKSettings
    download: DownloadSettings
    cache: CacheSettings


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"config section '{name}' must be a mapping")
    return value


def _positive(name: str, value: int | float) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")


def load_settings(config_path: str | Path | None = None) -> Settings:
    configured_path = config_path or os.getenv("THERMAL_CONFIG_PATH")
    selected_path = Path(configured_path).expanduser() if configured_path else (
        Path(__file__).resolve().parent.parent / "config.yaml"
    )
    if not selected_path.is_absolute():
        selected_path = (Path.cwd() / selected_path).resolve()
    if not selected_path.is_file():
        raise FileNotFoundError(f"configuration file does not exist: {selected_path}")

    raw = yaml.safe_load(selected_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("config root must be a mapping")

    app_data = _section(raw, "app")
    sdk_data = _section(raw, "sdk")
    download_data = _section(raw, "download")
    cache_data = _section(raw, "cache")

    configured_library = os.getenv("SDK_LIBRARY_PATH") or sdk_data.get("library_path")
    if not configured_library:
        raise ValueError("sdk.library_path is required")
    library_path = Path(str(configured_library)).expanduser()
    if not library_path.is_absolute():
        library_path = (selected_path.parent / library_path).resolve()

    max_megabytes = float(download_data.get("max_file_size_mb", 50))
    settings = Settings(
        app=AppSettings(
            host=str(app_data.get("host", "0.0.0.0")),
            port=int(app_data.get("port", 8000)),
            log_level=str(app_data.get("log_level", "info")),
        ),
        sdk=SDKSettings(
            library_path=library_path,
            max_concurrent_calls=int(sdk_data.get("max_concurrent_calls", 4)),
        ),
        download=DownloadSettings(
            connect_timeout_seconds=float(
                download_data.get("connect_timeout_seconds", 5)
            ),
            read_timeout_seconds=float(download_data.get("read_timeout_seconds", 30)),
            retries=int(download_data.get("retries", 2)),
            retry_backoff_seconds=float(
                download_data.get("retry_backoff_seconds", 0.5)
            ),
            max_file_size_bytes=int(max_megabytes * 1024 * 1024),
            follow_redirects=bool(download_data.get("follow_redirects", True)),
        ),
        cache=CacheSettings(
            max_entries=int(cache_data.get("max_entries", 128)),
            ttl_seconds=float(cache_data.get("ttl_seconds", 3600)),
        ),
    )

    _positive("app.port", settings.app.port)
    _positive("sdk.max_concurrent_calls", settings.sdk.max_concurrent_calls)
    _positive("download.connect_timeout_seconds", settings.download.connect_timeout_seconds)
    _positive("download.read_timeout_seconds", settings.download.read_timeout_seconds)
    _positive("download.max_file_size_mb", max_megabytes)
    _positive("cache.max_entries", settings.cache.max_entries)
    _positive("cache.ttl_seconds", settings.cache.ttl_seconds)
    if settings.download.retries < 0:
        raise ValueError("download.retries must not be negative")
    if settings.download.retry_backoff_seconds < 0:
        raise ValueError("download.retry_backoff_seconds must not be negative")
    if not 1 <= settings.app.port <= 65535:
        raise ValueError("app.port must be between 1 and 65535")
    return settings
