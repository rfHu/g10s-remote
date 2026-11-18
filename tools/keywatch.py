# -*- coding: utf-8 -*-
"""
keywatch.py —— 实时显示「你按下的这个键，在系统里到底是什么」

为什么不用键盘钩子：
    键盘钩子只能看到已经被 Windows 映射成键盘按键的东西。遥控器上那些
    「按了没反应」的键，往往压根没进键盘事件流（走的是消费者页或厂商私有页），
    钩子永远看不到 —— 于是你无法区分「这键没发任何东西」和「发了但系统不认」。
    本脚本读 Raw Input，能在驱动层就看到原始报文，两种情况都能分辨。

用法（在本目录下打开终端）：
    python keywatch.py                    # 监控全部 HID 设备
    python keywatch.py --only 1915:1025   # 只看这支飞鼠接收器
    python keywatch.py --raw              # 连按住不放的自动重复也打印
    python keywatch.py --secs 60          # 60 秒后自动退出

输出长这样：
    [14:03:22.417] 1915:1025/MI_02  键盘页      ▼按下  Enter            VK=0x0D sc=0x1C
    [14:03:22.560] 1915:1025/MI_02  键盘页      ▲松开  Enter            VK=0x0D sc=0x1C
    [14:03:23.001] 1915:1025/MI_03  消费者页    ▼按下  音量加           usage=0x00E9
    [14:03:25.884] 1915:1025/MI_03  厂商私有页  报文变化  字节[3] 00→01

结果同时写入 keywatch.log。
"""

import ctypes
import ctypes.wintypes as wt
import os
import re
import sys
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)

WM_INPUT = 0x00FF
RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000007
RIDI_DEVICEINFO = 0x2000000B
RIM_TYPEHID = 2
RIDEV_INPUTSINK = 0x00000100
RI_KEY_BREAK = 0x01
RI_KEY_E0 = 0x02

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10
GUID_DEVINTERFACE_HID = "{4D1E55B2-F16F-11CF-88CB-001111000030}"

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

RAW = "--raw" in sys.argv
MOTION = "--motion" in sys.argv    # 额外打印鼠标位移（默认不打印，避免刷屏）

# ============================ 名字表 ============================

VK_NAMES = {
    0x03: "Cancel", 0x08: "Backspace", 0x09: "Tab", 0x0C: "Clear", 0x0D: "Enter",
    0x10: "Shift", 0x11: "Ctrl", 0x12: "Alt", 0x13: "Pause", 0x14: "CapsLock",
    0x1B: "Esc", 0x20: "空格", 0x21: "PageUp", 0x22: "PageDown", 0x23: "End",
    0x24: "Home", 0x25: "←", 0x26: "↑", 0x27: "→", 0x28: "↓", 0x29: "Select",
    0x2C: "PrintScreen", 0x2D: "Insert", 0x2E: "Delete", 0x2F: "Help",
    0x5B: "左Win", 0x5C: "右Win", 0x5D: "菜单键(AppsKey)",
    0x5F: "Sleep", 0x90: "NumLock", 0x91: "ScrollLock",
    0xA0: "左Shift", 0xA1: "右Shift", 0xA2: "左Ctrl", 0xA3: "右Ctrl",
    0xA4: "左Alt", 0xA5: "右Alt",
    0xA6: "浏览器后退", 0xA7: "浏览器前进", 0xA8: "浏览器刷新", 0xA9: "浏览器停止",
    0xAA: "浏览器搜索", 0xAB: "浏览器收藏", 0xAC: "浏览器主页",
    0xAD: "静音", 0xAE: "音量-", 0xAF: "音量+",
    0xB0: "下一曲", 0xB1: "上一曲", 0xB2: "停止", 0xB3: "播放/暂停",
    0xB4: "邮件", 0xB5: "媒体选择", 0xB6: "APP1", 0xB7: "APP2",
    0xBA: "分号", 0xBB: "等号", 0xBC: "逗号", 0xBD: "减号", 0xBE: "句号",
    0xBF: "斜杠", 0xC0: "反引号", 0xDB: "左方括号", 0xDC: "反斜杠",
    0xDD: "右方括号", 0xDE: "引号", 0xFF: "伪键(仅用于修饰键)",
}

