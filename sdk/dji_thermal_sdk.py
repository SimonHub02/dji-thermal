from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt


ERROR_NAMES = {
    -1: "malloc error",
    -2: "null pointer",
    -3: "invalid parameters",
    -4: "invalid RAW data in R-JPEG",
    -5: "invalid R-JPEG header",
    -6: "invalid curve LUT in R-JPEG",
    -7: "R-JPEG parse error",
    -8: "incorrect buffer size",
    -9: "invalid SDK handle",
    -10: "invalid input image format",
    -11: "invalid output image format",
    -12: "unsupported SDK function",
    -13: "SDK is not ready",
    -14: "SDK activation failed",
    -15: "invalid libv_list.ini",
    -16: "invalid dependent SDK library",
    -64: "unsupported super-mode image",
}


class SDKLoadError(RuntimeError):
    """The native DJI SDK could not be loaded."""


class SDKError(RuntimeError):
    def __init__(self, function: str, return_code: int) -> None:
        self.function = function
        self.return_code = return_code
        description = ERROR_NAMES.get(return_code, "unknown SDK error")
        super().__init__(f"{function} failed with code {return_code}: {description}")


class DirpResolution(ctypes.Structure):
    _fields_ = [("width", ctypes.c_int32), ("height", ctypes.c_int32)]


@dataclass(slots=True)
class RJPEGHandle:
    pointer: ctypes.c_void_p
    data_buffer: Any
    width: int
    height: int
    destroyed: bool = False


class DJIThermalSDKWrapper:
    """Small, typed ctypes boundary around DJI's DIRP native library."""

    def __init__(self, library_path: str | Path) -> None:
        self.library_path = Path(library_path).resolve()
        if not self.library_path.is_file():
            raise SDKLoadError(f"DJI SDK library does not exist: {self.library_path}")

        self._dll_directory: Any | None = None
        try:
            if os.name == "nt" and hasattr(os, "add_dll_directory"):
                self._dll_directory = os.add_dll_directory(
                    str(self.library_path.parent)
                )
            self._library = ctypes.CDLL(str(self.library_path))
        except OSError as exc:
            raise SDKLoadError(
                f"failed to load DJI SDK library '{self.library_path}': {exc}"
            ) from exc
        self._bind_functions()

    def _bind_functions(self) -> None:
        self._library.dirp_create_from_rjpeg.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_int32,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._library.dirp_create_from_rjpeg.restype = ctypes.c_int32

        self._library.dirp_get_rjpeg_resolution.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(DirpResolution),
        ]
        self._library.dirp_get_rjpeg_resolution.restype = ctypes.c_int32

        self._library.dirp_measure.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int16),
            ctypes.c_int32,
        ]
        self._library.dirp_measure.restype = ctypes.c_int32

        self._library.dirp_destroy.argtypes = [ctypes.c_void_p]
        self._library.dirp_destroy.restype = ctypes.c_int32

    @staticmethod
    def _check(function: str, return_code: int) -> None:
        if return_code != 0:
            raise SDKError(function, return_code)

    def create_from_rjpeg(self, rjpeg_data: bytes) -> RJPEGHandle:
        if not rjpeg_data:
            raise ValueError("R-JPEG data must not be empty")
        if len(rjpeg_data) > 2_147_483_647:
            raise ValueError("R-JPEG data exceeds the SDK int32 size limit")

        data_buffer = (ctypes.c_uint8 * len(rjpeg_data)).from_buffer_copy(rjpeg_data)
        pointer = ctypes.c_void_p()
        return_code = self._library.dirp_create_from_rjpeg(
            data_buffer, len(rjpeg_data), ctypes.byref(pointer)
        )
        self._check("dirp_create_from_rjpeg", return_code)
        if not pointer.value:
            raise SDKError("dirp_create_from_rjpeg", -2)

        resolution = DirpResolution()
        try:
            return_code = self._library.dirp_get_rjpeg_resolution(
                pointer, ctypes.byref(resolution)
            )
            self._check("dirp_get_rjpeg_resolution", return_code)
            if resolution.width <= 0 or resolution.height <= 0:
                raise SDKError("dirp_get_rjpeg_resolution", -8)
        except Exception:
            self._library.dirp_destroy(pointer)
            raise

        return RJPEGHandle(
            pointer=pointer,
            data_buffer=data_buffer,
            width=resolution.width,
            height=resolution.height,
        )

    def measure(self, handle: RJPEGHandle) -> npt.NDArray[np.float32]:
        if handle.destroyed:
            raise ValueError("cannot measure with a destroyed SDK handle")
        pixel_count = handle.width * handle.height
        output = (ctypes.c_int16 * pixel_count)()
        output_size = pixel_count * ctypes.sizeof(ctypes.c_int16)
        return_code = self._library.dirp_measure(
            handle.pointer, output, output_size
        )
        self._check("dirp_measure", return_code)

        # DIRP INT16 output uses deci-degrees Celsius; copy before the ctypes
        # buffer leaves scope so the returned ndarray owns its memory.
        temperatures = np.ctypeslib.as_array(output).reshape(
            handle.height, handle.width
        )
        return temperatures.astype(np.float32) / np.float32(10.0)

    def destroy(self, handle: RJPEGHandle) -> None:
        if handle.destroyed:
            return
        return_code = self._library.dirp_destroy(handle.pointer)
        handle.destroyed = True
        handle.pointer = ctypes.c_void_p()
        handle.data_buffer = None
        self._check("dirp_destroy", return_code)
