"""连续录音电平记录器（纯 ctypes + winmm，无第三方依赖）。

用途：判断「遥控器麦克风开着但没说话」时，音频是不是数字零。
这决定了能不能用音频流当作「还按着」的证据。

用法：
    python audio_watch.py -l             # 列出录音设备
    python audio_watch.py -d 0 -s 60     # 录 60 秒，每 100ms 打一行

输出同时写 audio.log，带绝对时间戳，方便和 bridge.log 对齐。
"""

import ctypes
import ctypes.wintypes as wt
import math
import os
import sys
import time

winmm = ctypes.WinDLL("winmm")

CALLBACK_NULL = 0x00000000
WHDR_DONE = 0x00000001
WHDR_PREPARED = 0x00000002
WAVE_FORMAT_PCM = 1
WAVE_MAPPER = 0xFFFFFFFF

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio.log")


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


def list_devices():
    n = winmm.waveInGetNumDevs()
    print("录音设备 %d 个：" % n)
    for i in range(n):
        caps = WAVEINCAPSW()
        if winmm.waveInGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            print("  [%d] %s" % (i, caps.szPname))
    return n


class Recorder:
    def __init__(self, dev=0, chunk_ms=100, rate=16000, buffers=4):
        self.wfx = WAVEFORMATEX()
        self.wfx.wFormatTag = WAVE_FORMAT_PCM
        self.wfx.nChannels = 1
        self.wfx.nSamplesPerSec = rate
        self.wfx.wBitsPerSample = 16
        self.wfx.nBlockAlign = 2
        self.wfx.nAvgBytesPerSec = rate * 2
        self.hwi = ctypes.c_void_p()
        r = winmm.waveInOpen(ctypes.byref(self.hwi), wt.UINT(dev), ctypes.byref(self.wfx),
                             None, None, CALLBACK_NULL)
        if r != 0:
            raise OSError("waveInOpen 失败 code=%d" % r)
        self.nbytes = int(rate * chunk_ms / 1000) * 2
        self.hdrs, self.bufs = [], []
        self.errors = 0
        for _ in range(buffers):
            hdr = WAVEHDR()
            buf = ctypes.create_string_buffer(self.nbytes)
            hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
            hdr.dwBufferLength = self.nbytes
            winmm.waveInPrepareHeader(self.hwi, ctypes.byref(hdr), ctypes.sizeof(hdr))
            winmm.waveInAddBuffer(self.hwi, ctypes.byref(hdr), ctypes.sizeof(hdr))
            self.hdrs.append(hdr)
            self.bufs.append(buf)
        winmm.waveInStart(self.hwi)

    def poll(self):
        """返回已就绪的数据块 [(bytes_recorded, peak, rms)]，并把缓冲重新入队。"""
        out = []
        for hdr, buf in zip(self.hdrs, self.bufs):
            if not (hdr.dwFlags & WHDR_DONE):
                continue
            n = hdr.dwBytesRecorded // 2
            peak, total = 0, 0
            if n > 0:
                samples = ctypes.cast(buf, ctypes.POINTER(ctypes.c_int16))
                for i in range(n):
                    v = samples[i]
                    total += v * v
                    if abs(v) > peak:
                        peak = abs(v)
            rms = math.sqrt(total / n) if n else 0.0
            out.append((n, peak, rms))
            # 关键：只能清 WHDR_DONE，不能把 dwFlags 整个清零 ——
            # waveInPrepareHeader 会在 dwFlags 里打上 WHDR_PREPARED，
            # 清掉它就等于把缓冲「取消准备」，之后的 AddBuffer 会全部失败。
            hdr.dwFlags = WHDR_PREPARED
            hdr.dwBytesRecorded = 0
            rc = winmm.waveInAddBuffer(self.hwi, ctypes.byref(hdr), ctypes.sizeof(hdr))
            if rc != 0:
                self.errors += 1
                if self.errors <= 3:
                    print("waveInAddBuffer 失败 code=%d" % rc, flush=True)
            else:
                self.errors = 0
        return out

    def close(self):
        try:
            winmm.waveInStop(self.hwi)
            winmm.waveInReset(self.hwi)
            for hdr in self.hdrs:
                winmm.waveInUnprepareHeader(self.hwi, ctypes.byref(hdr), ctypes.sizeof(hdr))
        finally:
            winmm.waveInClose(self.hwi)


def main():
    args = sys.argv[1:]
    if "-l" in args:
        list_devices()
        return 0
    dev = int(args[args.index("-d") + 1]) if "-d" in args else 0
    secs = float(args[args.index("-s") + 1]) if "-s" in args else 30.0

    rec = Recorder(dev)
    f = open(LOG, "w", encoding="utf-8")
    print("记录 %.0f 秒（设备 %d），每块约 100ms …" % (secs, dev))
    t0 = time.time()
    try:
        while time.time() - t0 < secs:
            for n, peak, rms in rec.poll():
                db = 20 * math.log10(rms / 32768.0) if rms > 0 else -999.0
                line = "%s  字节=%5d  peak=%6d  rms=%8.1f  %7.1f dBFS  %s" % (
                    time.strftime("%H:%M:%S") + (".%03d" % int((time.time() % 1) * 1000)),
                    n * 2, peak, rms, db,
                    "无数据" if n == 0 else ("静音" if peak <= 2 else "有声"))
                print(line, flush=True)
                f.write(line + "\n")
            f.flush()
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        rec.close()
        f.close()
    print("已写入 %s" % LOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