# HID 键盘页(0x07) usage → 名字
HID_KEY = {
    0x28: "Enter", 0x29: "Esc", 0x2A: "Backspace", 0x2B: "Tab", 0x2C: "空格",
    0x2D: "-", 0x2E: "=", 0x2F: "[", 0x30: "]", 0x31: "\\", 0x32: "非US-#",
    0x33: ";", 0x34: "'", 0x35: "`", 0x36: ",", 0x37: ".", 0x38: "/",
    0x39: "CapsLock", 0x46: "PrintScreen", 0x47: "ScrollLock", 0x48: "Pause",
    0x49: "Insert", 0x4A: "Home", 0x4B: "PageUp", 0x4C: "Delete", 0x4D: "End",
    0x4E: "PageDown", 0x4F: "→", 0x50: "←", 0x51: "↓", 0x52: "↑",
    0xE0: "左Ctrl", 0xE1: "左Shift", 0xE2: "左Alt", 0xE3: "左Win",
    0xE4: "右Ctrl", 0xE5: "右Shift", 0xE6: "右Alt", 0xE7: "右Win",
}

# 消费者页(0x0C) usage → 名字
CONSUMER = {
    0x0030: "电源", 0x0032: "睡眠", 0x0040: "菜单",
    0x0041: "菜单选择(Menu Pick) = OK键",
    0x0042: "菜单上", 0x0043: "菜单下", 0x0044: "菜单左", 0x0045: "菜单右",
    0x0046: "菜单退出",
    0x00B0: "播放", 0x00B1: "暂停",
    0x00B2: "录制", 0x00B3: "快进", 0x00B4: "快退", 0x00B5: "下一曲",
    0x00B6: "上一曲", 0x00B7: "停止", 0x00B8: "弹出",
    0x00CD: "播放/暂停", 0x00CF: "语音指令(Voice Command) ★",
    0x00E2: "静音", 0x00E9: "音量+", 0x00EA: "音量-",
    0x0183: "媒体启动", 0x0221: "搜索", 0x0223: "主页", 0x0224: "返回",
    0x0225: "前进", 0x0226: "停止", 0x0227: "刷新", 0x022A: "书签",
    0x006F: "亮度+", 0x0070: "亮度-", 0x01A6: "媒体播放器",
}

# 实测：Windows 只对其中一部分消费者用途码做了翻译，翻成对应 VK 后再发一条
# hDevice=0 的键盘事件。表里没有的（尤其 0x0041 OK 键、0x00CF 麦克风键）
# Windows 完全不处理，等于「按了毫无反应」，只能靠 Raw Input 才看得到。
CONSUMER_VK = {
    0x00CD: (0xB3, "播放/暂停"),
    0x00E2: (0xAD, "静音"),
    0x00E9: (0xAF, "音量+"),
    0x00EA: (0xAE, "音量-"),
    0x0224: (0xA6, "浏览器后退"),
    0x0223: (0xA4, "浏览器主页"),   # 规范如此，你这支未实测到
    0x0225: (0xA7, "浏览器前进"),
}


def consumer_verdict(u):
    """这个用途码 Windows 会不会翻译成键盘键？返回一句可读结论。"""
    kv = CONSUMER_VK.get(u)
    if kv:
        return "Windows→VK 0x%02X(%s)" % kv
    return "Windows 不翻译→无任何键"


def hid_key_name(u):
    if 0x04 <= u <= 0x1D:
        return chr(ord("a") + u - 0x04)
    if 0x1E <= u <= 0x26:
        return chr(ord("1") + u - 0x1E)
    if u == 0x27:
        return "0"
    if 0x3A <= u <= 0x45:
        return "F%d" % (u - 0x3A + 1)
    return HID_KEY.get(u, "usage=0x%02X" % u)


