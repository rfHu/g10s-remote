"""截取屏幕右下角通知区域，用于确认托盘图标是否显示。

纯 ctypes + GDI，不需要 Pillow。输出 shot-tray.bmp。
用法：python shot_tray.py [宽] [高]
"""

import ctypes
import struct
import sys
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

u32.GetDC.restype = ctypes.c_void_p
u32.GetDC.argtypes = (wintypes.HWND,)
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.argtypes = (ctypes.c_void_p,)
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.CreateCompatibleBitmap.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_int)
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
gdi32.BitBlt.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, wintypes.DWORD)
gdi32.GetDIBits.argtypes = (ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT)
gdi32.GetDIBits.restype = ctypes.c_int
u32.ReleaseDC.argtypes = (wintypes.HWND, ctypes.c_void_p)
u32.ReleaseDC.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = (ctypes.c_void_p,)
gdi32.DeleteDC.argtypes = (ctypes.c_void_p,)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def dpi_aware():
    for call in (lambda: u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)),
                 lambda: ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)):
        try:
            call()
            return
        except Exception:
            continue


def grab(path, w, h, src_x, src_y):
    hdc = u32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not gdi32.BitBlt(mem, 0, 0, w, h, hdc, src_x, src_y, 0x00CC0020):
            raise OSError("BitBlt 失败")
        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth = w
        bi.biHeight = -h
        bi.biPlanes = 1
        bi.biBitCount = 32
        size = w * h * 4
        buf = ctypes.create_string_buffer(size)
        if not gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0):
            raise OSError("GetDIBits 失败")
        with open(path, "wb") as f:
            f.write(b"BM" + struct.pack("<IHHI", 14 + 40 + size, 0, 0, 54))
            f.write(struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, size, 0, 0, 0, 0))
            f.write(buf.raw)
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        u32.ReleaseDC(None, hdc)


def main():
    dpi_aware()
    sw = u32.GetSystemMetrics(0)
    sh = u32.GetSystemMetrics(1)
    w = int(sys.argv[1]) if len(sys.argv) > 1 else 460
    h = int(sys.argv[2]) if len(sys.argv) > 2 else 70
    path = "shot-tray.bmp"
    grab(path, w, h, sw - w, sh - h)
    print("屏幕 %dx%d，已截取右下角 %dx%d -> %s" % (sw, sh, w, h, path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
