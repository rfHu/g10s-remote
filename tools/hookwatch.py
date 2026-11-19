"""被动监听低级键盘钩子：只记录，不拦截、不改写、不注入。

用途：确认某个 HID 用途码被 Windows 翻译成了哪个 VK，以及「按住 / 松开」
在钩子这一侧的时间关系（按键到底是跟着手指走，还是固定脉冲）。

写出的日志：tools/hookwatch.log
"""

import ctypes
import os
import sys
import time
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
_ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

REPEAT_LOG_INTERVAL = 0.25          # 自动重复最多每 250ms 记一行


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", _ULONG_PTR)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                              wintypes.WPARAM, wintypes.LPARAM)
u32.CallNextHookEx.argtypes = (ctypes.c_void_p, ctypes.c_int,
                               wintypes.WPARAM, wintypes.LPARAM)
u32.CallNextHookEx.restype = ctypes.c_ssize_t
u32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD)
u32.SetWindowsHookExW.restype = ctypes.c_void_p
u32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                            wintypes.UINT, wintypes.UINT)

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hookwatch.log")
_logf = open(LOG_PATH, "w", encoding="utf-8")

_down_at = {}        # vk -> 按下时刻
_last_repeat = {}    # vk -> 上次记录重复的时刻


def emit(text):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), text)
    try:
        print(line, flush=True)
    except Exception:
        pass
    _logf.write(line + "\n")
    _logf.flush()


def on_key(n_code, w_param, l_param):
    if n_code == 0:
        kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        vk = kb.vkCode
        now = time.monotonic()
        if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
            if vk in _down_at:
                # 按住期间系统重复，节流记录，用来判断这个键会不会连发
                if now - _last_repeat.get(vk, 0) >= REPEAT_LOG_INTERVAL:
                    _last_repeat[vk] = now
                    emit("  ▼重复 VK=0x%02X（按住 %.0fms）"
                         % (vk, (now - _down_at[vk]) * 1000))
            else:
                _down_at[vk] = now
                _last_repeat[vk] = now
                emit("▼按下 VK=0x%02X sc=0x%02X flags=0x%X"
                     % (vk, kb.scanCode, kb.flags))
        elif w_param in (WM_KEYUP, WM_SYSKEYUP):
            held = (now - _down_at.pop(vk, now)) * 1000
            _last_repeat.pop(vk, None)
            emit("▲松开 VK=0x%02X 按住 %.0fms" % (vk, held))
    # 一律放行：这是只读观察，绝不能影响真实输入
    return u32.CallNextHookEx(None, n_code, w_param, l_param)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    proc = HOOKPROC(on_key)
    hook = u32.SetWindowsHookExW(WH_KEYBOARD_LL, proc, None, 0)
    if not hook:
        emit("安装钩子失败 err=%d" % ctypes.get_last_error())
        return 1
    emit("被动钩子已安装（只记录，不拦截）")
    emit("VK 对照提示：0xA6=浏览器后退 0xAC=浏览器主页 0x5D=菜单键 0x08=退格")
    emit("-" * 70)
    msg = wintypes.MSG()
    while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        u32.TranslateMessage(ctypes.byref(msg))
        u32.DispatchMessageW(ctypes.byref(msg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
