"""Set only the application DACLs, using explicit per-file ACEs on Windows.

Avoid combining recursive inheritance removal and grants in icacls: that can leave
existing children with empty DACLs. Never requests SACL/owner changes or privileges.
"""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path


def _api():
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    adv.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
                                         ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                                         ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    adv.GetNamedSecurityInfoW.restype = wintypes.DWORD
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    adv.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    adv.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
                                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    adv.SetNamedSecurityInfoW.restype = wintypes.DWORD
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    return adv, kernel


def owner_sid(path):
    adv, kernel = _api()
    owner, descriptor = ctypes.c_void_p(), ctypes.c_void_p()
    result = adv.GetNamedSecurityInfoW(str(path), 1, 1, ctypes.byref(owner), None, None, None, ctypes.byref(descriptor))
    if result:
        raise ctypes.WinError(result)
    text = wintypes.LPWSTR()
    try:
        if not adv.ConvertSidToStringSidW(owner, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        return text.value
    finally:
        if text:
            kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
        kernel.LocalFree(descriptor)


def set_dacl_only(path, sddl):
    adv, kernel = _api()
    descriptor = ctypes.c_void_p()
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if not adv.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)):
            raise ctypes.WinError(ctypes.get_last_error())
        result = adv.SetNamedSecurityInfoW(str(path), 1, 4 | 0x80000000, None, None, dacl, None)
        if result:
            raise ctypes.WinError(result)
    finally:
        kernel.LocalFree(descriptor)


def secure_tree(root):
    root = Path(root).absolute()
    if os.name != 'nt':
        raise RuntimeError('This installer is for Windows')
    owner = owner_sid(root)
    count = 0

    def secure(path):
        nonlocal count
        path.resolve().relative_to(root.resolve())
        if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
            raise RuntimeError('Refusing to set permissions through a reparse point: ' + str(path))
        directory = path.is_dir()
        inherit = 'OICI' if directory else ''
        sddl = 'D:P' + ''.join(f'(A;{inherit};FA;;;{sid})' for sid in (owner, 'SY', 'BA'))
        set_dacl_only(path, sddl)
        count += 1
        if directory:
            for child in path.iterdir():
                secure(child)

    secure(root)
    return count


if __name__ == '__main__':
    print('Verified owner/SYSTEM/Administrators access on', secure_tree(Path(__file__).resolve().parent), 'application entries.')
