"""验证微信输入法认不认「注入的 Ctrl+Win」。

判断方式不靠肉眼：注入前后对比顶层窗口，凡属 wetype_* 进程的新窗口都算
「输入法响应了」。同时打印注入前的输入法运行状态，避免在输入法没开时
把「没反应」误判成「拒绝注入」。

用法：python verify_ctrlwin.py
"""

import ctypes
import subprocess
import time
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)
sendinput = u32.SendInput

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
VK_LCONTROL = 0xA2
VK_LWIN = 0x5B

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
    i.u.ki.dwFlags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if ext else 0)
    return sendinput(1, ctypes.byref(i), ctypes.sizeof(INPUT))


def wetype_procs():
    """返回正在运行的 wetype_* 进程名集合。"""
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"],
                             capture_output=True, text=True, timeout=10,
                             errors="ignore").stdout
    except Exception:
        return set()
    names = set()
    for line in out.splitlines():
        first = line.split(",")[0].strip('"').lower()
        if first.startswith("wetype"):
            names.add(first)
    return names


def windows():
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


def pid_names():
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"],
                             capture_output=True, text=True, timeout=10,
                             errors="ignore").stdout
    except Exception:
        return {}
    m = {}
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split(",")]
        if len(parts) >= 2:
            try:
                m[int(parts[1])] = parts[0]
            except ValueError:
                pass
    return m


def main():
    procs = wetype_procs()
    print("注入前 wetype 进程: %s" % (", ".join(sorted(procs)) if procs else "（无）"))
    if not procs:
        print("\n⚠ 微信输入法没有在运行。此时注入 Ctrl+Win 必然无反应，")
        print("  这个结果不能说明它是否接受注入。请先切换到微信输入法再跑。")
        return 1

    fg = u32.GetForegroundWindow()
    t = ctypes.create_unicode_buffer(256)
    u32.GetWindowTextW(fg, t, 256)
    print("注入时前台窗口: [%s]" % t.value)

    before = windows()
    print("\n注入 Ctrl+Win（按住 2 秒）...")
    send(VK_LCONTROL)
    time.sleep(0.05)
    send(VK_LWIN, ext=True)
    print("  保持中 —— 如果认注入，此时应有语音浮窗", flush=True)
    time.sleep(2.0)
    send(VK_LWIN, up=True, ext=True)
    time.sleep(0.05)
    send(VK_LCONTROL, up=True)

    time.sleep(0.6)
    after = windows()
    names = pid_names()
    new = {h: v for h, v in after.items() if h not in before}

    print("\n新增窗口:")
    if not new:
        print("  （无）")
    hit = []
    for h, (cls, title, pid) in new.items():
        pname = names.get(pid, "?")
        print("  hwnd=%-10d pid=%-6d %-24s class=%-24s title=[%s]"
              % (h, pid, pname, cls, title))
        if pname.lower().startswith("wetype"):
            hit.append(pname)

    print("\n注入后 wetype 进程: %s"
          % (", ".join(sorted(wetype_procs())) or "（无）"))

    if hit:
        print("\n结论: 微信输入法响应了注入（%s），方案可行。" % ", ".join(sorted(set(hit))))
    else:
        print("\n结论: 没有检测到微信输入法的窗口。可能是「拒绝注入」，")
        print("      也可能是语音界面不是独立窗口（画在别处）。请同时用眼睛确认一次。")

    for vk, ext in ((VK_LWIN, True), (VK_LCONTROL, False)):
        send(vk, up=True, ext=ext)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
