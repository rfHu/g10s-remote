"""G10S Pro 遥控器桥接：播放键 → 回车，麦克风键 → Ctrl+Win。

两个诉求的技术路径不同：
  · 播放/暂停：设备发消费者页 0x00CD，Windows 把它翻译成 VK 0xB3 再送进键盘事件流。
    要「不做播放、只发回车」，必须用低级键盘钩子把 0xB3 吃掉（Raw Input 只能看不能拦）。
  · 麦克风：设备发消费者页 0x00CF，Windows 直接丢弃，不产生任何键。
    只能靠 Raw Input 读到原始报文，再注入 Ctrl+Win（按住多久就保持多久）。

只读 Raw Input 的 Consumer Control（0x0C/0x01），不碰键盘页和鼠标页。

用法：
    python bridge.py                  # 正常跑
    python bridge.py --dry-run        # 只注册、只挂钩子，不注入任何按键
    python bridge.py --play additive  # 播放键改成叠加（保留原媒体功能）
    python bridge.py --no-play        # 关掉播放键映射
    python bridge.py --no-voice       # 关掉语音映射
"""

import argparse
import ctypes
import os
import queue
import re
import sys
import threading
import time
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateMutexW.restype = ctypes.c_void_p
k32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
k32.GetModuleHandleW.restype = ctypes.c_void_p
sendinput = u32.SendInput

# ---------------- 常量 ----------------
RIDEV_INPUTSINK = 0x00000100
RIDEV_NOLEGACY = 0x00000004   # 不让系统为该集合生成传统键鼠事件（消费者页默认行为一并关掉）
MAGIC_EXTRA = 0x47475331      # "GGS1"：给我们自己 SendInput 出来的事件打的标记
WM_INPUT = 0x00FF
WM_APP_KEYMAP = 0x8001        # 钩子把「命中的按键」转发给消息循环处理
WM_TRAYICON = 0x8002          # 托盘图标的回调消息
WM_COMMAND_ = 0x0111
MENU_OPEN_LOG = 1001
MENU_EXIT = 1002
MENU_AUTOSTART = 1003
MENU_KEY_MODE = 1004
MF_STRING, MF_CHECKED, MF_SEPARATOR = 0x0000, 0x0008, 0x0800
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "G10S-Remote"
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000007
WH_KEYBOARD_LL = 13
RIM_TYPEKEYBOARD = 1
RIM_TYPEHID = 2

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001

VK_LCONTROL = 0xA2
VK_LWIN = 0x5B
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_BROWSER_BACK = 0xA6        # 遥控器「返回」键被 Windows 翻译成的 VK
VK_APPS = 0x5D                # 遥控器「菜单/灯泡」键（Applications 键）
VK_CONTROL = 0x11
VK_MENU = 0x12                 # 通用 Alt（组合键用；Alt+Tab 那套用 VK_LMENU）
VK_K = 0x4B

# 「第一阶段」正在生效时，这些键不触发收尾：
# 修饰键本身（含我们自己注入的 Ctrl/Win/Alt）必须排除，否则一注入就自我打断
STAGE_KEEP_MODS = {0x10, 0x11, 0x12, 0x14, 0x5B, 0x5C,
                   0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}
# Alt+Tab 浮窗里要用左右/上下选窗口、Tab 轮换，这些不能触发收尾
ALT_TAB_KEEP = STAGE_KEEP_MODS | {0x09, 0x25, 0x26, 0x27, 0x28}
VK_MUTE = 0xAD                # 遥控器「静音」键（消费者页 0x00E2）
VK_UP = 0x26
VK_DOWN = 0x28
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120
WHEEL_MIN_INTERVAL = 0.09     # 滚轮最小间隔，防止按住时滚得飞快
KEY_MODE = "edit"             # "edit"=编辑模式（上/下=方向键）；"watch"=观看模式（上/下=滚轮）
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B

USAGE_PLAY_PAUSE = 0x00CD
USAGE_VOICE = 0x00CF
USAGE_OK = 0x0041          # 飞鼠关闭时 OK 键发的用途码（飞鼠开时它是鼠标左键）
USAGE_MUTE = 0x00E2        # 静音键在消费者页的用途码（与 VK 0xAD 是同一颗键）
USAGE_BACK = 0x0224        # AC Back（返回键，Windows 会翻译成 VK 0xA6）
USAGE_VOL_UP = 0x00E9
USAGE_VOL_DOWN = 0x00EA

# NOLEGACY 之后这些键不再有系统默认行为，全由本程序转发（或有意不转发）
VK_VOLUME_UP = 0xAF
VK_VOLUME_DOWN = 0xAE
CONSUMER_OWNED = False     # 消费者页是否已由本程序独占（main() 按参数设置）
_last_vol_log = 0.0

SIZEOF_RAWINPUTHEADER = 24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16
TRAY_TIP = "G10S 遥控器桥接：运行中"
_ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
_PAD = 32 if ctypes.sizeof(ctypes.c_void_p) == 8 else 24

# 语音键按住超过这个秒数就认为松开报文丢了，强制收尾，避免修饰键卡住
VOICE_WATCHDOG_SECS = 120.0
VK_LMENU = 0xA4
VK_TAB = 0x09
OK_ARM_TIMEOUT = 15.0      # Alt+Tab 浮窗最多保持这么久，防止忘了再按一次

ALT_TAB_ARMED = False
ALT_TAB_SINCE = 0.0

# 打包成 exe 后 __file__ 指向临时解包目录，日志必须落在 exe 旁边
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "bridge.log")
_logf = None


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    # --noconsole 打包后 sys.stdout 是 None，直接 print 会抛异常
    if sys.stdout is not None:
        try:
            print(line, flush=True)
        except Exception:
            pass
    if _logf:
        _logf.write(line + "\n")
        _logf.flush()


# ---------------- SendInput 封装 ----------------
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", _ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", _ULONG_PTR)]


class _UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("pad", ctypes.c_byte * _PAD)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]


sendinput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
sendinput.restype = wintypes.UINT


def _send(vk, up=False, ext=False, extra=0):
    # 关键：cbSize 必须等于 sizeof(INPUT)（x64 下 40），否则 SendInput 静默失败
    i = INPUT()
    i.type = INPUT_KEYBOARD
    i.u.ki.wVk = vk
    i.u.ki.wScan = 0
    i.u.ki.dwFlags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if ext else 0)
    i.u.ki.time = 0
    i.u.ki.dwExtraInfo = extra
    if sendinput(1, ctypes.byref(i), ctypes.sizeof(INPUT)) != 1:
        raise OSError(ctypes.get_last_error(), "SendInput 失败 vk=0x%02X" % vk)


def tap_right_click():
    """在光标当前位置发一次鼠标右键。"""
    INPUT_MOUSE = 0
    MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
    for flags in (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP):
        i = INPUT()
        i.type = INPUT_MOUSE
        i.u.mi.dx = 0
        i.u.mi.dy = 0
        i.u.mi.mouseData = 0
        i.u.mi.dwFlags = flags
        i.u.mi.time = 0
        i.u.mi.dwExtraInfo = 0
        if sendinput(1, ctypes.byref(i), ctypes.sizeof(INPUT)) != 1:
            raise OSError(ctypes.get_last_error(), "SendInput 鼠标右键失败")
        time.sleep(0.01)


def tap_chord(mods, vk):
    """按住若干修饰键再敲一个键，然后按反序松开。"""
    for m in mods:
        _send(m)
        time.sleep(0.012)
    _send(vk)
    time.sleep(0.015)
    _send(vk, up=True)
    time.sleep(0.012)
    for m in reversed(mods):
        _send(m, up=True)
        time.sleep(0.008)


