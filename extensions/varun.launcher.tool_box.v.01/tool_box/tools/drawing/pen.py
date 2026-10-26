"""Read Windows Ink pressure without consuming Kit's pointer/mouse messages."""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from typing import Any, ClassVar

LOGGER: logging.Logger = logging.getLogger(__name__)


class PointerInfo(ctypes.Structure):
    _fields_: ClassVar[list[tuple[str, Any]]] = [
        ("pointerType", wintypes.UINT),
        ("pointerId", wintypes.UINT),
        ("frameId", wintypes.UINT),
        ("pointerFlags", wintypes.UINT),
        ("sourceDevice", wintypes.HANDLE),
        ("hwndTarget", wintypes.HWND),
        ("ptPixelLocation", wintypes.POINT),
        ("ptHimetricLocation", wintypes.POINT),
        ("ptPixelLocationRaw", wintypes.POINT),
        ("ptHimetricLocationRaw", wintypes.POINT),
        ("dwTime", wintypes.DWORD),
        ("historyCount", wintypes.UINT),
        ("InputData", ctypes.c_int32),
        ("dwKeyStates", wintypes.DWORD),
        ("PerformanceCount", ctypes.c_uint64),
        ("ButtonChangeType", wintypes.UINT),
    ]
    pointerFlags: int


class PenInfo(ctypes.Structure):
    _fields_: ClassVar[list[tuple[str, Any]]] = [
        ("pointerInfo", PointerInfo),
        ("penFlags", wintypes.UINT),
        ("penMask", wintypes.UINT),
        ("pressure", wintypes.UINT),
        ("rotation", wintypes.UINT),
        ("tiltX", ctypes.c_int32),
        ("tiltY", ctypes.c_int32),
    ]
    pointerInfo: PointerInfo
    penMask: int
    pressure: int


class PenInput:
    _instance: ClassVar[PenInput | None] = None

    def __init__(self) -> None:
        self.pressure: float | None = None
        self.available: bool = False
        self._hwnd: int | None = None
        self._callback: Any = None
        self._user32: Any = None
        self._comctl32: Any = None

    @classmethod
    def get(cls) -> PenInput:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start(self) -> None:
        if self._hwnd is not None or sys.platform != "win32":
            return
        import carb.windowing
        import omni.appwindow

        try:
            native: Any = carb.windowing.acquire_windowing_interface().get_native_window(
                omni.appwindow.get_default_app_window().get_window()
            )
            capsule_name: Any = ctypes.pythonapi.PyCapsule_GetName
            capsule_name.argtypes = [ctypes.py_object]
            capsule_name.restype = ctypes.c_char_p
            capsule_pointer: Any = ctypes.pythonapi.PyCapsule_GetPointer
            capsule_pointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
            capsule_pointer.restype = ctypes.c_void_p
            hwnd: int = int(capsule_pointer(native, capsule_name(native)))
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            self._comctl32 = ctypes.WinDLL("comctl32", use_last_error=True)
            self._user32.GetPointerPenInfo.argtypes = [wintypes.UINT, ctypes.POINTER(PenInfo)]
            self._user32.GetPointerPenInfo.restype = wintypes.BOOL
            self._user32.GetMessageExtraInfo.restype = ctypes.c_ssize_t
            callback_type: Any = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t,
                wintypes.HWND,
                wintypes.UINT,
                ctypes.c_size_t,
                ctypes.c_ssize_t,
                ctypes.c_size_t,
                ctypes.c_size_t,
            )
            self._callback = callback_type(self._message)
            self._comctl32.SetWindowSubclass.argtypes = [wintypes.HWND, callback_type, ctypes.c_size_t, ctypes.c_size_t]
            self._comctl32.SetWindowSubclass.restype = wintypes.BOOL
            self._comctl32.RemoveWindowSubclass.argtypes = [wintypes.HWND, callback_type, ctypes.c_size_t]
            self._comctl32.RemoveWindowSubclass.restype = wintypes.BOOL
            self._comctl32.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
            self._comctl32.DefSubclassProc.restype = ctypes.c_ssize_t
            if not self._comctl32.SetWindowSubclass(hwnd, self._callback, id(self), 0):
                raise ctypes.WinError(ctypes.get_last_error())
            self._hwnd = hwnd
            self.available = True
        except (OSError, TypeError, ValueError, AttributeError):
            LOGGER.exception("Windows Ink pressure input could not be initialized")

    def _message(self, hwnd: int, message: int, wparam: int, lparam: int, subclass: int, data: int) -> int:
        try:
            # POINTERDOWN / POINTERUPDATE: capture pressure before Kit promotes the pen to a mouse.
            if message in (0x0245, 0x0246):
                pen: PenInfo = PenInfo()
                if not self._user32.GetPointerPenInfo(wparam & 0xFFFF, ctypes.byref(pen)) or not pen.penMask & 1:
                    self.pressure = None
                elif pen.pointerInfo.pointerFlags & 4:
                    self.pressure = max(0.0, min(1.0, pen.pressure / 1024.0))
            elif message in (0x0200, 0x0201):
                # Genuine mouse events must not reuse the previous pen's pressure.
                extra: int = int(self._user32.GetMessageExtraInfo())
                if extra & 0xFFFFFF00 != 0xFF515700:
                    self.pressure = None
            elif message == 0x0008:  # KILLFOCUS
                self.pressure = None
        except Exception:
            LOGGER.exception("Could not read Windows Ink sample")
        return int(self._comctl32.DefSubclassProc(hwnd, message, wparam, lparam))

    def stop(self) -> None:
        if self._hwnd is not None and not self._comctl32.RemoveWindowSubclass(self._hwnd, self._callback, id(self)):
            LOGGER.error("Could not detach Windows Ink callback; retaining it safely")
            return
        self._hwnd = None
        self._callback = None
        self.available = False
        self.pressure = None
