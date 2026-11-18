"""测试「播放键 → 回车」这条链路。

做法：启动记事本并聚焦 → 后台拉起 bridge.py → 注入一个 VK 0xB3
→ 读 bridge.log 看钩子有没有截到并改发回车 → 收尾。

注意：SendInput 注入的事件会进低级钩子（但不会进 Raw Input），
所以这个测试能验证钩子路径，不能验证 Raw Input 路径。
"""

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)
sendinput = u32.SendInput

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_MEDIA_PLAY_PAUSE = 0xB3

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


def send(vk, up=False):
    i = INPUT()
    i.type = INPUT_KEYBOARD
    i.u.ki.wVk = vk
    i.u.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
    return sendinput(1, ctypes.byref(i), ctypes.sizeof(INPUT))


def class_of(hwnd):
    c = ctypes.create_unicode_buffer(128)
    u32.GetClassNameW(hwnd, c, 128)
    return c.value


def find_notepad():
    hits = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if u32.IsWindowVisible(hwnd) and "notepad" in class_of(hwnd).lower():
            hits.append(hwnd)
        return True

    u32.EnumWindows(cb, 0)
    return hits


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    logpath = os.path.join(here, "bridge.log")

    print("启动记事本 ...")
    subprocess.Popen(["notepad.exe"])
    hwnd = None
    for _ in range(40):
        time.sleep(0.15)
        hits = find_notepad()
        if hits:
            hwnd = hits[-1]
            break
    if hwnd:
        u32.ShowWindow(hwnd, 9)
        u32.SetForegroundWindow(hwnd)
        time.sleep(0.5)
        fg = u32.GetForegroundWindow()
        print("前台窗口 class=%s  (记事本 hwnd=%d)" % (class_of(fg), hwnd))
    else:
        print("没找到记事本，回车会发到当前前台窗口")

    print("启动 bridge.py ...")
    proc = subprocess.Popen([sys.executable, os.path.join(here, "bridge.py")],
                            cwd=here, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, errors="ignore")
    time.sleep(2.0)

    before = ""
    if os.path.exists(logpath):
        before = open(logpath, encoding="utf-8", errors="ignore").read()

    print("注入 VK 0xB3（模拟遥控器播放键）...")
    send(VK_MEDIA_PLAY_PAUSE)
    time.sleep(0.05)
    send(VK_MEDIA_PLAY_PAUSE, up=True)
    time.sleep(1.0)

    print("等 1 秒后收尾 ...")
    proc.terminate()
    try:
        out = proc.communicate(timeout=5)[0]
    except subprocess.TimeoutExpired:
        proc.kill()
        out = ""

    print("\n--- bridge 输出 ---")
    print(out.strip() or "(无)")

    after = ""
    if os.path.exists(logpath):
        after = open(logpath, encoding="utf-8", errors="ignore").read()
    delta = after[len(before):]
    print("\n--- 本次新增日志 ---")
    print(delta.strip() or "(无)")

    ok = "播放键 → 回车" in delta
    print("\n结论: %s" % ("钩子成功拦截 0xB3 并改发回车" if ok
                       else "未看到拦截记录，需要检查"))

    # 收尾：撤销刚注入的换行，再关掉记事本，避免留下待保存的窗口
    if hwnd:
        print("清理：撤销换行并关闭记事本 ...")
        send(0xA2)                       # Ctrl
        send(0x5A)                       # Z
        send(0x5A, up=True)
        send(0xA2, up=True)
        time.sleep(0.3)
        u32.PostMessageW(hwnd, 0x0010, 0, 0)     # WM_CLOSE
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