# 源 VK -> (修饰键元组, 主键)。由 main() 按参数填充。
CHORD_ACTIONS = {}


def alt_tab_open():
    """打开 Alt+Tab 浮窗并保持：按住 Alt，点一下 Tab。

    浮窗之所以不消失，是因为 Alt 一直按着（不是 Tab）。
    Tab 若也按住不放，Windows 会认为它仍处于按下状态，方向键切换容易失效。
    """
    global ALT_TAB_ARMED, ALT_TAB_SINCE
    _send(VK_LMENU)
    time.sleep(0.03)
    _send(VK_TAB)
    time.sleep(0.03)
    _send(VK_TAB, up=True)
    ALT_TAB_ARMED = True
    ALT_TAB_SINCE = time.time()
    log("Alt+Tab 已打开：左右键切换窗口，再按 OK 确认")


def alt_tab_commit(reason=""):
    global ALT_TAB_ARMED
    if not ALT_TAB_ARMED:
        return False
    _send(VK_LMENU, up=True)
    ALT_TAB_ARMED = False
    log("Alt+Tab 已确认%s" % ("（%s）" % reason if reason else ""))
    return True


def alt_tab_watchdog():
    if ALT_TAB_ARMED and time.time() - ALT_TAB_SINCE > OK_ARM_TIMEOUT:
        alt_tab_commit("超时，自动确认")


_last_wheel = 0.0
_last_wheel_log = 0.0


def tap_wheel(delta):
    """发一格鼠标滚轮。带间隔节流：方向键按住会以约 30 次/秒重复，
    不节流的话滚屏会快到无法阅读。"""
    global _last_wheel
    now = time.time()
    if now - _last_wheel < WHEEL_MIN_INTERVAL:
        return False
    _last_wheel = now
    i = INPUT()
    i.type = 0                      # INPUT_MOUSE
    i.u.mi.dx = 0
    i.u.mi.dy = 0
    i.u.mi.mouseData = ctypes.c_ulong(delta & 0xFFFFFFFF).value
    i.u.mi.dwFlags = MOUSEEVENTF_WHEEL
    i.u.mi.time = 0
    i.u.mi.dwExtraInfo = 0
    if sendinput(1, ctypes.byref(i), ctypes.sizeof(INPUT)) != 1:
        raise OSError(ctypes.get_last_error(), "SendInput 滚轮失败")
    return True


def notify(title, text):
    """托盘气泡通知（主动弹出，几秒后自动消失）。

    气泡失败绝不能影响主逻辑，所以整段包在 try 里。
    """
    if TRAY_NID is None:
        return
    try:
        nid = NOTIFYICONDATAW()
        ctypes.pointer(nid)[0] = TRAY_NID      # 复制现有图标信息
        nid.uFlags = NIF_INFO
        nid.szInfoTitle = title
        nid.szInfo = text
        nid.dwInfoFlags = 0x1                  # NIIF_INFO
        nid.uVersion = 0
        if not shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)):
            log("气泡通知发送失败（err=%d）" % ctypes.get_last_error())
    except Exception as e:
        log("气泡通知异常: %r" % (e,))


def mode_text():
    return "编辑模式" if KEY_MODE == "edit" else "观看模式"


def tray_text():
    return "%s ｜ %s ｜ 自启动%s" % (
        TRAY_TIP, mode_text(), "开" if autostart_enabled() else "关")


def refresh_tray():
    """一次性刷新图标与悬停文案（模式切换、自启动开关都走这里）。"""
    if TRAY_NID is None:
        return
    TRAY_NID.hIcon = tray_icon_handle()
    TRAY_NID.szTip = tray_text()
    TRAY_NID.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(TRAY_NID))


def refresh_tray_text():
    refresh_tray()


def toggle_mode():
    global KEY_MODE
    KEY_MODE = "watch" if KEY_MODE == "edit" else "edit"
    txt = mode_text()
    log("已切换到：%s" % txt)
    refresh_tray()
    # 原来这里还会弹托盘气泡，但 Windows 11 已废弃传统气泡（实测从不显示），
    # 调用只是让 Shell_NotifyIcon 白跑一趟。模式指示统一交给图标变色 + 悬停文字。
    return txt


def preempt_stages(vk):
    """按下别的键时，先把处于「第一阶段」的切换型功能收尾。

    - 语音输入进行中  → 先结束语音（松开 Ctrl+Win），再让这个键照常生效
    - Alt+Tab 浮窗打开 → 先确认切换（松开 Alt），再让这个键照常生效
    修饰键不触发，否则我们自己注入的 Ctrl/Win/Alt 会立刻把刚打开的状态关掉。

    注意：本函数运行在低级钩子回调里，全程必须极快。
    这里只能调 release_fast（纯 keyup，不加锁不睡眠）——调带锁的 release
    会让回调卡到 300ms 以上，被系统静默摘除，整个钩子就此失效。
    """
    if VOICE.held and vk not in STAGE_KEEP_MODS:
        try:
            VOICE.release_fast("被其他按键打断")
        except Exception as e:
            # 兜底：无论如何必须把 Ctrl/Win 松开，否则整个键盘都会变成快捷键
            log("打断语音失败(%r)，强制松开修饰键" % (e,))
            VOICE._force_release("打断失败兜底")
    if ALT_TAB_ARMED and vk not in ALT_TAB_KEEP:
        alt_tab_commit("被其他按键打断")


def tap_key(vk, extra=0):
    _send(vk, extra=extra)
    time.sleep(0.01)
    _send(vk, up=True, extra=extra)


# 源 VK -> (种类, 目标 VK, 模式, 日志文案)。
# 种类 key=敲一个键，right_click=发鼠标右键；模式 replace=吃掉原键，additive=放行。
# 这张表由 main() 按命令行参数填充。
KEY_ACTIONS = {}

# 只吃掉原生行为、动作另走一条路的源键。
# 静音键归此类：它的切换动作改由消费者页报文（0x00E2）触发。
# 这样即使键盘钩子被系统摘除，模式切换依然可用 —— 钩子和 Raw Input 是两条独立通道，
# 让「切换模式」这条活路不要吊死在钩子上（钩子失效过一次就很难自己发现）。
INTERCEPT_ONLY = set()