def vk_name(vk, scan):
    if vk in VK_NAMES:
        return VK_NAMES[vk]
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x60 <= vk <= 0x69:
        return "小键盘%d" % (vk - 0x60)
    if 0x70 <= vk <= 0x87:
        return "F%d" % (vk - 0x70 + 1)
    return "[未命名] 扫描码=0x%02X" % scan


# ============================ 输出 ============================
LOGF = None
T0 = time.time()


def out(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S") + (".%03d" % int((time.time() % 1) * 1000)), msg)
    try:
        print(line, flush=True)
    except Exception:
        pass
    if LOGF:
        LOGF.write(line + "\n")
        LOGF.flush()


# ============================ HID 集合发现 ============================

class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD),
                ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wt.DWORD), ("Reserved", ctypes.c_void_p)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", wt.USHORT), ("UsagePage", wt.USHORT),
                ("InputReportByteLength", wt.USHORT), ("OutputReportByteLength", wt.USHORT),
                ("FeatureReportByteLength", wt.USHORT), ("Reserved", wt.USHORT * 17),
                ("NumberLinkCollectionNodes", wt.USHORT), ("NumberInputButtonCaps", wt.USHORT),
                ("NumberInputValueCaps", wt.USHORT), ("NumberInputDataIndices", wt.USHORT),
                ("NumberOutputButtonCaps", wt.USHORT), ("NumberOutputValueCaps", wt.USHORT),
                ("NumberOutputDataIndices", wt.USHORT), ("NumberFeatureButtonCaps", wt.USHORT),
                ("NumberFeatureValueCaps", wt.USHORT), ("NumberFeatureDataIndices", wt.USHORT)]


def discover_collections(vid, pid):
    """枚举该 VID/PID 的 HID 顶级集合，返回 {(page, usage)}"""
    found = set()
    setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                              ctypes.c_void_p, wt.DWORD]
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wt.DWORD,
        ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
        ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, wt.DWORD, wt.DWORD,
                                     ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    hid.HidD_GetPreparsedData.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
    hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]

    g = GUID()
    u = __import__("uuid").UUID(GUID_DEVINTERFACE_HID)
    g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
    for i in range(8):
        g.Data4[i] = u.bytes[8 + i]
    hdev = setupapi.SetupDiGetClassDevsW(ctypes.byref(g), None, None,
                                         DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if not hdev or hdev == 0xFFFFFFFFFFFFFFFF:
        return found
    did = SP_DEVICE_INTERFACE_DATA()
    did.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
    idx = 0
    while setupapi.SetupDiEnumDeviceInterfaces(hdev, None, ctypes.byref(g), idx, ctypes.byref(did)):
        idx += 1
        need = wt.DWORD(0)
        setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(did), None, 0,
                                                  ctypes.byref(need), None)
        if not need.value:
            continue
        buf = ctypes.create_string_buffer(need.value)
        cb = wt.DWORD(8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6)
        ctypes.memmove(buf, ctypes.byref(cb), 4)
        if not setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(did), buf,
                                                         need.value, ctypes.byref(need), None):
            continue
        path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
        m = re.search(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})", path, re.I)
        if not m:
            continue
        if int(m.group(1), 16) != vid or int(m.group(2), 16) != pid:
            continue
        h = kernel32.CreateFileW(path, 0, 3, None, 3, 0, None)
        if not h or h == 0xFFFFFFFFFFFFFFFF:
            continue
        caps = HIDP_CAPS()
        pp = ctypes.c_void_p()
        if hid.HidD_GetPreparsedData(h, ctypes.byref(pp)):
            if hid.HidP_GetCaps(pp, ctypes.byref(caps)) == 0x00110000:  # HIDP_STATUS_SUCCESS
                found.add((caps.UsagePage, caps.Usage))
            hid.HidD_FreePreparsedData(pp)
        kernel32.CloseHandle(h)
    return found


