"""Windows handle-owned file publication. Never closes a target lock to overwrite by path."""
from __future__ import annotations

import ctypes
import hashlib
import os
from contextlib import ExitStack, contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import Generator

from .patches import RESERVED
from .scoped_files import ScopeError, pin_path, identity, verify


class FileInfo(ctypes.Structure):
    _fields_ = [('attributes', wintypes.DWORD), ('created', wintypes.FILETIME), ('accessed', wintypes.FILETIME),
        ('written', wintypes.FILETIME), ('volume', wintypes.DWORD), ('sizeHigh', wintypes.DWORD),
        ('sizeLow', wintypes.DWORD), ('links', wintypes.DWORD), ('indexHigh', wintypes.DWORD), ('indexLow', wintypes.DWORD)]


class SecurityAttributes(ctypes.Structure):
    _fields_ = [('length', wintypes.DWORD), ('descriptor', ctypes.c_void_p), ('inherit', wintypes.BOOL)]


class StreamInfo(ctypes.Structure):
    _fields_ = [('size', ctypes.c_int64), ('name', wintypes.WCHAR * 296)]


def libraries():
    if os.name != 'nt':
        raise ScopeError('WINDOWS_REQUIRED')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInfo)]
    kernel.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_int64, ctypes.c_void_p, wintypes.DWORD]
    kernel.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel.WriteFile.argtypes = kernel.ReadFile.argtypes
    kernel.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.FindFirstStreamW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    kernel.FindFirstStreamW.restype = wintypes.HANDLE
    kernel.FindNextStreamW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel.FindClose.argtypes = [wintypes.HANDLE]
    advapi.GetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [ctypes.c_void_p, wintypes.DWORD,
        wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    advapi.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    advapi.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
    advapi.SetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    return kernel, advapi


class LockedFile:
    def __init__(self, path: Path, *, create: bool = False, security: str | None = None, attributes: int = 0x80):
        self.path = path
        self.kernel, self.advapi = libraries()
        descriptor = ctypes.c_void_p()
        try:
            pointer = None
            if create:
                if not path.name.startswith(RESERVED) or security is None:
                    raise ScopeError('INVALID_REQUEST')
                if not self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(security, 1, ctypes.byref(descriptor), None):
                    raise ScopeError('ACCESS_DENIED')
                attrs = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
                pointer = ctypes.byref(attrs)
            # DELETE access lets the owning handle rename itself; other handles cannot write/delete.
            self.handle = self.kernel.CreateFileW(str(path), 0x80030000 | (0x40040000 if create else 0),
                1, pointer, 1 if create else 3, 0x00200000 | attributes, None)
        finally:
            if descriptor.value:
                self.kernel.LocalFree(descriptor)
        if self.handle == ctypes.c_void_p(-1).value:
            raise ScopeError('ACCESS_DENIED')
        try:
            info = self.info()
            if info.attributes & (0x400 | 0x10 | 0x1) or info.links != 1:
                raise ScopeError('PATCH_SCOPE_UNSUPPORTED')
            if info.attributes & ~(0x80 | 0x20 | 0x2 | 0x4 | 0x2000):
                raise ScopeError('PATCH_SCOPE_UNSUPPORTED')
            self.file_identity = f'{info.volume}:{info.indexHigh}:{info.indexLow}'
            self.attributes = info.attributes
            self.security = self.security_text()
            if create and self.security != security:
                assert security is not None
                self.clone_dacl(security)
                self.security = self.security_text()
            if create and self.security != security:
                raise ScopeError('ACCESS_DENIED')
            stream = StreamInfo()
            search = self.kernel.FindFirstStreamW(str(path), 0, ctypes.byref(stream), 0)
            if search == ctypes.c_void_p(-1).value:
                if ctypes.get_last_error() != 38:
                    raise ScopeError('PATCH_SCOPE_UNSUPPORTED')
            else:
                try:
                    while True:
                        if stream.name != '::$DATA':
                            raise ScopeError('PATCH_SCOPE_UNSUPPORTED')
                        if not self.kernel.FindNextStreamW(search, ctypes.byref(stream)):
                            if ctypes.get_last_error() != 38:
                                raise ScopeError('PATCH_SCOPE_UNSUPPORTED')
                            break
                finally:
                    self.kernel.FindClose(search)
        except Exception:
            if create:
                try:
                    self.delete_owned()
                except ScopeError:
                    pass
            self.close()
            raise

    def info(self) -> FileInfo:
        value = FileInfo()
        if not self.kernel.GetFileInformationByHandle(self.handle, ctypes.byref(value)):
            raise ScopeError('ACCESS_DENIED')
        return value

    def security_text(self) -> str:
        descriptor, text = ctypes.c_void_p(), wintypes.LPWSTR()
        try:
            if self.advapi.GetSecurityInfo(self.handle, 1, 7, None, None, None, None, ctypes.byref(descriptor)):
                raise ScopeError('ACCESS_DENIED')
            if not self.advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 7, ctypes.byref(text), None):
                raise ScopeError('ACCESS_DENIED')
            return str(text.value)
        finally:
            if text:
                self.kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
            if descriptor.value:
                self.kernel.LocalFree(descriptor)

    def clone_dacl(self, security: str) -> None:
        # Set inheritance flags on our still-empty staging file, never the source.
        # No bytes are written unless owner/group/DACL including all flags match.
        if not self.path.name.startswith(RESERVED) or self.info().sizeLow:
            raise ScopeError('ACCESS_DENIED')
        descriptor, dacl = ctypes.c_void_p(), ctypes.c_void_p()
        present, defaulted = wintypes.BOOL(), wintypes.BOOL()
        control, revision = wintypes.WORD(), wintypes.DWORD()
        try:
            if not self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(security, 1, ctypes.byref(descriptor), None):
                raise ScopeError('ACCESS_DENIED')
            if (not self.advapi.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted))
                    or not self.advapi.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision))):
                raise ScopeError('ACCESS_DENIED')
            flags = 0x80000000 if control.value & 0x1000 else 0x20000000
            if self.advapi.SetSecurityInfo(self.handle, 1, 4 | flags, None, None, dacl, None):
                raise ScopeError('ACCESS_DENIED')
        finally:
            if descriptor.value:
                self.kernel.LocalFree(descriptor)

    def read(self) -> bytes:
        if self.info().sizeHigh or self.info().sizeLow > 200000:
            raise ScopeError('INPUT_TOO_LARGE')
        if not self.kernel.SetFilePointerEx(self.handle, 0, None, 0):
            raise ScopeError('ACCESS_DENIED')
        buffer = ctypes.create_string_buffer(200001)
        count = wintypes.DWORD()
        if not self.kernel.ReadFile(self.handle, buffer, 200001, ctypes.byref(count), None):
            raise ScopeError('ACCESS_DENIED')
        return buffer.raw[:count.value]

    def write(self, content: bytes) -> None:
        if not self.path.name.startswith(RESERVED) or len(content) > 200000 or self.info().sizeLow:
            raise ScopeError('INVALID_REQUEST')
        count = wintypes.DWORD()
        if (not self.kernel.WriteFile(self.handle, content, len(content), ctypes.byref(count), None)
                or count.value != len(content) or not self.kernel.FlushFileBuffers(self.handle)):
            raise ScopeError('STORAGE_UNAVAILABLE')

    def rename(self, path: Path) -> None:
        if path.parent != self.path.parent:
            raise ScopeError('PATH_OUTSIDE_SCOPE')
        encoded = str(path).encode('utf-16-le')
        class Rename(ctypes.Structure):
            _fields_ = [('flags', wintypes.DWORD), ('root', wintypes.HANDLE), ('length', wintypes.DWORD),
                ('name', wintypes.WCHAR * (len(encoded) // 2 + 1))]
        value = Rename()
        value.flags = 0  # Never replace anything already created at the destination.
        value.length = len(encoded)
        value.name = str(path)
        if not self.kernel.SetFileInformationByHandle(self.handle, 22, ctypes.byref(value), Rename.name.offset + len(encoded)):
            raise ScopeError('PRECONDITION_FAILED')
        self.path = path

    def delete_owned(self) -> None:
        if not self.path.name.startswith(RESERVED):
            raise ScopeError('PATH_OUTSIDE_SCOPE')
        delete = wintypes.BOOL(True)
        if not self.kernel.SetFileInformationByHandle(self.handle, 4, ctypes.byref(delete), ctypes.sizeof(delete)):
            raise ScopeError('ACCESS_DENIED')

    def digest(self) -> str:
        return hashlib.sha256(self.read()).hexdigest()

    def close(self) -> None:
        if getattr(self, 'handle', None) not in (None, ctypes.c_void_p(-1).value):
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self) -> LockedFile:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@contextmanager
def write_scope(grant: dict[str, str], paths: list[str]) -> Generator[None, None, None]:
    with ExitStack() as stack:
        seen: set[Path] = set()
        for root in [Path(grant['root']), Path(grant['gitDir']), Path(grant['commonDir']),
                     *(Path(grant['root']).joinpath(*path.split('/')).parent for path in paths)]:
            for parent in (*reversed(root.parents), root):
                if parent not in seen:
                    pin_path(parent, stack)
                    seen.add(parent)
        kernel, _ = libraries()
        index = Path(grant['gitDir']) / 'index'
        if not index.is_file():
            raise ScopeError('INDEX_UNAVAILABLE')
        pinned_files: set[Path] = set()
        for directory in {Path(grant['gitDir']), Path(grant['commonDir'])}:
            for parent, children, names in os.walk(directory, followlinks=False):
                for name in children:
                    child = Path(parent) / name
                    if child not in seen:
                        pin_path(child, stack)
                        seen.add(child)
                for name in names:
                    file = Path(parent) / name
                    if file in pinned_files:
                        continue
                    if len(pinned_files) >= 20000:
                        raise ScopeError('OUTPUT_LIMIT')
                    identity(file)
                    handle = kernel.CreateFileW(str(file), 0x80000000, 1, None, 3, 0x00200000, None)
                    if handle == ctypes.c_void_p(-1).value:
                        raise ScopeError('ACCESS_DENIED')
                    stack.callback(kernel.CloseHandle, handle)
                    pinned_files.add(file)
        verify(grant)
        yield
        verify(grant)