# ---------------- 语音键状态 ----------------
class Voice:
    """管理 Ctrl+Win 的按下/松开，保证任何路径退出时都会释放。"""

    def __init__(self):
        self.held = False
        self.since = 0.0
        self.lock = threading.Lock()

    def press(self):
        if not self.lock.acquire(timeout=1.0):
            log("[voice] press 拿不到锁，跳过")
            return False
        try:
            if self.held:
                return False
            log("[voice] press 步骤1 发 Ctrl")
            _send(VK_LCONTROL)
            time.sleep(0.02)
            log("[voice] press 步骤2 发 Win")
            _send(VK_LWIN, ext=True)
            self.held = True
            self.since = time.time()
            log("语音输入开始：已注入 Ctrl+Win 按下（再按一次结束）")
            return True
        finally:
            self.lock.release()

    def release(self, reason=""):
        # 用带超时的加锁：万一另一条路径卡在锁上，也不能让「松开修饰键」这件事永远做不成
        if not self.lock.acquire(timeout=0.8):
            log("[voice] release 拿不到锁，跳过（%s）" % reason)
            return False
        try:
            if not self.held:
                log("[voice] release 时 held 已是 False（%s）" % reason)
                return False
            held_s = time.time() - self.since
            # 先置位、再松键，顺序不能反。
            # 松 Win 的那一瞬间微信输入法就开始把识别结果上屏，那些字符会逐个
            # 经过我们的低级钩子进入 preempt_stages；若此时 held 仍为 True，
            # 钩子里会再走一次释放并等待这把锁（最长 0.8s），回调一旦超过 300ms
            # 就会被 Windows 静默摘除 —— 之后播放/静音/菜单键全部失效，
            # 而语音键因为走 Raw Input 仍然可用，表现为「一半好一半坏」。
            self.held = False
            try:
                log("[voice] release 步骤1 松 Win")
                _send(VK_LWIN, up=True, ext=True)
                time.sleep(0.02)
                log("[voice] release 步骤2 松 Ctrl")
                _send(VK_LCONTROL, up=True)
            except Exception as e:
                # held 已置 False，不能让修饰键就此卡住，必须硬松开
                log("释放修饰键失败(%r)，强制松开" % (e,))
                self._force_release(reason)
                return True
            log("语音输入结束%s，共持续 %.1f 秒"
                % ("（%s）" % reason if reason else "", held_s))
            return True
        finally:
            try:
                self.lock.release()
            except RuntimeError:
                pass

    def release_fast(self, reason=""):
        """低级钩子专用：只发两个 keyup，不加锁、不 sleep。

        钩子回调必须在 300ms 内返回，超时会被系统静默摘除且不报任何错。
        「sleep(0.02) + 等锁 0.8s」的组合足以踩线，所以钩子路径改走这条极简实现。
        两条 keyup 本身是幂等的，即使和主路径重复也不会有副作用。
        """
        if not self.held:
            return False
        self.held = False
        for key, ext in ((VK_LWIN, True), (VK_LCONTROL, False)):
            try:
                _send(key, up=True, ext=ext)
            except Exception:
                pass
        log("语音输入结束（%s）" % reason)
        return True

    def _force_release(self, reason=""):
        """不走锁的兜底松开，异常也吞掉。"""
        self.held = False
        for key, ext in ((VK_LWIN, True), (VK_LCONTROL, False)):
            try:
                _send(key, up=True, ext=ext)
            except Exception:
                pass
        log("语音输入已强制结束（%s）" % reason)

    def watchdog(self):
        if self.held and time.time() - self.since > VOICE_WATCHDOG_SECS:
            self.release("超时保护")


VOICE = Voice()

# ---------------- 音频端点（读/写静音状态） ----------------
# 音量和静音的默认行为发生在 HID 驱动层，比键盘钩子更低一层：
# 钩子能收到它顺手生成的 VK 0xAD，却拦不住音量本身。
# 所以这里直接问音频端点，用来判断「这一次按键到底有没有真的静音」，
# 需要时用 API 直接复位 —— 不走按键事件，因此没有音量 OSD、也没有断音。
ole32 = ctypes.WinDLL("ole32", use_last_error=True)
ole32.CLSIDFromString.argtypes = (wintypes.LPCWSTR, ctypes.c_void_p)
ole32.CoCreateInstance.argtypes = (ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                   ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
COINIT_MULTITHREADED = 0x0


class _CA_GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8)]


def _ca_guid(text):
    g = _CA_GUID()
    ole32.CLSIDFromString(text, ctypes.byref(g))
    return g


def _vt_call(obj, index, restype, argtypes, *args):
    """按 vtable 序号调用 COM 方法（QueryInterface 记为 0）。"""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])
    return fn(obj, *args)


_TLS = threading.local()      # 每个线程一个音频端点，见 audio_endpoint 的说明
EXPECTED_MUTE = None          # 我们认为系统当前应有的静音状态


