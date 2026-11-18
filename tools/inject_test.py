"""注入验证：微信输入法认不认「程序假装按下 Ctrl+Win」。

只做一件事——用 SendInput 注入按键，然后由你用眼睛判断输入法有没有反应。
它不读设备、不改系统、不常驻，跑完就退出。

用法：
    python inject_test.py              # 注入 Ctrl+Win，按住 2 秒后松开
    python inject_test.py --secs 3     # 按住 3 秒
    python inject_test.py --mode winr  # 注入 Win+R（先确认注入机制本身通不通）
    python inject_test.py --dry        # 只打印结构体尺寸，不注入

跑之前请把光标放进一个输入框（记事本就行），并确认当前输入法是微信输入法。
"""

import argparse
import ctypes
import sys
import time
from ctypes import wintypes

sendinput = ctypes.WinDLL("user32", use_last_error=True).SendInput

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001

VK_LCONTROL = 0xA2
VK_LWIN = 0x5B
VK_R = 0x52
VK_ESCAPE = 0x1B

# ULONG_PTR：x64 是 8 字节，x86 是 4 字节
_ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


# INPUT 是个联合体，SendInput 会严格校验 cbSize 必须等于 sizeof(INPUT)。
# 联合体最大成员是 MOUSEINPUT（x64 32 字节 / x86 24 字节），所以用字节数组占位。
_PAD = 32 if ctypes.sizeof(ctypes.c_void_p) == 8 else 24


class _UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * _PAD)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]


sendinput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
sendinput.restype = wintypes.UINT


def send(vk, up=False, extended=False):
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.u.ki.wVk = vk
    inp.u.ki.wScan = 0
    flags = 0
    if up:
        flags |= KEYEVENTF_KEYUP
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    inp.u.ki.dwFlags = flags
    inp.u.ki.time = 0
    inp.u.ki.dwExtraInfo = 0
    n = sendinput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    if n != 1:
        raise OSError(ctypes.get_last_error(), "SendInput 失败")
    return n


def release_all():
    """兜底：强制松开可能卡住的修饰键，避免系统处于奇怪状态。"""
    for vk in (VK_LWIN, VK_LCONTROL):
        try:
            send(vk, up=True, extended=(vk == VK_LWIN))
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["ctrlwin", "winr"], default="ctrlwin")
    ap.add_argument("--secs", type=float, default=2.0, help="按住时长（秒）")
    ap.add_argument("--dry", action="store_true", help="只打印结构体尺寸")
    ap.add_argument("--no-wait", action="store_true", help="跳过倒计时")
    a = ap.parse_args()

    print("sizeof(KEYBDINPUT) = %d" % ctypes.sizeof(KEYBDINPUT))
    print("sizeof(INPUT)      = %d" % ctypes.sizeof(INPUT))
    print("期望 sizeof(INPUT) = %d" % (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28))
    if a.dry:
        print("\n--dry 模式，不注入任何按键。")
        return 0

    print("\n模式：%s，按住 %.1f 秒" % (a.mode, a.secs))
    if a.mode == "ctrlwin":
        print("请确认：光标已在输入框内，当前输入法是微信输入法。")
    else:
        print("请确认：能看到桌面（会尝试弹出「运行」对话框）。")

    if not a.no_wait:
        for i in (3, 2, 1):
            print("  %d..." % i, flush=True)
            time.sleep(1)

    try:
        t0 = time.time()
        if a.mode == "winr":
            print("[注入] Win 按下 → R 按下 → R 松开 → Win 松开")
            send(VK_LWIN, extended=True)
            time.sleep(0.06)
            send(VK_R)
            time.sleep(0.04)
            send(VK_R, up=True)
            time.sleep(0.06)
            send(VK_LWIN, up=True, extended=True)
        else:
            print("[注入] Ctrl 按下 → Win 按下 ...")
            send(VK_LCONTROL)
            time.sleep(0.05)
            send(VK_LWIN, extended=True)
            print("        保持中（%.1f 秒），此时应有语音浮窗" % a.secs, flush=True)
            time.sleep(a.secs)
            print("[注入] Win 松开 → Ctrl 松开")
            send(VK_LWIN, up=True, extended=True)
            time.sleep(0.05)
            send(VK_LCONTROL, up=True)
        print("[完成] 用时 %.0f ms" % ((time.time() - t0) * 1000))
    finally:
        release_all()

    print("""
请对照判断：
  1. 语音浮窗出现了、能说话        → 注入被接受，方案可行
  2. 完全没反应                    → 注入被忽略，需要驱动层注入
  3. 弹出了开始菜单 / 其他异常      → Win 键松手有副作用，需要调整顺序
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
