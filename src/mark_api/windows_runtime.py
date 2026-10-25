"""Native Windows helpers for the ordinary (non-A6) customer runtime.

The Linux multi-UID/Landlock product launcher remains Linux-only. This module
does not provide a substitute A6 security attestation.
"""
from __future__ import annotations

import ctypes
import os
import stat
from pathlib import Path

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _kernel32():
    if os.name != "nt":
        raise OSError("Windows kernel APIs are unavailable")
    return ctypes.WinDLL("kernel32", use_last_error=True)


class WindowsCdpTargetClaim:
    """An exclusive, process-wide and cross-process named Windows kernel object.

    Unlike a Win32 mutex, an event's lifetime is not bound to a single thread:
    the last successful CloseHandle releases the exclusive name. This is a
    cooperating-client guard, not browser/platform authentication.
    """

    def __init__(self, port: int) -> None:
        if os.name != "nt" or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("invalid Windows CDP target")
        kernel = _kernel32()
        create = kernel.CreateEventW
        create.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p)
        create.restype = ctypes.c_void_p
        # Global\ namespace coordinates separate Windows desktop sessions.
        handle = create(None, False, False, f"Global\\kleinanzeiger-cdp-target-v1-{port}")
        if not handle:
            raise OSError(ctypes.get_last_error(), "Windows target claim unavailable")
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
            kernel.CloseHandle.restype = ctypes.c_int
            kernel.CloseHandle(handle)
            raise FileExistsError("CDP target already reserved")
        self._handle: int | None = int(handle)
        self.port = port

    def is_live(self) -> bool:
        handle = self._handle
        if handle is None:
            return False
        kernel = _kernel32()
        check = kernel.GetHandleInformation
        check.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
        check.restype = ctypes.c_int
        flags = ctypes.c_ulong()
        return bool(check(ctypes.c_void_p(handle), ctypes.byref(flags)))

    def close(self) -> None:
        handle = self._handle
        if handle is None:
            return
        kernel = _kernel32()
        close = kernel.CloseHandle
        close.argtypes = (ctypes.c_void_p,)
        close.restype = ctypes.c_int
        if not close(ctypes.c_void_p(handle)):
            raise OSError(ctypes.get_last_error(), "Windows target release unavailable")
        self._handle = None


def require_private_windows_directory(path: str | os.PathLike[str], *, create: bool = False) -> Path:
    """Admit only non-reparse paths under this user's LocalAppData profile.

    Windows user-profile ACLs provide the normal per-user boundary. This check
    is not a protection against an attacker already executing as that user.
    """
    if os.name != "nt":
        raise OSError("Windows directory boundary unavailable")
    env = os.environ.get("LOCALAPPDATA")
    if not env or not os.path.isabs(env) or not os.path.isabs(os.fspath(path)):
        raise OSError("private Windows profile path is unavailable")
    root = Path(os.path.abspath(env))
    destination = Path(os.path.abspath(path))
    root_normal = os.path.normcase(str(root))
    dest_normal = os.path.normcase(str(destination))
    try:
        inside = os.path.commonpath((root_normal, dest_normal)) == root_normal
    except ValueError:
        inside = False
    if not inside or destination == root:
        raise OSError("private Windows path must be beneath LocalAppData")
    parents = [destination, *destination.parents]
    for entry in reversed(parents):
        if os.path.normcase(str(entry)) == root_normal:
            break
    else:
        raise OSError("private Windows root is untrusted")
    segments = [root]
    rel = destination.relative_to(root)
    for part in rel.parts:
        segments.append(segments[-1] / part)
    for segment in segments[:-1]:
        info = segment.lstat()
        if not stat.S_ISDIR(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
        ):
            raise OSError("private Windows path has an unsafe parent")
    if create:
        try:
            destination.mkdir()
        except FileExistsError:
            pass
    info = destination.lstat()
    if not stat.S_ISDIR(info.st_mode) or (
        getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
    ):
        raise OSError("private Windows directory is unsafe")
    return destination