def audio_endpoint():
    """按线程各建一个音频端点。

    绝不能跨线程共用一个：IAudioEndpointVolume 是 STA 对象，主线程建好后
    交给后台线程调用会返回 RPC_E_WRONG_THREAD（0x8001010E），表现为
    「SetMute 偶发失败、音量没复位」。所以用 threading.local 各存一份。
    失败只试一次，避免每次按键都白跑一趟 COM。
    """
    p = getattr(_TLS, "vol", None)
    if p is not None:
        return p
    if getattr(_TLS, "failed", False):
        return None
    try:
        # 必须用 MTA：后台工作线程没有消息泵，STA 的跨公寓调用会间歇性失败
        # （RPC_E_WRONG_THREAD / 超时），表现就是「SetMute 有时成功有时失败」。
        ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        clsid = _ca_guid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")   # MMDeviceEnumerator
        iid_enum = _ca_guid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
        iid_vol = _ca_guid("{5CDF2C82-841E-4546-9722-0CF74078229A}")
        CLSCTX_ALL = 23
        p_enum = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, CLSCTX_ALL,
                                    ctypes.byref(iid_enum), ctypes.byref(p_enum))
        if hr != 0:
            raise OSError("CoCreateInstance hr=0x%08X" % (hr & 0xFFFFFFFF))
        p_dev = ctypes.c_void_p()
        hr = _vt_call(p_enum, 4, ctypes.c_long,        # GetDefaultAudioEndpoint(render, console)
                      (ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
                      0, 0, ctypes.byref(p_dev))
        if hr != 0:
            raise OSError("GetDefaultAudioEndpoint hr=0x%08X" % (hr & 0xFFFFFFFF))
        p_vol = ctypes.c_void_p()
        hr = _vt_call(p_dev, 3, ctypes.c_long,         # IMMDevice::Activate
                      (ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                       ctypes.POINTER(ctypes.c_void_p)),
                      ctypes.byref(iid_vol), CLSCTX_ALL, None, ctypes.byref(p_vol))
        if hr != 0:
            raise OSError("Activate hr=0x%08X" % (hr & 0xFFFFFFFF))
        for obj in (p_dev, p_enum):
            _vt_call(obj, 2, ctypes.c_ulong, ())       # Release
        _TLS.vol = p_vol
        log("音频端点已就绪（%s 线程）" % threading.current_thread().name)
    except Exception as e:
        _TLS.failed = True
        log("音频端点不可用（静音键不做恢复）：%r" % (e,))
    return getattr(_TLS, "vol", None)


def get_mute():
    p = audio_endpoint()
    if not p:
        return None
    m = wintypes.BOOL()
    hr = _vt_call(p, 15, ctypes.c_long, (ctypes.POINTER(wintypes.BOOL),), ctypes.byref(m))
    return bool(m.value) if hr == 0 else None


def set_mute(flag):
    p = audio_endpoint()
    if not p:
        return False
    # 注意签名是两个参数：SetMute(BOOL bMute, LPCGUID pguidEventContext)。
    # 早先只传了一个，第二个参数读到栈上的垃圾值，导致复位时好时坏。
    hr = _vt_call(p, 14, ctypes.c_long, (wintypes.BOOL, ctypes.c_void_p),
                  wintypes.BOOL(flag), None)
    if hr != 0:
        log("[audio] SetMute(%s) 失败 hr=0x%08X" % (flag, hr & 0xFFFFFFFF))
    return hr == 0


def refresh_expected_mute():
    """记录「按键前应有的静音状态」，只在启动时调用一次。

    运行期间**不做**轮询：任何在主线程上跑的阻塞调用（COM 尤其）都会让
    低级钩子的回调排队，超过系统阈值后这一次事件被跳过，VK 0xAD 直接漏给
    系统 —— 表现就是静音键偶尔真的静音并弹 OSD。

    代价是：用户运行期间用物理键盘/系统面板改过静音，这个基线会过期，
    下次按遥控器静音键时会恢复到过期的值（一次性偏差）。比起漏键让音量
    真的变化，这个代价可以接受。
    """
    global EXPECTED_MUTE
    if not audio_endpoint():
        return
    m = get_mute()
    if m is not None:
        EXPECTED_MUTE = m


CFG = None
HWND = None
TRAY_HWND = None
TRAY_NID = None
FLAG_QUIT = False
CLS_NAME = ctypes.c_wchar_p()


# ---------------- Raw Input ----------------
class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


u32.GetRawInputData.argtypes = (wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
                                ctypes.POINTER(wintypes.UINT), wintypes.UINT)
u32.GetRawInputData.restype = wintypes.UINT
u32.GetRawInputDeviceInfoW.argtypes = (wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
                                       ctypes.POINTER(wintypes.UINT))
u32.GetRawInputDeviceInfoW.restype = wintypes.UINT
u32.RegisterRawInputDevices.argtypes = (ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT)
u32.RegisterRawInputDevices.restype = wintypes.BOOL

LABEL_CACHE = {}


def device_is_target(handle_int):
    """按设备路径判断是不是这支遥控器。句柄一律用普通 int（c_void_p 不可哈希）。"""
    if handle_int in LABEL_CACHE:
        return LABEL_CACHE[handle_int]
    n = wintypes.UINT(0)
    u32.GetRawInputDeviceInfoW(handle_int, RIDI_DEVICENAME, None, ctypes.byref(n))
    ok = False
    if n.value:
        buf = ctypes.create_unicode_buffer(n.value + 2)
        if u32.GetRawInputDeviceInfoW(handle_int, RIDI_DEVICENAME, ctypes.byref(buf),
                                      ctypes.byref(n)):
            path = buf.value.lower()
            ok = bool(re.search(r"vid_%s&pid_%s" % (CFG.vid, CFG.pid), path))
            if not ok:
                log("忽略非目标设备: ...%s" % path[-40:])
    LABEL_CACHE[handle_int] = ok
    return ok


def parse_consumer(data):
    """消费者页报文 → usage。带 Report ID 时是 [id, lo, hi]。"""
    if len(data) >= 3:
        return data[1] | (data[2] << 8)
    if len(data) >= 2:
        return data[0] | (data[1] << 8)
    return 0


# ---------------- 后台任务队列 ----------------
# 静音键的处理（COM 读音量 + 托盘刷新 + 气泡）不能留在主线程：
# 低级键盘钩子的回调跑在主线程上，主线程一忙，钩子回调就开始排队，
# 超过系统阈值（默认 300ms）后 Windows 会跳过这一次钩子，VK 0xAD 直接漏给
# 系统 —— 表现就是「静音键偶尔真的静音并弹 OSD」。实测连续快速按两下时，
# 第二次必漏。所以主线程只往队列里丢一个任务就返回。
_WORK_Q = queue.Queue()


def handle_mute_key():
    """静音键的全部逻辑，跑在后台线程，绝不占用主线程。"""
    if CFG.silent_key != "mode":
        if CONSUMER_OWNED:
            tap_key(VK_MUTE, extra=MAGIC_EXTRA)
        log("静音键 → 原生静音")
        return
    if CFG.keep_mute_native:
        # 不拦 VK 0xAD：驱动层 toggle 一次（静音）、shell 处理键盘消息再 toggle 一次
        # （取消静音），正常会互相抵消、音量不动，只会闪一下 OSD。
        # 但系统偶尔忙，第二次没跟上就只剩一次 toggle、音量真的静音 —— 所以
        # 等两次都落地之后再兜底检查一遍，仍然是静音就补回来。
        before = EXPECTED_MUTE
        txt = toggle_mode()
        log("静音键 → 切换上/下键模式：现在%s" % txt)
        time.sleep(0.15)                 # 等两次 toggle 都落地（间隔约 40ms）
        m = get_mute()
        if before is not None and m is not None and m != before:
            if set_mute(before):
                log("  兜底：两次 toggle 未抵消（现在=%s），已恢复为 %s" % (m, before))
            else:
                log("  兜底：音量停在 %s，复位失败" % m)
        return
    before = EXPECTED_MUTE          # 按键前应有的状态
    actual = get_mute()             # 现在（可能已被驱动层改过）
    txt = toggle_mode()
    if not CONSUMER_OWNED and before is not None and actual is not None:
        if actual != before:
            # 驱动层改了 —— 说明这一次钩子没拦住（主线程刚好在忙）。
            # 用 API 直接设回原件，不经过按键事件。
            ok = set_mute(before)
            if not ok:
                # 音频服务偶尔会对紧接着的调用返回失败，等一下再试一次
                time.sleep(0.02)
                ok = set_mute(before)
            if ok:
                log("静音键：驱动层改动了静音（%s→%s），已用 API 静默复位"
                    % (actual, before))
            else:
                log("静音键：驱动层改动了静音，API 复位失败（音量已变）")
        else:
            log("静音键：驱动层未改动静音（钩子已拦住）")
    log("静音键 → 切换上/下键模式：现在%s" % txt)


def work_loop():
    while True:
        item = _WORK_Q.get()
        if item is None:
            return
        try:
            if item[0] == "mute":
                handle_mute_key()
        except Exception as e:
            log("后台任务出错: %r" % (e,))


def on_raw_input(lparam):
    global _last_vol_log
    size = wintypes.UINT(SIZEOF_RAWINPUTHEADER)
    u32.GetRawInputData(lparam, RID_INPUT, None, ctypes.byref(size), SIZEOF_RAWINPUTHEADER)
    if not size.value:
        return
    buf = ctypes.create_string_buffer(size.value)
    got = u32.GetRawInputData(lparam, RID_INPUT, ctypes.byref(buf),
                              ctypes.byref(size), SIZEOF_RAWINPUTHEADER)
    if got == 0 or got == 0xFFFFFFFF:
        return
    raw = bytes(bytearray(buf[:got]))
    if len(raw) < SIZEOF_RAWINPUTHEADER + 8:
        return

    dw_type = int.from_bytes(raw[0:4], "little")
    if dw_type != RIM_TYPEHID:
        return
    handle = int.from_bytes(raw[8:16], "little")
    if not device_is_target(handle):
        return

    n = int.from_bytes(raw[24:28], "little")      # dwSizeHid
    if n <= 0:
        return
    off = SIZEOF_RAWINPUTHEADER + 8
    if len(raw) < off + n:
        return
    data = raw[off:off + n]

    usage = parse_consumer(data)
    if CFG.debug:
        log("  收到报文 len=%d  %s  → usage=0x%04X" % (len(data), data.hex(" "), usage))
    if len(data) != 3:
        # 非消费者页格式（例如 17 字节的厂商私有报文），调试模式下只记录不处理
        return
    if usage == USAGE_VOICE:
        if CFG.voice:
            if CFG.voice_mode == "toggle":
                # 这支遥控器不会报告松手（按下后 100ms 就归零，之后静默），
                # 所以只能用两次按键划定起止：第一次开始，第二次结束。
                if VOICE.held:
                    VOICE.release("再次按下")
                else:
                    VOICE.press()
            else:
                VOICE.press()
    elif usage == 0:
        if CFG.voice_mode == "hold":
            VOICE.release()
    elif usage == USAGE_MUTE:
        # 只投递任务、立刻返回。这里跑在主线程上，任何耗时操作（COM、托盘、
        # 气泡）都会牵连正在排队的钩子回调，进而让下一次 VK 0xAD 漏给系统。
        _WORK_Q.put(("mute",))
    elif usage == USAGE_BACK:
        if not CONSUMER_OWNED or CFG.back == "off":
            if CFG.debug:
                log("返回键消费者页报文（未接管，交给系统）")
        elif KEY_MODE == "watch":
            tap_key(VK_BROWSER_BACK, extra=MAGIC_EXTRA)
            log("返回键 → 原生后退（观看模式）")
        elif CFG.back == "additive":
            tap_key(VK_BROWSER_BACK, extra=MAGIC_EXTRA)
            tap_key(VK_ESCAPE, extra=MAGIC_EXTRA)
            log("返回键 → 后退 + Esc（叠加）")
        else:
            tap_key(VK_ESCAPE, extra=MAGIC_EXTRA)
            log("返回键 → Esc")
    elif usage in (USAGE_VOL_UP, USAGE_VOL_DOWN):
        if not CONSUMER_OWNED:
            if CFG.debug:
                log("音量键消费者页报文（未接管，交给系统）")
        else:
            # NOLEGACY 把系统的音量默认行为一起关掉了，这里转发等价的原生 VK 补回来
            tap_key(VK_VOLUME_UP if usage == USAGE_VOL_UP else VK_VOLUME_DOWN,
                    extra=MAGIC_EXTRA)
            now = time.time()
            if now - _last_vol_log > 1.0:
                _last_vol_log = now
                log("音量%s" % ("+" if usage == USAGE_VOL_UP else "-"))
    elif usage == USAGE_OK:
        # 只会在飞鼠关闭时出现；飞鼠开启时这个键直接变成鼠标左键，走不到这里
        if CFG.ok_mode == "alt-tab":
            if ALT_TAB_ARMED:
                alt_tab_commit("再次按下")
            else:
                alt_tab_open()
        elif CFG.ok_mode == "enter":
            tap_key(VK_RETURN)
            log("OK 键 → 回车")
    elif usage == USAGE_PLAY_PAUSE:
        if not CONSUMER_OWNED:
            # 老路径：Windows 已把它翻译成 VK 0xB3，由键盘钩子拦截并改发。
            # 这里同时收到消费者页报文是正常的，不能重复处理，也不该报「未处理」。
            if CFG.debug:
                log("播放键消费者页报文（已由钩子处理）%s" % data.hex(" "))
        elif CFG.play == "none":
            pass
        elif KEY_MODE == "watch":
            # 观看模式恢复原生媒体控制；带标记注入，钩子认出来会直接放行
            tap_key(VK_MEDIA_PLAY_PAUSE, extra=MAGIC_EXTRA)
            log("播放键 → 原生媒体控制（观看模式）")
        elif CFG.play == "additive":
            tap_key(VK_MEDIA_PLAY_PAUSE, extra=MAGIC_EXTRA)
            tap_key(VK_RETURN, extra=MAGIC_EXTRA)
            log("播放键 → 回车（叠加，原生媒体也发）")
        else:
            tap_key(VK_RETURN, extra=MAGIC_EXTRA)
            log("播放键 → 回车")
    else:
        # 其余消费者码原样交给系统，只在调试时记录
        if CFG.debug:
            log("其他消费者码 0x%04X（未处理）" % usage)


# ---------------- 低级键盘钩子 ----------------
class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", _ULONG_PTR)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_hook_ref = None      # 必须留全局引用，否则被 GC 后钩子失效


def low_level_proc(n_code, w_param, l_param):
    if n_code == 0:
        kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        # 自己注入的事件带着 MAGIC_EXTRA 标记，一律原样放行。
        # 不加这一步会自噬：观看模式想「放行原生播放键」，注入的 0xB3 会被本钩子
        # 再拦一次变成回车；静音回拨注入的 0xAD 也会被自己吃掉。
        if kb.dwExtraInfo == MAGIC_EXTRA:
            return u32.CallNextHookEx(None, n_code, w_param, l_param)
        if w_param in (0x0100, 0x0104):        # 只在按下时判断，松开不重复触发
            if CFG.debug_keys:
                log("钩子看到按键 VK=0x%02X（录音中=%s，AltTab=%s）"
                    % (kb.vkCode, VOICE.held, ALT_TAB_ARMED))
            try:
                preempt_stages(kb.vkCode)
            except Exception as e:
                log("抢占逻辑出错: %r" % (e,))
        # 观看模式下播放键回归原生媒体控制，完全不拦
        if KEY_MODE == "watch" and kb.vkCode == VK_MEDIA_PLAY_PAUSE:
            return u32.CallNextHookEx(None, n_code, w_param, l_param)
        # 观看模式下返回键也是原生的（浏览器后退），不发 Esc
        if KEY_MODE == "watch" and kb.vkCode == VK_BROWSER_BACK:
            return u32.CallNextHookEx(None, n_code, w_param, l_param)
        # 观看模式下菜单/灯泡键也是原生的（AppsKey 弹焦点菜单）
        if KEY_MODE == "watch" and kb.vkCode == VK_APPS:
            return u32.CallNextHookEx(None, n_code, w_param, l_param)
        # 观看模式下，上/下键不发给系统，改成滚轮
        if KEY_MODE == "watch" and kb.vkCode in (VK_UP, VK_DOWN):
            if w_param in (0x0100, 0x0104) and HWND:
                u32.PostMessageW(HWND, WM_APP_KEYMAP, kb.vkCode, 0)
            return 1
        # 只吃掉原键、动作走另一条通道（静音键的切换由消费者页 0x00E2 触发）
        if kb.vkCode in INTERCEPT_ONLY:
            if CFG.debug_keys and w_param in (0x0100, 0x0104):
                # 钩子回调里写日志本身就是阻塞源，只在显式诊断时开
                log("钩子拦截 0x%02X（dwExtraInfo=0x%X）" % (kb.vkCode, kb.dwExtraInfo))
            return 1
        act = KEY_ACTIONS.get(kb.vkCode)
        if act is not None:
            # 不在这里做注入：钩子回调有 300ms 超时限制（超时会被系统静默摘除），
            # 转给消息循环处理，回调立刻返回。
            if w_param in (0x0100, 0x0104):        # WM_KEYDOWN / WM_SYSKEYDOWN
                if HWND:
                    u32.PostMessageW(HWND, WM_APP_KEYMAP, kb.vkCode, 0)
            # replace：吃掉原键，原功能收不到；additive：放行，原功能保留
            if act[2] == "replace":
                return 1
    return u32.CallNextHookEx(None, n_code, w_param, l_param)


# ---------------- 窗口与消息循环 ----------------
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)
u32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
u32.DefWindowProcW.restype = ctypes.c_ssize_t
# 完整声明签名：句柄类参数若走默认的 c_int，x64 下会被判成「int too long to convert」
u32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p)
u32.CreateWindowExW.restype = wintypes.HWND
u32.RegisterClassW.argtypes = (ctypes.c_void_p,)
u32.RegisterClassW.restype = wintypes.ATOM
# 必须声明 argtypes：c_int 装不下 64 位的 LPARAM，否则每个非 0xB3 的按键都会
# 在钩子里抛 OverflowError（异常返回 0，等于放行，表现为「能用但一直报错」）。
u32.CallNextHookEx.argtypes = (ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
u32.CallNextHookEx.restype = ctypes.c_ssize_t
u32.SetWindowsHookExW.restype = ctypes.c_void_p


def wndproc(hwnd, msg, wparam, lparam):
    global _last_wheel_log
    if msg == WM_TRAYICON:
        ev = lparam & 0xFFFF
        if ev == 0x0203:                 # WM_LBUTTONDBLCLK：双击打开日志
            open_log()
        elif ev == 0x0205:               # WM_RBUTTONUP：右键菜单
            show_tray_menu(hwnd)
        return 0
    if msg == WM_COMMAND_:               # 菜单项也可走 WM_COMMAND 投递
        handle_menu(wparam & 0xFFFF)
        return 0
    if msg == WM_INPUT:
        try:
            on_raw_input(lparam)
        except Exception as e:
            log("解析出错: %r" % (e,))
        return 0
    if msg == WM_APP_KEYMAP:
        if wparam in (VK_UP, VK_DOWN) and KEY_MODE == "watch":
            try:
                moved = tap_wheel(WHEEL_DELTA if wparam == VK_UP else -WHEEL_DELTA)
                if moved and time.time() - _last_wheel_log > 1.0:
                    _last_wheel_log = time.time()
                    log("上/下键 → 滚轮（观看模式）")
            except Exception as e:
                log("注入滚轮失败: %r" % (e,))
            return 0
        act = KEY_ACTIONS.get(wparam)
        if act is not None:
            kind, target, mode, text = act
            try:
                if kind == "right_click":
                    tap_right_click()
                elif kind == "toggle_mode":
                    toggle_mode()
                    return 0
                elif kind == "chord":
                    spec = CHORD_ACTIONS.get(wparam)
                    if spec:
                        tap_chord(spec[0], spec[1])
                else:
                    tap_key(target)
                log("%s%s" % (text, "（原键已拦截）" if mode == "replace"
                              else "（叠加，原功能保留）"))
            except Exception as e:
                log("注入失败（%s）: %r" % (text, e))
        return 0
    return u32.DefWindowProcW(hwnd, msg, wparam, lparam)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


def make_message_window():
    """注册普通窗口类，再创建一个 message-only 窗口（父窗口 HWND_MESSAGE=-3）。"""
    global _wndproc_ref
    _wndproc_ref = WNDPROC(wndproc)
    hinst = k32.GetModuleHandleW(None)
    cls = "G10SBridgeWnd_%d" % os.getpid()

    wc = WNDCLASSW()
    wc.style = 0
    wc.lpfnWndProc = _wndproc_ref
    wc.hInstance = hinst
    wc.lpszClassName = cls
    if not u32.RegisterClassW(ctypes.byref(wc)):
        raise OSError(ctypes.get_last_error(), "RegisterClassW 失败")
    CLS_NAME.value = cls

    HWND_MESSAGE = wintypes.HWND(-3)
    hwnd = u32.CreateWindowExW(0, cls, "bridge", 0, 0, 0, 0, 0,
                               HWND_MESSAGE, None, hinst, None)
    if not hwnd:
        raise OSError(ctypes.get_last_error(), "创建消息窗口失败")
    return hwnd


# ---------------- 托盘图标 ----------------
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD), ("guidItem", _GUID),
                ("hBalloonIcon", wintypes.HICON)]


