"""试探 RegisterRawInputDevices 的各种标志组合，找出能让系统彻底不处理
消费者页默认行为（音量/静音）的合法配置。

背景：RIDEV_NOLEGACY 和 RIDEV_INPUTSINK 组合会被拒（err=1004），
而我们需要「后台接收 + 不让系统做默认处理」同时成立。
本脚本用真正的 message-only 窗口逐个试，测完立即注销，不留副作用。

用法：
    python tools/flags_probe.py
"""
import ctypes
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.GetModuleHandleW.restype = ctypes.c_void_p

u32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p)
u32.CreateWindowExW.restype = wintypes.HWND
u32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
u32.DefWindowProcW.restype = ctypes.c_ssize_t
u32.RegisterClassW.argtypes = (ctypes.c_void_p,)
u32.RegisterClassW.restype = wintypes.ATOM


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


def make_hidden_window():
    proc = WNDPROC(lambda h, m, w, l: u32.DefWindowProcW(h, m, w, l))
    wc = WNDCLASSW()
    wc.lpfnWndProc = proc
    wc.hInstance = k32.GetModuleHandleW(None)
    wc.lpszClassName = "FlagsProbeWnd"
    u32.RegisterClassW(ctypes.byref(wc))
    hwnd = u32.CreateWindowExW(0, "FlagsProbeWnd", "probe", 0, 0, 0, 0, 0,
                               wintypes.HWND(-3), None, wc.hInstance, None)
    return hwnd, proc


def register(flags, hwnd):
    d = RAWINPUTDEVICE()
    d.usUsagePage = 0x0C
    d.usUsage = 0x01
    d.dwFlags = flags
    d.hwndTarget = hwnd
    ctypes.set_last_error(0)
    ok = u32.RegisterRawInputDevices(ctypes.byref(d), 1, ctypes.sizeof(d))
    return bool(ok), ctypes.get_last_error()


def main():
    u32.RegisterRawInputDevices.argtypes = (ctypes.POINTER(RAWINPUTDEVICE),
                                            wintypes.UINT, wintypes.UINT)
    u32.RegisterRawInputDevices.restype = wintypes.BOOL
    hwnd, _proc = make_hidden_window()
    if not hwnd:
        print("创建窗口失败 err=%d" % ctypes.get_last_error())
        return 1
    print("message-only 窗口 hwnd=0x%X\n" % hwnd)

    RIDEV_REMOVE, RIDEV_EXINPUTSINK, RIDEV_INPUTSINK, RIDEV_NOLEGACY = 1, 0x1000, 0x100, 0x04
    cases = [
        (RIDEV_INPUTSINK | RIDEV_NOLEGACY, "INPUTSINK|NOLEGACY（当前用的，已知被拒）"),
        (RIDEV_EXINPUTSINK | RIDEV_NOLEGACY, "EXINPUTSINK|NOLEGACY"),
        (RIDEV_NOLEGACY, "NOLEGACY 单独（要求窗口在前台）"),
        (RIDEV_EXINPUTSINK, "EXINPUTSINK 单独"),
    ]
    for flags, label in cases:
        ok, err = register(flags, hwnd)
        print("  %-38s ok=%-5s err=%d" % (label, ok, err))
        if ok:
            # 立刻注销，避免 NOLEGACY 的全局效果影响正在运行的 bridge
            register(RIDEV_REMOVE, None)
            print("  %-38s 已注销，恢复系统默认" % "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