# ============================ Raw Input ============================

class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wt.USHORT), ("usUsage", wt.USHORT),
                ("dwFlags", wt.DWORD), ("hwndTarget", wt.HWND)]


HWND_G = None
ONLY = None            # (vid, pid) 或 None
ERR_COUNT = 0
LABEL_CACHE = {}
KEY_STATE = {}         # (dev, vk) -> 上次是否按下
HID_LAST = {}          # dev -> 上次报文 bytes
REPEAT_AT = {}         # (dev, vk) -> 上次打印时间


def dev_label(handle):
    if handle == 0:
        # hDevice=0 表示这条事件不是硬件直报，而是 Windows 由消费者页用法
        # 翻译出来的「合成键盘事件」（音量键就是这类，所以会看到两遍）。
        return "系统合成事件"
    if handle in LABEL_CACHE:
        return LABEL_CACHE[handle]
    n = wt.UINT(0)
    user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, None, ctypes.byref(n))
    label = "未知设备"
    if n.value:
        buf = ctypes.create_unicode_buffer(n.value + 2)
        user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, ctypes.byref(buf),
                                      ctypes.byref(n))
        path = buf.value
        m = re.search(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})", path, re.I)
        if m:
            label = "%s:%s" % (m.group(1).upper(), m.group(2).upper())
            mi = re.search(r"MI_([0-9A-Fa-f]{2})", path, re.I)
            if mi:
                label += "/MI_%s" % mi.group(1).upper()
        else:
            label = os.path.basename(path)[:24]
    LABEL_CACHE[handle] = label
    return label


def tlc_of(handle):
    """返回 (usagePage, usage)，失败返回 (0,0)"""
    n = wt.UINT(32)
    buf = ctypes.create_string_buffer(32)
    cb = wt.DWORD(32)
    ctypes.memmove(ctypes.byref(buf), ctypes.byref(cb), 4)   # RID_DEVICE_INFO.cbSize 必须先填
    r = user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICEINFO, ctypes.byref(buf),
                                      ctypes.byref(n))
    if r in (0, 0xFFFFFFFF):
        return (0, 0)
    raw = bytes(bytearray(buf[:32]))
    if len(raw) < 24:
        return (0, 0)
    # RID_DEVICE_INFO: cbSize(4) dwType(4) union: hid{dwVendorId,dwProductId,dwVersionNumber,usUsagePage,usUsage}
    page = int.from_bytes(raw[20:22], "little")
    usage = int.from_bytes(raw[22:24], "little")
    return (page, usage)


def page_tag(page, usage):
    if page == 0x01 and usage == 0x06:
        return "键盘页"
    if page == 0x01 and usage == 0x02:
        return "鼠标页"
    if page == 0x01 and usage == 0x80:
        return "系统控制页"
    if page == 0x0C:
        return "消费者页"
    if 0xFF00 <= page <= 0xFFFF:
        return "厂商私有页(0x%04X)" % page
    return "HID 0x%04X/0x%04X" % (page, usage)


# RAWMOUSE.usButtonFlags 是「状态变化标志」，不是状态位图：
# 0x0001 左键按下 / 0x0002 左键松开 / 0x0004 右键按下 / 0x0008 右键松开 ...
MOUSE_TRANS = [
    (0x0001, "鼠标左键", "按下"), (0x0002, "鼠标左键", "松开"),
    (0x0004, "鼠标右键", "按下"), (0x0008, "鼠标右键", "松开"),
    (0x0010, "中键", "按下"), (0x0020, "中键", "松开"),
    (0x0040, "侧键1", "按下"), (0x0080, "侧键1", "松开"),
    (0x0100, "侧键2", "按下"), (0x0200, "侧键2", "松开"),
    (0x0400, "滚轮", ""), (0x0800, "横向滚轮", ""),
]