shell32 = ctypes.WinDLL("shell32", use_last_error=True)
shell32.Shell_NotifyIconW.argtypes = (wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
u32.LoadIconW.restype = ctypes.c_void_p
u32.LoadIconW.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
u32.LoadImageW.restype = ctypes.c_void_p
u32.LoadImageW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR, wintypes.UINT,
                           ctypes.c_int, ctypes.c_int, wintypes.UINT)
u32.CreatePopupMenu.restype = ctypes.c_void_p
u32.AppendMenuW.argtypes = (ctypes.c_void_p, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR)
u32.TrackPopupMenu.argtypes = (ctypes.c_void_p, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, wintypes.HWND, ctypes.c_void_p)
u32.DestroyMenu.argtypes = (ctypes.c_void_p,)
u32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)


def make_tray_window():
    """托盘需要一个「普通」窗口（消息专用窗口收不到托盘回调）。

    建成隐藏的 WS_POPUP，从不 ShowWindow，因此不可见也不占任务栏。
    """
    hinst = k32.GetModuleHandleW(None)
    WS_POPUP = 0x80000000
    hwnd = u32.CreateWindowExW(0, CLS_NAME.value, "bridge-tray", WS_POPUP,
                               0, 0, 0, 0, None, None, hinst, None)
    if not hwnd:
        raise OSError(ctypes.get_last_error(), "创建托盘窗口失败")
    return hwnd


