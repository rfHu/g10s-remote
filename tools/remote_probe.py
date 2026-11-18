# -*- coding: utf-8 -*-
"""
遥控器/飞鼠探针：同时记录「它发了什么 HID 报文」和「麦克风有没有声音」

一条命令就能把三件事一起测清楚：
  1. 按麦克风键时，设备是不是真的在往电脑送音频（看电平曲线）
  2. 各按键落在哪个 HID 集合上、发的是什么 usage / 字节
  3. 厂商自定义集合（0xFFxx）里有没有数据 —— 有的话说明它可能走了私有通道

用法：
  python remote_probe.py                 # 自动找 VID 1915 / PID 1025（当前这支飞鼠的接收器）
  python remote_probe.py --vid 1915 --pid 1025
  python remote_probe.py --vid 0x1915 --secs 40
  python remote_probe.py --mic 0         # 指定录音设备序号（默认自动挑第一个）

结果同时写到 remote_probe.log，方便贴回来。
"""

import ctypes
import ctypes.wintypes as wt
import math
import os
import sys
import threading
import time
import uuid

# ---------- HID 侧 ----------
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
winmm = ctypes.WinDLL("winmm")

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10
GUID_DEVINTERFACE_HID = uuid.UUID("{4D1E55B2-F16F-11CF-88CB-001111000030}")
GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3

WAVE_MAPPER = 0xFFFFFFFF
CALLBACK_NULL = 0
WHDR_DONE = 0x1
WAVE_FORMAT_PCM = 1


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD),
                ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wt.DWORD), ("Reserved", ctypes.c_void_p)]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wt.ULONG), ("VendorID", wt.USHORT),
                ("ProductID", wt.USHORT), ("VersionNumber", wt.USHORT)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", wt.USHORT), ("UsagePage", wt.USHORT),
                ("InputReportByteLength", wt.USHORT),
                ("OutputReportByteLength", wt.USHORT),
                ("FeatureReportByteLength", wt.USHORT),
                ("Reserved", wt.USHORT * 17),
                ("NumberLinkCollectionNodes", wt.USHORT),
                ("NumberInputButtonCaps", wt.USHORT),
                ("NumberInputValueCaps", wt.USHORT),
                ("NumberInputDataIndices", wt.USHORT),
                ("NumberOutputButtonCaps", wt.USHORT),
                ("NumberOutputValueCaps", wt.USHORT),
                ("NumberOutputDataIndices", wt.USHORT),
                ("NumberFeatureButtonCaps", wt.USHORT),
                ("NumberFeatureValueCaps", wt.USHORT),
                ("NumberFeatureDataIndices", wt.USHORT)]


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [("wFormatTag", wt.WORD), ("nChannels", wt.WORD),
                ("nSamplesPerSec", wt.DWORD), ("nAvgBytesPerSec", wt.DWORD),
                ("nBlockAlign", wt.WORD), ("wBitsPerSample", wt.WORD),
                ("cbSize", wt.WORD)]


class WAVEINCAPSW(ctypes.Structure):
    _fields_ = [("wMid", wt.WORD), ("wPid", wt.WORD), ("vDriverVersion", wt.UINT),
                ("szPname", wt.WCHAR * 32), ("dwFormats", wt.DWORD),
                ("wChannels", wt.WORD), ("wReserved1", wt.WORD)]


class WAVEHDR(ctypes.Structure):
    _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wt.DWORD),
                ("dwBytesRecorded", wt.DWORD), ("dwUser", ctypes.c_void_p),
                ("dwFlags", wt.DWORD), ("dwLoops", wt.DWORD),
                ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_void_p)]


def bind():
    setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                              ctypes.c_void_p, wt.DWORD]
    setupapi.SetupDiEnumDeviceInterfaces.restype = wt.BOOL
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wt.DWORD,
        ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
    setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wt.BOOL
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
        ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, wt.DWORD, wt.DWORD,
                                     ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.c_void_p]
    kernel32.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.DWORD,
                                  ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
    kernel32.ReadFile.restype = wt.BOOL
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    hid.HidD_GetAttributes.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDD_ATTRIBUTES)]
    hid.HidD_GetProductString.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, wt.ULONG]
    hid.HidD_GetPreparsedData.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]


