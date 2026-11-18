"""客观验证 SendInput 注入在本机是否生效。

不依赖肉眼看屏幕：注入 Win+R 后枚举窗口，如果能找到「运行」对话框，
就证明注入链路是通的。找到后自动发 Esc 关掉，不留痕迹。

用法：python verify_inject.py
"""

import ctypes
import time
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)
sendinput = u32.SendInput

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
VK_LWIN = 0x5B
VK_R = 0x52
VK_ESCAPE = 0x1B

_ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
_PAD = 32 if ctypes.sizeof(ctypes.c_void_p) == 8 else 24


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", _ULONG_PTR)]


class _U(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * _PAD)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


sendinput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
sendinput.restype = wintypes.UINT


def send(vk, up=False, ext=False):
    i = INPUT()
    i.type = INPUT_KEYBOARD
    i.u.ki.wVk = vk
    i.u.ki.wScan = 0
    i.u.ki.dwFlags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if ext else 0)
    i.u.ki.time = 0
    i.u.ki.dwExtraInfo = 0
    return sendinput(1, ctypes.byref(i), ctypes.sizeof(INPUT))


def windows():
    """返回 {hwnd: (class, title)}，只取可见顶层窗口。"""
    out = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not u32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(128)
        u32.GetClassNameW(hwnd, cls, 128)
        title = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(hwnd, title, 256)
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out[hwnd] = (cls.value, title.value, pid.value)
        return True

    u32.EnumWindows(cb, 0)
    return out


def main():
    print("sizeof(INPUT) = %d" % ctypes.sizeof(INPUT))
    before = windows()
    print("注入前可见顶层窗口: %d 个" % len(before))

    fg = u32.GetForegroundWindow()
    t = ctypes.create_unicode_buffer(256)
    u32.GetWindowTextW(fg, t, 256)
    print("注入时前台窗口: [%s]" % t.value)

    print("\n注入 Win+R ...")
    send(VK_LWIN, ext=True)
    time.sleep(0.06)
    send(VK_R)
    time.sleep(0.04)
    send(VK_R, up=True)
    time.sleep(0.06)
    send(VK_LWIN, up=True, ext=True)

    time.sleep(0.9)
    after = windows()
    new = {h: v for h, v in after.items() if h not in before}

    print("\n新增窗口:")
    if not new:
        print("  （无）")
    found = False
    for h, (cls, title, pid) in new.items():
        print("  hwnd=%-10d class=%-24s pid=%-6d title=[%s]" % (h, cls, pid, title))
        if title.strip() == "运行" or cls == "#32770":
            found = True

    print("\n结论: %s" % ("注入生效（检测到 Win+R 弹出的对话框）" if found
                       else "未检测到对话框 —— 注入未生效或被拦截"))

    if found:
        print("发送 Esc 关闭对话框 ...")
        send(VK_ESCAPE)
        time.sleep(0.05)
        send(VK_ESCAPE, up=True)
        time.sleep(0.4)
        left = {h: v for h, v in windows().items() if h not in before}
        print("关闭后残留窗口: %d 个" % len(left))
        for h, (cls, title, pid) in left.items():
            print("  hwnd=%-10d class=%-24s title=[%s]" % (h, cls, title))

    # 兜底释放修饰键
    for vk, ext in ((VK_LWIN, True),):
        send(vk, up=True, ext=ext)


if __name__ == "__main__":
    main()