_TRAY_ICONS = {}      # 文件名 -> HICON，避免每次切模式都从磁盘读图标


def tray_icon_handle(icon_name=None):
    """按当前模式选图标：方向键=蓝（app.ico），滚轮=橙（app-wheel.ico）。

    找不到文件就退回系统默认图标，不影响功能。
    """
    if icon_name is None:
        icon_name = "app-watch.ico" if KEY_MODE == "watch" else "app-edit.ico"
    if icon_name in _TRAY_ICONS:
        return _TRAY_ICONS[icon_name]
    h = None
    bases = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(meipass)
    bases.append(BASE_DIR)
    for base in bases:
        path = os.path.join(base, icon_name)
        if os.path.exists(path):
            h = u32.LoadImageW(None, path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
            if h:
                break
    if not h:
        h = u32.LoadIconW(None, ctypes.c_void_p(32512))   # IDI_APPLICATION
    _TRAY_ICONS[icon_name] = h
    return h


def add_tray(hwnd):
    global TRAY_NID
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = hwnd
    nid.uID = 1
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = WM_TRAYICON
    nid.hIcon = tray_icon_handle()
    nid.szTip = TRAY_TIP
    if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
        raise OSError(ctypes.get_last_error(), "添加托盘图标失败")
    TRAY_NID = nid


def promote_tray_icon(tip):
    """把「我们自己这个」托盘图标设为始终显示。

    Windows 11 默认把新出现的图标塞进溢出区（^），用户看不到就等于没有。
    开关在 HKCU\\Control Panel\\NotifyIconSettings\\<条目>\\IsPromoted。

    匹配优先用 ExecutablePath（精确到我们这个 exe / pythonw），
    只有拿不到路径时才退回 tooltip —— 否则会命中同名的历史条目
    （比如以前 python 版的残留），改完仍然不显示。
    """
    try:
        import winreg
        base = r"Control Panel\NotifyIconSettings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as root:
            count = winreg.QueryInfoKey(root)[0]
            names = [winreg.EnumKey(root, i) for i in range(count)]
    except OSError:
        return False

    me = os.path.abspath(sys.executable).lower()
    by_exe = None
    by_tip = None
    for name in names:
        path = base + "\\" + name
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as sub:
                exe = None
                t = None
                try:
                    exe = str(winreg.QueryValueEx(sub, "ExecutablePath")[0]).lower()
                except OSError:
                    pass
                try:
                    t = str(winreg.QueryValueEx(sub, "InitialTooltip")[0])
                except OSError:
                    pass
                if exe and exe == me and by_exe is None:
                    by_exe = path
                elif t == tip and by_tip is None:
                    by_tip = path
        except OSError:
            continue

    target = by_exe or by_tip
    if target is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, target) as sub:
            try:
                if winreg.QueryValueEx(sub, "IsPromoted")[0] == 1:
                    return True
            except OSError:
                pass
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, target, 0, winreg.KEY_SET_VALUE) as w:
            winreg.SetValueEx(w, "IsPromoted", 0, winreg.REG_DWORD, 1)
        return True
    except OSError:
        return False