def handle_mouse(dev, raw):
    flags = int.from_bytes(raw[28:30], "little")
    if not flags:
        # 无按键变化时，--motion 下把位移打出来：dx 在 36..39，dy 在 40..43
        # （缓冲区前 24 字节是 RAWINPUTHEADER：dwType/dwSize/hDevice/wParam）
        if MOTION and len(raw) >= 44:
            lx = int.from_bytes(raw[36:40], "little", signed=True)
            ly = int.from_bytes(raw[40:44], "little", signed=True)
            if lx or ly:
                out("%-22s %s  位移 dx=%+d dy=%+d" % (dev_label(dev), "鼠标页", lx, ly))
        return
    data = int.from_bytes(raw[30:32], "little", signed=True)  # 滚轮增量
    parts = []
    for bit, name, act in MOUSE_TRANS:
        if flags & bit:
            if bit in (0x0400, 0x0800):
                parts.append("%s %+d" % (name, data))
            else:
                parts.append("%s %s" % (name, act))
    if parts:
        out("%-22s %s  %s" % (dev_label(dev), "鼠标页", "   ".join(parts)))


def handle_keyboard(dev, raw):
    off = 24
    make = int.from_bytes(raw[off:off + 2], "little")
    flags = int.from_bytes(raw[off + 2:off + 4], "little")
    vk = int.from_bytes(raw[off + 6:off + 8], "little")
    down = not (flags & RI_KEY_BREAK)
    name = vk_name(vk, make)
    key = (dev, vk)
    if vk == 0xFF:      # 伪键：前缀字节，通常是组合键的修饰，单独看没意义
        if RAW:
            out("%-22s %s  %s  %s  VK=0xFF sc=0x%02X" %
                (dev_label(dev), page_tag(1, 6), "▼按下" if down else "▲松开", "伪键(修饰)"))
        return
    if KEY_STATE.get(key) == down:
        if not RAW:
            return
        now = time.time()
        if now - REPEAT_AT.get(key, 0) < 0.25:
            return
        REPEAT_AT[key] = now
        out("%-22s %s  %s  %-18s VK=0x%02X sc=0x%02X (重复)" %
            (dev_label(dev), page_tag(1, 6), "▼按住" if down else "▲", name, vk, make))
        return
    KEY_STATE[key] = down
    out("%-22s %s  %s  %-18s VK=0x%02X sc=0x%02X" %
        (dev_label(dev), page_tag(1, 6), "▼按下" if down else "▲松开", name, vk, make))


