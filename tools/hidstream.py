"""直接读 HID 原始输入报告流，判断按住期间设备是否持续发帧。

背景：Raw Input 只在报告内容变化时通知，若设备按住时每 10ms 发一次同样的
`02 23 02`，Raw Input 只会给一次，看起来像「固定脉冲」。ReadFile 走的是
HID 报告队列，可能看得到每一帧 —— 如果能，按住时长就是可测的。

两路同时收，用来对照：
  [RI] = Raw Input（只在内容变化时通知）
  [FD] = ReadFile 直读报告队列（每一帧都算）
同一次按键若 [RI] 1 条、[FD] 十几条，说明按住期间设备确实在持续发帧。

用法：python tools/hidstream.py [秒数]   # 省略秒数 = 一直跑，Ctrl+C 或结束进程
"""

import ctypes
import sys
import threading
import time
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32 = ctypes.WinDLL("user32", use_last_error=True)

RIM_TYPEHID = 2
RIDI_DEVICENAME = 0x20000007
RIDEV_INPUTSINK = 0x00000100
WM_INPUT = 0x00FF
GENERIC_READ = 0x80000000
FILE_SHARE_READ, FILE_SHARE_WRITE = 0x1, 0x2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

COUNTS = {"ri": 0, "fd": 0}
LOCK = threading.Lock()
T0 = time.time()


class RAWINPUTDEVICELIST(ctypes.Structure):
    _fields_ = [("hDevice", wintypes.HANDLE), ("dwType", wintypes.DWORD)]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT),
                ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)

u32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
u32.CreateWindowExW.restype = wintypes.HWND
u32.RegisterClassW.argtypes = (ctypes.POINTER(WNDCLASSW),)
u32.RegisterClassW.restype = wintypes.ATOM
u32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
u32.DefWindowProcW.restype = ctypes.c_void_p
u32.RegisterRawInputDevices.argtypes = (ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT)
u32.RegisterRawInputDevices.restype = wintypes.BOOL
u32.GetRawInputData.argtypes = (wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
                                ctypes.POINTER(wintypes.UINT), wintypes.UINT)
u32.GetRawInputData.restype = wintypes.UINT
k32.GetModuleHandleW.restype = wintypes.HMODULE
u32.GetRawInputDeviceList.argtypes = (ctypes.POINTER(RAWINPUTDEVICELIST),
                                      ctypes.POINTER(wintypes.UINT), wintypes.UINT)
u32.GetRawInputDeviceInfoW.argtypes = (wintypes.HANDLE, wintypes.UINT,
                                       ctypes.c_void_p, ctypes.POINTER(wintypes.UINT))
k32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
k32.CreateFileW.restype = wintypes.HANDLE
k32.ReadFile.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                         ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
k32.ReadFile.restype = wintypes.BOOL
k32.CloseHandle.argtypes = (wintypes.HANDLE,)
k32.CloseHandle.restype = wintypes.BOOL


def stamp():
    now = time.time()
    return time.strftime("%H:%M:%S", time.localtime(now)) + ".%03d" % int(now % 1 * 1000)


def emit(text):
    with LOCK:
        print("[%s] %s" % (stamp(), text), flush=True)


def bump(key):
    with LOCK:
        COUNTS[key] += 1


def hid_paths():
    n = wintypes.UINT(0)
    u32.GetRawInputDeviceList(None, ctypes.byref(n), ctypes.sizeof(RAWINPUTDEVICELIST))
    arr = (RAWINPUTDEVICELIST * n.value)()
    u32.GetRawInputDeviceList(arr, ctypes.byref(n), ctypes.sizeof(RAWINPUTDEVICELIST))
    found = []
    for i in range(n.value):
        if arr[i].dwType != RIM_TYPEHID:
            continue
        size = wintypes.UINT(0)
        u32.GetRawInputDeviceInfoW(arr[i].hDevice, RIDI_DEVICENAME, None, ctypes.byref(size))
        if not size.value:
            continue
        buf = ctypes.create_unicode_buffer(size.value + 1)
        u32.GetRawInputDeviceInfoW(arr[i].hDevice, RIDI_DEVICENAME, buf, ctypes.byref(size))
        name = buf.value
        if "vid_1915" in name.lower() and "pid_1025" in name.lower():
            found.append(name)
    return found


