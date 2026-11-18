"""读取默认输出设备的静音状态与音量，用来客观判断某次按键有没有真的静音。

用法：
    python tools/audiocheck.py          # 打印一行
    python tools/audiocheck.py --watch  # 每 0.5 秒打印一次，按遥控器时看变化

为什么需要它：日志只能记录「收到了哪个报文」，记录不了音量。
判断「静音键会不会真的静音」只能直接问系统。

用 ctypes 手写 IAudioEndpointVolume，不依赖第三方库（pycaw 等）。
"""
import ctypes
import sys
import time
from ctypes import (POINTER, byref, c_void_p, c_float, Structure,
                    WINFUNCTYPE, wintypes)

ole32 = ctypes.WinDLL("ole32", use_last_error=True)
ole32.CoInitialize(None)
ole32.CLSIDFromString.argtypes = (wintypes.LPCWSTR, ctypes.c_void_p)
ole32.CoCreateInstance.argtypes = (ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                   ctypes.c_void_p, POINTER(c_void_p))


class GUID(Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8)]


def make_guid(text):
    g = GUID()
    ole32.CLSIDFromString(text, byref(g))
    return g


CLSID_MMDeviceEnumerator = make_guid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
IID_IMMDeviceEnumerator = make_guid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
IID_IAudioEndpointVolume = make_guid("{5CDF2C82-841E-4546-9722-0CF74078229A}")

CLSCTX_ALL = 23
eRender, eConsole = 0, 0


def vt_call(obj, index, restype, argtypes, *args):
    """按 vtable 序号调用 COM 方法（序号从 QueryInterface=0 起算）。"""
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
    fn = WINFUNCTYPE(restype, c_void_p, *argtypes)(vtbl[index])
    return fn(obj, *args)


def open_endpoint_volume():
    p_enum = c_void_p()
    hr = ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                                byref(IID_IMMDeviceEnumerator), byref(p_enum))
    if hr != 0:
        raise OSError("CoCreateInstance 失败 hr=0x%08X" % (hr & 0xFFFFFFFF))
    p_dev = c_void_p()
    # IMMDeviceEnumerator::GetDefaultAudioEndpoint(EDataFlow, ERole, IMMDevice**)
    hr = vt_call(p_enum, 4, ctypes.c_long,
                 (ctypes.c_int, ctypes.c_int, POINTER(c_void_p)),
                 eRender, eConsole, byref(p_dev))
    if hr != 0:
        raise OSError("GetDefaultAudioEndpoint 失败 hr=0x%08X" % (hr & 0xFFFFFFFF))
    p_vol = c_void_p()
    # IMMDevice::Activate(REFIID, DWORD, PROPVARIANT*, void**)
    hr = vt_call(p_dev, 3, ctypes.c_long,
                 (ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, POINTER(c_void_p)),
                 byref(IID_IAudioEndpointVolume), CLSCTX_ALL, None, byref(p_vol))
    if hr != 0:
        raise OSError("Activate 失败 hr=0x%08X" % (hr & 0xFFFFFFFF))
    # 释放前两个对象（Release 是 vtable 第 2 项）
    for obj in (p_dev, p_enum):
        vt_call(obj, 2, ctypes.c_ulong, ())
    return p_vol


def read(p_vol):
    mute = wintypes.BOOL()
    # IAudioEndpointVolume::GetMute(BOOL*) —— vtable 第 15 项
    hr1 = vt_call(p_vol, 15, ctypes.c_long, (POINTER(wintypes.BOOL),), byref(mute))
    level = c_float()
    # GetMasterVolumeLevelScalar(float*) —— vtable 第 9 项
    hr2 = vt_call(p_vol, 9, ctypes.c_long, (POINTER(c_float),), byref(level))
    return (bool(mute.value) if hr1 == 0 else None,
            round(level.value * 100) if hr2 == 0 else None)


def main():
    watch = "--watch" in sys.argv
    p_vol = open_endpoint_volume()
    if not watch:
        mute, level = read(p_vol)
        print("静音=%s  音量=%s%%" % ("是" if mute else "否", level))
        return 0
    print("每 0.5 秒刷新，Ctrl+C 结束。按遥控器静音键，看这两列怎么变：")
    last = None
    try:
        while True:
            mute, level = read(p_vol)
            cur = (mute, level)
            mark = "" if cur == last else "   ← 变化"
            print("[%s] 静音=%-2s 音量=%s%%%s"
                  % (time.strftime("%H:%M:%S"), "是" if mute else "否", level, mark))
            last = cur
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