def handle_hid(dev, raw, page, usage):
    size_hid = int.from_bytes(raw[24:28], "little")
    count = int.from_bytes(raw[28:32], "little")
    data = bytes(bytearray(raw[32:32 + size_hid * count]))
    tag = page_tag(page, usage)

    if page == 0x01 and usage == 0x06 and len(data) >= 3:
        # 键盘集合以 HID 报文形式送达：byte0=修饰键，byte2..=按键 usage
        mods = data[0]
        keys = [b for b in data[2:] if b]
        if not keys:
            if mods:
                out("%-22s %s  修饰键 0x%02X" % (dev_label(dev), tag, mods))
            return
        names = " + ".join(hid_key_name(k) for k in keys)
        if mods:
            names = "修饰0x%02X + %s" % (mods, names)
        sig = (dev, "kbd", bytes(data))
        if HID_LAST.get(sig[0:2]) == data and not RAW:
            return
        HID_LAST[sig[0:2]] = data
        out("%-22s %s  ▼按下  %s   raw=%s" % (dev_label(dev), tag, names, data.hex(" ")))
        return

    if page == 0x0C and len(data) >= 2:
        # 消费者页报文。注意两种格式：
        #   有 Report ID → [ID, usage_lo, usage_hi]（你这支就是 02 xx xx）
        #   无 Report ID → [usage_lo, usage_hi]
        if len(data) >= 3:
            u = data[1] | (data[2] << 8)
        else:
            u = data[0] | (data[1] << 8)
        prev = HID_LAST.get(dev)
        HID_LAST[dev] = data
        prev_u = 0
        if prev and len(prev) >= 3:
            prev_u = prev[1] | (prev[2] << 8)
        if u == 0:
            if prev_u:
                out("%-22s %s  ▲松开" % (dev_label(dev), tag))
            return
        name = CONSUMER.get(u, "未收录的用途码")
        out("%-22s %s  ▼按下  %-26s usage=0x%04X  %s  raw=%s" %
            (dev_label(dev), tag, name, u, consumer_verdict(u), data.hex(" ")))
        return

    # 厂商私有页 / 其他：只在报文变化时打印，并指出是哪个字节变了
    prev = HID_LAST.get(dev)
    HID_LAST[dev] = data
    if prev == data:
        return
    if prev and len(prev) == len(data):
        diff = ["字节[%d] %02X→%02X" % (i, prev[i], data[i])
                for i in range(len(data)) if prev[i] != data[i]]
        out("%-22s %s  报文变化  %s" % (dev_label(dev), tag, "  ".join(diff[:6])))
    else:
        out("%-22s %s  报文  %s" % (dev_label(dev), tag, data.hex(" ")))


SIZEOF_RAWINPUTHEADER = 24


def on_raw_input(lparam):
    size = wt.UINT(SIZEOF_RAWINPUTHEADER)
    user32.GetRawInputData(lparam, RID_INPUT, None, ctypes.byref(size),
                           SIZEOF_RAWINPUTHEADER)
    if not size.value:
        return
    buf = ctypes.create_string_buffer(size.value)
    got = user32.GetRawInputData(lparam, RID_INPUT, ctypes.byref(buf),
                                 ctypes.byref(size), SIZEOF_RAWINPUTHEADER)
    if got in (0, 0xFFFFFFFF):
        return
    parse_raw(bytes(bytearray(buf[:got])))


def parse_raw(raw):
    if len(raw) < 32:
        return
    dw_type = int.from_bytes(raw[0:4], "little")
    # 注意：必须转成普通 int。ctypes.c_void_p 不可哈希，拿来当字典键会抛
    # TypeError: unhashable type，导致每个事件都被判成「解析出错」。
    dev = int.from_bytes(raw[8:16], "little")
    if ONLY:
        lbl = dev_label(dev)
        m = re.match(r"([0-9A-F]{4}):([0-9A-F]{4})", lbl)
        if not m or (int(m.group(1), 16), int(m.group(2), 16)) != ONLY:
            return
    if dw_type == 0:            # 鼠标（飞鼠移动很频繁，只在按键状态变化时打印）
        handle_mouse(dev, raw)
    elif dw_type == 1:          # 键盘
        handle_keyboard(dev, raw)
    elif dw_type == 2:          # HID
        page, usage = tlc_of(dev)
        if page == 0x01 and usage == 0x02:   # 鼠标移动不打印
            return
        handle_hid(dev, raw, page, usage)


def wndproc(hwnd, msg, wparam, lparam):
    global ERR_COUNT
    if msg == WM_INPUT:
        try:
            on_raw_input(lparam)
        except Exception as e:
            ERR_COUNT += 1
            if ERR_COUNT <= 3:      # 前三次带完整堆栈，之后只报次数，避免刷屏
                import traceback
                out("解析出错(%d) %r" % (ERR_COUNT, e))
                for ln in traceback.format_exc().splitlines()[-4:]:
                    out("    " + ln)
            elif ERR_COUNT == 4:
                out("解析出错已出现多次，后续只计数不再打印。")
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def bind():
    user32.GetRawInputData.argtypes = [wt.HANDLE, wt.UINT, ctypes.c_void_p,
                                       ctypes.POINTER(wt.UINT), wt.UINT]
    user32.GetRawInputData.restype = wt.UINT
    user32.GetRawInputDeviceInfoW.argtypes = [wt.HANDLE, wt.UINT, ctypes.c_void_p,
                                              ctypes.POINTER(wt.UINT)]
    user32.GetRawInputDeviceInfoW.restype = wt.UINT
    user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE),
                                               wt.UINT, wt.UINT]
    user32.RegisterRawInputDevices.restype = wt.BOOL
    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.CreateWindowExW.restype = ctypes.c_void_p
    user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE,
                                       ctypes.c_void_p]
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT,
                                    wt.UINT, wt.UINT]
    user32.PeekMessageW.restype = wt.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]


