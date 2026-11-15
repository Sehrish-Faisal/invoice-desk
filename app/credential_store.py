"""Current-Windows-user encrypted API credentials, outside the source checkout."""
import ctypes
from ctypes import wintypes
import os


class Blob(ctypes.Structure):
    _fields_ = [('length', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

def protect(data: bytes, decrypt=False) -> bytes:
    if os.name != 'nt':
        raise RuntimeError('Encrypted local storage requires Windows; use server environment variables elsewhere.')
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, output = Blob(len(data), buffer), Blob()
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN, user scope; never machine-wide protection.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise RuntimeError('Windows credential encryption/decryption failed.')
    try: return ctypes.string_at(output.data, output.length)
    finally: kernel.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))