def reader(path):
    """直读报告队列：每一帧都打印。"""
    h = k32.CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                        None, OPEN_EXISTING, 0, None)
    if h == INVALID_HANDLE_VALUE or h is None:
        emit("[FD] 打不开 %s（err=%d）" % (path.split("#")[1] if "#" in path else path,
                                      ctypes.get_last_error()))
        return
    emit("[FD] 已打开 %s" % (path.split("#")[1] if "#" in path else path))
    buf = ctypes.create_string_buffer(128)
    n = wintypes.DWORD(0)
    while True:
        ok = k32.ReadFile(h, buf, 128, ctypes.byref(n), None)
        if not ok:
            emit("[FD] 读取失败（err=%d）" % ctypes.get_last_error())
            break
        bump("fd")
        emit("[FD] %2d 字节: %s" % (n.value, bytes(buf.raw[:n.value]).hex(" ")))
    k32.CloseHandle(h)


def sizeof_rawinputheader():
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        return 24
    return 16


def raw_input_thread():
    """Raw Input：只在报告内容变化时通知，作为对照。"""
    hinst = k32.GetModuleHandleW(None)
    cls = "hidstream_ri_%d" % int(time.time() * 1000)

    def wndproc(hwnd, msg, wparam, lparam):
        if msg == WM_INPUT:
            size = wintypes.UINT(0)
            u32.GetRawInputData(wparam, 0x10000003, None, ctypes.byref(size),
                                sizeof_rawinputheader())
            if size.value:
                buf = ctypes.create_string_buffer(size.value)
                got = u32.GetRawInputData(wparam, 0x10000003, buf, ctypes.byref(size),
                                          sizeof_rawinputheader())
                if got:
                    data = bytes(buf.raw[:got])
                    body = data[sizeof_rawinputheader():]
                    # body: 报告长度(4) + 报告内容
                    if len(body) >= 4:
                        rlen = int.from_bytes(body[:4], "little")
                        payload = body[4:4 + rlen]
                        bump("ri")
                        emit("[RI] %2d 字节: %s" % (rlen, payload.hex(" ")))
            return 0
        if msg == 0x0002:      # WM_DESTROY
            u32.PostQuitMessage(0)
            return 0
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc = WNDPROC(wndproc)
    wc = WNDCLASSW()
    wc.lpfnWndProc = ctypes.cast(proc, ctypes.c_void_p)
    wc.hInstance = hinst
    wc.lpszClassName = cls
    if not u32.RegisterClassW(ctypes.byref(wc)):
        emit("[RI] RegisterClass 失败 err=%d" % ctypes.get_last_error())
        return
    hwnd = u32.CreateWindowExW(0, cls, cls, 0, 0, 0, 0, 0, None, None, hinst, None)
    if not hwnd:
        emit("[RI] CreateWindow 失败 err=%d" % ctypes.get_last_error())
        return
    reg = (RAWINPUTDEVICE * 1)()
    reg[0].usUsagePage = 0x0C
    reg[0].usUsage = 0x01
    reg[0].dwFlags = RIDEV_INPUTSINK
    reg[0].hwndTarget = hwnd
    if not u32.RegisterRawInputDevices(reg, 1, ctypes.sizeof(RAWINPUTDEVICE)):
        emit("[RI] 注册消费者页失败 err=%d" % ctypes.get_last_error())
        return
    emit("[RI] 已注册消费者页（0x0C/0x01），hwnd=%d" % hwnd)
    msg = wintypes.MSG()
    while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        u32.TranslateMessage(ctypes.byref(msg))
        u32.DispatchMessageW(ctypes.byref(msg))


def heartbeat():
    while True:
        time.sleep(10)
        emit("… 已运行 %d 秒，累计 RI=%d 条 / FD=%d 条"
             % (int(time.time() - T0), COUNTS["ri"], COUNTS["fd"]))


def main():
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    paths = hid_paths()
    emit("找到 %d 个集合" % len(paths))
    targets = [p for p in paths if "mi_03" in p.lower()] or paths
    for p in targets:
        threading.Thread(target=reader, args=(p,), daemon=True).start()
    threading.Thread(target=raw_input_thread, daemon=True).start()
    threading.Thread(target=heartbeat, daemon=True).start()
    emit("开始监听（%s）" % ("一直运行" if not secs else "%d 秒" % secs))
    try:
        time.sleep(secs if secs else 10 ** 9)
    except KeyboardInterrupt:
        pass
    emit("结束。累计 RI=%d 条 / FD=%d 条" % (COUNTS["ri"], COUNTS["fd"]))


if __name__ == "__main__":
    main()