_WNDPROC_REF = None      # 必须留引用，否则回调被回收后收到消息会崩


def create_window():
    global HWND_G, _WNDPROC_REF
    hinst = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSW()
    _WNDPROC_REF = WNDPROC(wndproc)
    wc.lpfnWndProc = _WNDPROC_REF
    wc.hInstance = hinst
    wc.lpszClassName = "KeyWatchWnd"
    if not user32.RegisterClassW(ctypes.byref(wc)):
        err = ctypes.get_last_error()
        if err != 1410:   # 类已注册
            out("注册窗口类失败 err=%d" % err)
            return False
    HWND_G = user32.CreateWindowExW(0, "KeyWatchWnd", "KeyWatch", 0, 0, 0, 0, 0,
                                    None, None, hinst, None)
    return bool(HWND_G)


def register(pairs):
    ok = []
    for page, usage in pairs:
        rd = RAWINPUTDEVICE()
        rd.usUsagePage = page
        rd.usUsage = usage
        rd.dwFlags = RIDEV_INPUTSINK
        rd.hwndTarget = HWND_G
        if user32.RegisterRawInputDevices(ctypes.byref(rd), 1,
                                          ctypes.sizeof(RAWINPUTDEVICE)):
            ok.append((page, usage))
    return ok


def main():
    global LOGF, ONLY
    for i, a in enumerate(sys.argv):
        if a == "--only" and i + 1 < len(sys.argv):
            v, p = sys.argv[i + 1].split(":")
            ONLY = (int(v, 16), int(p, 16))
    secs = None
    for i, a in enumerate(sys.argv):
        if a == "--secs" and i + 1 < len(sys.argv):
            secs = float(sys.argv[i + 1])

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    LOGF = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "keywatch.log"),
                "a", encoding="utf-8")
    LOGF.write("\n\n========== %s ==========\n" % time.strftime("%Y-%m-%d %H:%M:%S"))

    bind()
    if not create_window():
        out("创建窗口失败，无法接收原始输入。")
        return

    pairs = [(0x01, 0x06), (0x01, 0x02), (0x0C, 0x01), (0x01, 0x80)]
    if ONLY:
        found = discover_collections(*ONLY)
        pairs += sorted(found)
        out("发现 %d 个集合: %s" % (len(found),
                                  ", ".join("0x%04X/0x%04X" % f for f in sorted(found)) or "(无)"))
    seen = []
    for p in pairs:
        if p not in seen:
            seen.append(p)
    ok = register(seen)
    out("已监听 %d 个顶级集合: %s" % (len(ok),
                                 ", ".join(page_tag(*p) for p in ok)))
    out("")
    out("现在按遥控器上的键，每次按下/松开都会实时出现一行。")
    out("如果某个键按下去**一行都没有**，说明它根本没往电脑发任何 HID 报文（例如只发红外的电源键）。")
    out("Ctrl+C 退出。")
    out("-" * 78)

    msg = wt.MSG()
    start = time.time()
    while True:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):   # PM_REMOVE
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if secs and time.time() - start > secs:
            out("")
            out("到时退出。日志已写入 keywatch.log")
            break
        time.sleep(0.005)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
