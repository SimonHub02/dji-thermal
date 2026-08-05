from pathlib import Path

from config.settings import load_settings


def test_relative_sdk_path_is_resolved_from_config_file(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("sdk:\n  library_path: native/libdirp.so\n", encoding="utf-8")

    settings = load_settings(config)

    assert settings.sdk.library_path == (tmp_path / "native/libdirp.so").resolve()
