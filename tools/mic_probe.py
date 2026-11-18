# -*- coding: utf-8 -*-
"""
命令行录音电平探针（纯 ctypes + winmm，不需要任何第三方库）

用途：在不动手改系统设置的前提下，直接看某个录音设备到底有没有声音进来。
按麦克风键说话，如果电平曲线跟着说话起伏，说明这支设备的音频已经进了 Windows。

用法：
  python mic_probe.py            # 列出全部录音设备
  python mic_probe.py -l         # 同上
  python mic_probe.py -d 2 -s 15 # 用第 2 号设备录 15 秒，每 0.25 秒打一行电平
  python mic_probe.py -n "Mic"   # 按名字模糊匹配设备
"""

import ctypes
import ctypes.wintypes as wt
import sys
import time
import math

winmm = ctypes.WinDLL("winmm")

WAVE_MAPPER = 0xFFFFFFFF
CALLBACK_NULL = 0x00000000
WHDR_DONE = 0x00000001
WAVE_FORMAT_PCM = 1


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wt.WORD),
        ("nChannels", wt.WORD),
        ("nSamplesPerSec", wt.DWORD),
        ("nAvgBytesPerSec", wt.DWORD),
        ("nBlockAlign", wt.WORD),
        ("wBitsPerSample", wt.WORD),
        ("cbSize", wt.WORD),
    ]


class WAVEINCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wt.WORD),
        ("wPid", wt.WORD),
        ("vDriverVersion", wt.UINT),
        ("szPname", wt.WCHAR * 32),
        ("dwFormats", wt.DWORD),
        ("wChannels", wt.WORD),
        ("wReserved1", wt.WORD),
    ]


class WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", wt.DWORD),
        ("dwBytesRecorded", wt.DWORD),
        ("dwUser", ctypes.c_void_p),
        ("dwFlags", wt.DWORD),
        ("dwLoops", wt.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_void_p),
    ]


def list_devices():
    n = winmm.waveInGetNumDevs()
    print("录音设备数量: %d\n" % n)
    out = []
    for i in range(n):
        caps = WAVEINCAPSW()
        r = winmm.waveInGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps))
        name = caps.szPname if r == 0 else "?"
        chans = caps.wChannels if r == 0 else 0
        print("  [%d] %s   (声道=%d)" % (i, name, chans))
        out.append((i, name))
    return out


def capture(dev_index, seconds, window=0.25, rate=16000):
    wfx = WAVEFORMATEX()
    wfx.wFormatTag = WAVE_FORMAT_PCM
    wfx.nChannels = 1
    wfx.nSamplesPerSec = rate
    wfx.wBitsPerSample = 16
    wfx.nBlockAlign = wfx.nChannels * wfx.wBitsPerSample // 8
    wfx.nAvgBytesPerSec = wfx.nSamplesPerSec * wfx.nBlockAlign
    wfx.cbSize = 0

    hwi = ctypes.c_void_p()
    dev = WAVE_MAPPER if dev_index is None else dev_index
    r = winmm.waveInOpen(ctypes.byref(hwi), wt.UINT(dev), ctypes.byref(wfx), None, None, CALLBACK_NULL)
    if r != 0:
        print("waveInOpen 失败, code=%d" % r)
        return

    chunk_bytes = int(rate * window) * wfx.nBlockAlign
    hdr = WAVEHDR()
    buf = ctypes.create_string_buffer(chunk_bytes)
    hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
    hdr.dwBufferLength = chunk_bytes
    winmm.waveInPrepareHeader(hwi, ctypes.byref(hdr), ctypes.sizeof(hdr))

    print("\n开始录音 %.1f 秒（Ctrl+C 提前结束）…" % seconds)
    t0 = time.time()
    try:
        while time.time() - t0 < seconds:
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
                print("  (无数据)")
                continue
            samples = ctypes.cast(buf, ctypes.POINTER(ctypes.c_int16))
            total = 0
            peak = 0
            for i in range(n):
                v = samples[i]
                total += v * v
                if abs(v) > peak:
                    peak = abs(v)
            rms = math.sqrt(total / n) if n else 0
            db = 20 * math.log10(rms / 32768.0) if rms > 0 else -999
            bar = "#" * min(60, int(max(0, (db + 70) / 70 * 60)))
            print("  t=%5.2fs  RMS=%7.1f  %6.1f dBFS  峰值=%6d  %s"
                  % (time.time() - t0, rms, db, peak, bar))
    except KeyboardInterrupt:
        pass
    finally:
        winmm.waveInUnprepareHeader(hwi, ctypes.byref(hdr), ctypes.sizeof(hdr))
        winmm.waveInClose(hwi)


def main():
    args = sys.argv[1:]
    if "-l" in args or not args:
        devs = list_devices()
        print("\n用法: python mic_probe.py -d <序号> -s <秒数>")
        return
    dev = None
    secs = 10.0
    if "-d" in args:
        dev = int(args[args.index("-d") + 1])
    if "-n" in args:
        kw = args[args.index("-n") + 1].lower()
        for i, name in list_devices():
            if kw in name.lower():
                dev = i
                print("\n匹配到 [%d] %s" % (i, name))
                break
    if "-s" in args:
        secs = float(args[args.index("-s") + 1])
    capture(dev, secs)


if __name__ == "__main__":
    main()