def promote_tray_async(hwnd):
    """图标刚加上时系统还没建注册表条目，后台重试几次再刷新生效。"""
    def worker():
        for _ in range(12):
            time.sleep(0.4)
            if promote_tray_icon(TRAY_TIP):
                # 让资源管理器重新读一次，否则要等下次启动才挪出溢出区
                try:
                    remove_tray()
                    time.sleep(0.15)
                    add_tray(hwnd)
                except Exception:
                    pass
                log("托盘图标已设为常驻显示（不在溢出区）")
                return
        log("托盘图标常驻设置未生效，可在「任务栏设置」里手动拖出")

    threading.Thread(target=worker, daemon=True).start()


def set_tray_tip(text):
    if TRAY_NID is None:
        return
    TRAY_NID.szTip = text
    TRAY_NID.uFlags = NIF_TIP
    shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(TRAY_NID))
    TRAY_NID.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP


def remove_tray():
    if TRAY_NID is not None:
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(TRAY_NID))


def open_log():
    try:
        os.startfile(LOG_PATH)
    except Exception as e:
        log("打开日志失败: %r" % (e,))


def autostart_command():
    """写进 Run 键的命令行。打包后就是 exe 自己；源码模式用 pythonw 跑桥接。"""
    if getattr(sys, "frozen", False):
        return '"%s"' % sys.executable
    pyw = sys.executable
    if pyw.lower().endswith("python.exe"):
        cand = pyw[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(cand):
            pyw = cand
    return '"%s" "%s"' % (pyw, os.path.join(BASE_DIR, "bridge.py"))


def autostart_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as k:
            val = winreg.QueryValueEx(k, AUTOSTART_NAME)[0]
        return bool(str(val).strip())
    except OSError:
        return False


def set_autostart(on):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, AUTOSTART_NAME, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, AUTOSTART_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError as e:
        log("设置开机自启动失败: %r" % (e,))
        return False


def show_tray_menu(hwnd):
    menu = u32.CreatePopupMenu()
    if not menu:
        return
    try:
        u32.AppendMenuW(menu, MF_STRING, MENU_OPEN_LOG, "打开日志")
        wheel_on = MF_CHECKED if KEY_MODE == "watch" else 0
        u32.AppendMenuW(menu, MF_STRING | wheel_on, MENU_KEY_MODE,
                        "上/下键用滚轮（现在是%s）" % mode_text())
        flags = MF_STRING | (MF_CHECKED if autostart_enabled() else 0)
        u32.AppendMenuW(menu, flags, MENU_AUTOSTART, "开机自启动")
        u32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        u32.AppendMenuW(menu, MF_STRING, MENU_EXIT, "退出")
        pt = wintypes.POINT()
        u32.GetCursorPos(ctypes.byref(pt))
        # 不先置前台，菜单点外面不会消失
        u32.SetForegroundWindow(hwnd)
        TPM_RIGHTBUTTON, TPM_NONOTIFY, TPM_RETURNCMD = 0x0002, 0x0080, 0x0100
        cmd = u32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_NONOTIFY | TPM_RETURNCMD,
                                 pt.x, pt.y, 0, hwnd, None)
        u32.PostMessageW(hwnd, 0, 0, 0)     # WM_NULL，让菜单正常关闭
        if cmd:
            handle_menu(cmd)
    finally:
        u32.DestroyMenu(menu)


def handle_menu(cmd):
    global FLAG_QUIT
    if cmd == MENU_OPEN_LOG:
        open_log()
    elif cmd == MENU_KEY_MODE:
        toggle_mode()
    elif cmd == MENU_AUTOSTART:
        want = not autostart_enabled()
        if set_autostart(want):
            log("开机自启动：%s" % ("已开启" if want else "已关闭"))
            refresh_tray()
    elif cmd == MENU_EXIT:
        log("从托盘菜单退出")
        FLAG_QUIT = True


def register_raw_input(hwnd, pages=None, nolegacy_pages=()):
    """逐个注册，单个页面失败不影响其他页面。

    nolegacy_pages 里的集合会加 RIDEV_NOLEGACY：系统不再为它生成任何传统键鼠事件 ——
    这是让「静音键不真的静音」的唯一办法。音量/静音的默认行为发生在 HID 驱动层，
    比键盘钩子更低一层，钩子只能拦到它顺手生成的 VK 0xAD，拦不住音量的实际变化。

    注意 NOLEGACY 是全局生效的（对所有进程），所以只给确定要独占的集合加：
    消费者页归我们，系统控制页/私有页只是调试旁听，绝不能加。

    返回：消费者页是否成功独占。
    """
    if pages is None:
        pages = [(0x0C, 0x01)]
    consumer_ok = True
    for page, usage in pages:
        dev = RAWINPUTDEVICE()
        dev.usUsagePage = page
        dev.usUsage = usage
        dev.dwFlags = RIDEV_INPUTSINK          # 后台接收，窗口不必在前台
        owned = (page, usage) in nolegacy_pages
        if owned:
            dev.dwFlags |= RIDEV_NOLEGACY
        dev.hwndTarget = hwnd
        ok = u32.RegisterRawInputDevices(ctypes.byref(dev), 1, ctypes.sizeof(RAWINPUTDEVICE))
        if ok:
            log("已注册监听 0x%04X/0x%04X%s"
                % (page, usage, "（NOLEGACY，原生行为由本程序接管）" if owned else ""))
        else:
            log("注册 0x%04X/0x%04X 失败（err=%d）" % (page, usage, ctypes.get_last_error()))
            if owned:
                consumer_ok = False
    return consumer_ok


def install_hook():
    global _hook_ref
    _hook_ref = HOOKPROC(low_level_proc)
    h = u32.SetWindowsHookExW(WH_KEYBOARD_LL, _hook_ref, None, 0)
    if not h:
        raise OSError(ctypes.get_last_error(), "SetWindowsHookEx 失败")
    return h