def enum_hid():
    g = GUID()
    g.Data1, g.Data2, g.Data3 = GUID_DEVINTERFACE_HID.fields[0], GUID_DEVINTERFACE_HID.fields[1], GUID_DEVINTERFACE_HID.fields[2]
    for i in range(8):
        g.Data4[i] = GUID_DEVINTERFACE_HID.bytes[8 + i]
    hdev = setupapi.SetupDiGetClassDevsW(ctypes.byref(g), None, None,
                                         DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    out = []
    if not hdev or hdev == 0xFFFFFFFFFFFFFFFF:
        return out
    did = SP_DEVICE_INTERFACE_DATA()
    did.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
    idx = 0
    while setupapi.SetupDiEnumDeviceInterfaces(hdev, None, ctypes.byref(g), idx, ctypes.byref(did)):
        idx += 1
        need = wt.DWORD(0)
        setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(did), None, 0, ctypes.byref(need), None)
        if not need.value:
            continue
        buf = ctypes.create_string_buffer(need.value)
        cb = wt.DWORD(8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6)
        ctypes.memmove(buf, ctypes.byref(cb), 4)
        if not setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(did), buf, need.value, ctypes.byref(need), None):
            continue
        path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
        h = kernel32.CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                 None, OPEN_EXISTING, 0, None)
        if not h or h == 0xFFFFFFFFFFFFFFFF:
            h = kernel32.CreateFileW(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
            if not h or h == 0xFFFFFFFFFFFFFFFF:
                continue
        attrs = HIDD_ATTRIBUTES()
        attrs.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
        hid.HidD_GetAttributes(h, ctypes.byref(attrs))
        caps = HIDP_CAPS()
        pp = ctypes.c_void_p()
        if hid.HidD_GetPreparsedData(h, ctypes.byref(pp)):
            hid.HidP_GetCaps(pp, ctypes.byref(caps))
            hid.HidD_FreePreparsedData(pp)
        pname = ctypes.create_unicode_buffer(256)
        hid.HidD_GetProductString(h, pname, 512)
        out.append({"path": path, "handle": h, "vid": attrs.VendorID, "pid": attrs.ProductID,
                    "name": pname.value, "page": caps.UsagePage, "usage": caps.Usage,
                    "inlen": caps.InputReportByteLength})
    return out


# ---------- 输出 ----------
LOG_LOCK = threading.Lock()
LOGF = None
T0 = time.time()


def log(msg):
    line = "[%6.2fs] %s" % (time.time() - T0, msg)
    with LOG_LOCK:
        print(line, flush=True)
        if LOGF:
            LOGF.write(line + "\n")
            LOGF.flush()


def sniffer(dev):
    tag = "P%04X/U%04X" % (dev["page"], dev["usage"])
    h = dev["handle"]
    size = max(64, dev["inlen"])
    buf = ctypes.create_string_buffer(size)
    n = wt.DWORD(0)
    while True:
        ok = kernel32.ReadFile(h, buf, size, ctypes.byref(n), None)
        if not ok:
            log("%s  读取结束/失败" % tag)
            return
        data = bytes(bytearray(buf[: n.value]))
        log("%s  %s" % (tag, data.hex(" ")))


def mic_loop(dev_index, stop, window=0.25, rate=16000):
    wfx = WAVEFORMATEX()
    wfx.wFormatTag = WAVE_FORMAT_PCM
    wfx.nChannels = 1
    wfx.nSamplesPerSec = rate
    wfx.wBitsPerSample = 16
    wfx.nBlockAlign = 2
    wfx.nAvgBytesPerSec = rate * 2
    hwi = ctypes.c_void_p()
    dev = WAVE_MAPPER if dev_index is None else dev_index
    if winmm.waveInOpen(ctypes.byref(hwi), wt.UINT(dev), ctypes.byref(wfx), None, None, CALLBACK_NULL) != 0:
        log("MIC  打开录音设备失败")
        return
    chunk = int(rate * window) * 2
    hdr = WAVEHDR()
    buf = ctypes.create_string_buffer(chunk)
    hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
    hdr.dwBufferLength = chunk
    while not stop.is_set():
        hdr.dwFlags = 0
        hdr.dwBytesRecorded = 0
        winmm.waveInPrepareHeader(hwi, ctypes.byref(hdr), ctypes.sizeof(hdr))
        winmm.waveInAddBuffer(hwi, ctypes.byref(hdr), ctypes.sizeof(hdr))
        winmm.waveInStart(hwi)
        deadline = time.time() + window + 2.0
        while not (hdr.dwFlags & WHDR_DONE):
            if time.time() > deadline:
                break
            time.sleep(0.01)
        winmm.waveInStop(hwi)
        winmm.waveInReset(hwi)
        n = hdr.dwBytesRecorded // 2
        if n <= 0:
            continue
        s = ctypes.cast(buf, ctypes.POINTER(ctypes.c_int16))
        total = 0
        peak = 0
        for i in range(n):
            v = s[i]
            total += v * v
            if abs(v) > peak:
                peak = abs(v)
        rms = math.sqrt(total / n)
        db = 20 * math.log10(rms / 32768.0) if rms > 0 else -999
        if db > -85:
            log("MIC   %6.1f dBFS  峰值=%d  %s" % (db, peak, "#" * min(50, int(max(0, (db + 70) / 70 * 50)))))
    winmm.waveInUnprepareHeader(hwi, ctypes.byref(hdr), ctypes.sizeof(hdr))
    winmm.waveInClose(hwi)


def main():
    global LOGF
    bind()
    args = sys.argv[1:]
    vid = 0x1915
    pid = 0x1025
    secs = 60.0
    mic_index = None
    if "--vid" in args:
        vid = int(args[args.index("--vid") + 1], 0)
    if "--pid" in args:
        pid = int(args[args.index("--pid") + 1], 0)
    if "--secs" in args:
        secs = float(args[args.index("--secs") + 1])
    if "--mic" in args:
        mic_index = int(args[args.index("--mic") + 1])

    devs = enum_hid()
    mine = [d for d in devs if d["vid"] == vid and d["pid"] == pid]
    LOGF = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "remote_probe.log"),
                "a", encoding="utf-8")
    LOGF.write("\n\n========== %s  VID=%04X PID=%04X ==========\n"
               % (time.strftime("%Y-%m-%d %H:%M:%S"), vid, pid))
    log("匹配到 %d 个 HID 集合（VID=%04X PID=%04X）" % (len(mine), vid, pid))
    if not mine:
        log("没找到。先确认接收器插着，或用 --vid/--pid 指定。")
        return
    for d in mine:
        log("  集合 page=0x%04X usage=0x%04X 报文字节=%d  name=[%s]"
            % (d["page"], d["usage"], d["inlen"], d["name"]))
    log("")
    log("现在开始计时 %.0f 秒。请依次操作：" % secs)
    log("  ① 按住麦克风键说一句话（约 4 秒）② 松手")
    log("  ③ 方向键 上/下/左/右 各按一次   ④ OK")
    log("  ⑤ 音量+ / 音量-   ⑥ 主页   ⑦ 返回   ⑧ 菜单")
    log("")

    stop = threading.Event()
    threads = []
    for d in mine:
        t = threading.Thread(target=sniffer, args=(d,), daemon=True)
        t.start()
        threads.append(t)
    mt = threading.Thread(target=mic_loop, args=(mic_index, stop), daemon=True)
    mt.start()

    try:
        time.sleep(secs)
    except KeyboardInterrupt:
        pass
    stop.set()
    time.sleep(0.3)
    log("")
    log("采集结束，日志已写入 remote_probe.log")


if __name__ == "__main__":
    main()