def main():
    global CFG, HWND, TRAY_HWND, FLAG_QUIT, _logf, CONSUMER_OWNED

    ap = argparse.ArgumentParser()
    ap.add_argument("--vid", default="1915")
    ap.add_argument("--pid", default="1025")
    ap.add_argument("--play", choices=["replace", "additive", "none"], default="replace")
    ap.add_argument("--debug-keys", dest="debug_keys", action="store_true",
                    default=False, help="在日志里打印钩子看到的每个按键（排查用）")
    ap.add_argument("--silent-key", dest="silent_key", choices=["mode", "mute"],
                    default="mode",
                    help="静音键：mode=切换上/下键模式（默认）；mute=保留原生静音")
    ap.add_argument("--ok", dest="ok_mode", choices=["alt-tab", "enter", "off"],
                    default="alt-tab",
                    help="飞鼠关闭时的 OK 键：alt-tab=窗口切换；enter=回车；off=不处理")
    ap.add_argument("--menu", dest="menu_key", choices=["search", "right", "keep"],
                    default="search",
                    help="菜单/灯泡键：search=编辑模式发 Ctrl+Alt+K、观看模式保持原生（默认）；"
                         "right=固定发鼠标右键；keep=完全不接管")
    ap.add_argument("--back", choices=["esc", "additive", "off"], default="esc",
                    help="返回键：esc=编辑模式发 Esc（默认，观看模式仍后退）；"
                         "additive=两者都发；off=不接管")
    ap.add_argument("--voice", dest="voice", action="store_true", default=True)
    ap.add_argument("--no-voice", dest="voice", action="store_false")
    ap.add_argument("--voice-mode", choices=["toggle", "hold"], default="toggle",
                    help="toggle=按一下开始再按一下结束（本机唯一可靠方式）；hold=按住")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--debug", action="store_true", help="打印每一条消费者页报文")
    ap.add_argument("--allow-multi", action="store_true", help="跳过单实例检查（调试用）")
    ap.add_argument("--no-tray", action="store_true", help="不显示托盘图标")
    ap.add_argument("--own", action="store_true",
                    help="尝试独占消费者页（RIDEV_NOLEGACY）。实测这台复合设备会被系统拒绝"
                         "（err=1004），保留开关便于换设备时验证")
    ap.add_argument("--intercept-mute", dest="keep_mute_native", action="store_false",
                    help="拦截 VK 0xAD，只留驱动层一次 toggle 再由 API 复位。"
                         "默认不拦：让驱动层与 shell 各 toggle 一次互相抵消")
    CFG = ap.parse_args()

    _logf = open(LOG_PATH, "a", encoding="utf-8")
    log("=" * 50)
    back_desc = {"esc": "Esc(编辑)/原生(观看)", "additive": "Esc+后退", "off": "不接管"}[CFG.back]
    log("启动：设备 %s:%s  播放键=%s  返回键=%s  OK键=%s  语音键=%s（%s）%s%s"
        % (CFG.vid.upper(), CFG.pid.upper(), CFG.play, back_desc, CFG.ok_mode,
           "开" if CFG.voice else "关", CFG.voice_mode,
           "  [dry-run]" if CFG.dry_run else "",
           "  [own]" if CFG.own else ""))

    # 组装映射表：源 VK -> (目标 VK, 模式, 日志文案)
    if CFG.play != "none":
        KEY_ACTIONS[VK_MEDIA_PLAY_PAUSE] = ("key", VK_RETURN, CFG.play, "播放键 → 回车")
    if CFG.back != "off":
        # 编辑模式下替换原生后退；观看模式由钩子直接放行，走不到这里
        back_mode = "replace" if CFG.back == "esc" else "additive"
        KEY_ACTIONS[VK_BROWSER_BACK] = ("key", VK_ESCAPE, back_mode, "返回键 → Esc")
    if CFG.silent_key == "mode" and not CFG.keep_mute_native:
        # 动作改由消费者页 0x00E2 触发（见 on_raw_input），钩子这边只负责吃掉原生 0xAD
        INTERCEPT_ONLY.add(VK_MUTE)
    if CFG.menu_key == "right":
        KEY_ACTIONS[VK_APPS] = ("right_click", 0, "replace", "菜单键 → 鼠标右键")
    elif CFG.menu_key == "search":
        # 只在编辑模式生效：观看模式由钩子直接放行
        KEY_ACTIONS[VK_APPS] = ("chord", 0, "replace", "菜单键 → Ctrl+Alt+K（搜索）")
        CHORD_ACTIONS[VK_APPS] = ((VK_CONTROL, VK_MENU), VK_K)

    # 单实例保护：两份进程同时跑会让每次按键被注入两次
    if not CFG.allow_multi:
        mutex = k32.CreateMutexW(None, False, "Global\\G10SBridgeMutex")
        if not mutex:
            log("创建互斥体失败（err=%d），继续启动" % ctypes.get_last_error())
        elif ctypes.get_last_error() == 183:      # ERROR_ALREADY_EXISTS
            log("已有一个实例在运行，本次退出。要强制再起一个请加 --allow-multi")
            if _logf:
                _logf.close()
            return 0

    HWND = make_message_window()
    pages = [(0x0C, 0x01)]
    if CFG.debug:
        # 调试时顺带监听厂商私有页与系统控制页，看开麦期间有没有其他报文
        # （这两页只旁听，绝不加 NOLEGACY：那会连系统对电源键的处理一起关掉）
        pages += [(0xFF01, 0x01), (0x01, 0x80)]
    owned = [(0x0C, 0x01)] if CFG.own else []
    consumer_ok = register_raw_input(HWND, pages, owned)
    CONSUMER_OWNED = bool(owned) and consumer_ok
    if CONSUMER_OWNED:
        log("消费者页已独占：播放键、返回键、音量键、静音键都不再有系统默认行为")
        # 独占后遥控器的静音键不再产生 VK 0xAD，钩子这边没必要再拦；
        # 留着反而会把物理键盘的静音键吃掉。
        INTERCEPT_ONLY.discard(VK_MUTE)
    elif CFG.own:
        log("警告：消费者页独占未生效，已退回钩子模式（静音键靠事后回拨）")

    if not CFG.no_tray:
        try:
            TRAY_HWND = make_tray_window()
            add_tray(TRAY_HWND)
            log("托盘图标已显示（右键可退出，双击打开日志）")
            refresh_tray_text()
            promote_tray_async(TRAY_HWND)
        except Exception as e:
            log("托盘图标创建失败（不影响按键功能）：%r" % (e,))

    # 建立静音状态基线：否则第一次按静音键时，没法和「按键前」比对
    audio_endpoint()
    refresh_expected_mute()

    # 静音键的处理放到后台线程：主线程必须随时能立刻响应钩子回调
    threading.Thread(target=work_loop, name="mute-worker", daemon=True).start()

    hook = None
    if KEY_ACTIONS or INTERCEPT_ONLY:
        hook = install_hook()
        for src, (kind, target, mode, text) in KEY_ACTIONS.items():
            if kind == "right_click":
                dst = "鼠标右键"
            elif kind == "toggle_mode":
                dst = "切换上/下键模式"
            elif kind == "chord":
                dst = "Ctrl+Alt+K"
            else:
                dst = "0x%02X" % target
            log("已映射 0x%02X → %s  %s（%s）" % (src, dst, text, mode))
        for src in sorted(INTERCEPT_ONLY):
            log("已拦截 0x%02X（吃掉原生键，动作由消费者页报文触发）" % src)
        log("当前模式：%s" % mode_text())

    if CFG.dry_run:
        log("dry-run：不注入任何按键，3 秒后退出")
        time.sleep(3)
    else:
        log("就绪。右键托盘图标可退出。")

        msg = wintypes.MSG()
        try:
            while True:
                if FLAG_QUIT:
                    break
                while u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    if msg.message == 0x0012:      # WM_QUIT
                        FLAG_QUIT = True
                        break
                    u32.TranslateMessage(ctypes.byref(msg))
                    u32.DispatchMessageW(ctypes.byref(msg))
                VOICE.watchdog()
                alt_tab_watchdog()
                # 注意：这里绝不能放任何可能阻塞的调用（COM、IO、等待）。
                # 低级键盘钩子的回调跑在本线程上，主线程一阻塞，钩子回调就排队，
                # 超过系统阈值（默认 300ms）后这一次事件会被跳过，VK 0xAD 直接漏给
                # 系统 —— 表现就是「静音键偶尔真的静音并弹 OSD」。
                time.sleep(0.004)
        except KeyboardInterrupt:
            log("收到退出信号")

    # 收尾：一定要释放修饰键，否则 Win 会卡在按下状态
    _WORK_Q.put(None)
    alt_tab_commit("退出")
    VOICE.release("退出")
    remove_tray()
    if hook:
        u32.UnhookWindowsHookEx(hook)
    for h in (TRAY_HWND, HWND):
        if h:
            u32.DestroyWindow(h)
    log("已退出")
    if _logf:
        _logf.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
