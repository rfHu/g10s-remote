"""读 HID 报表描述符，列出每个集合支持的消费者页用途码。

用途：判断某个键盘/设备到底能不能发出 0x00CD（播放/暂停）这类键。
只用 ctypes + setupapi，无第三方依赖。

用法：
    python hid_usages.py            # 只看含消费者页的设备
    python hid_usages.py --all      # 全部设备
    python hid_usages.py --grep 046D
"""

import ctypes
import re
import sys
from ctypes import wintypes

setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10
IOCTL_HID_GET_REPORT_DESCRIPTOR = 0x000B0000
GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wintypes.ULONG), ("VendorID", wintypes.USHORT),
                ("ProductID", wintypes.USHORT), ("VersionNumber", wintypes.USHORT)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", wintypes.USHORT), ("UsagePage", wintypes.USHORT),
                ("InputReportByteLength", wintypes.USHORT),
                ("OutputReportByteLength", wintypes.USHORT),
                ("FeatureReportByteLength", wintypes.USHORT),
                ("Reserved", wintypes.USHORT * 17),
                ("NumberLinkCollectionNodes", wintypes.USHORT),
                ("NumberInputButtonCaps", wintypes.USHORT),
                ("NumberInputValueCaps", wintypes.USHORT),
                ("NumberInputDataIndices", wintypes.USHORT),
                ("NumberOutputButtonCaps", wintypes.USHORT),
                ("NumberOutputValueCaps", wintypes.USHORT),
                ("NumberOutputDataIndices", wintypes.USHORT),
                ("NumberFeatureButtonCaps", wintypes.USHORT),
                ("NumberFeatureValueCaps", wintypes.USHORT),
                ("NumberFeatureDataIndices", wintypes.USHORT)]


setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR,
                                         ctypes.c_void_p, wintypes.DWORD)
setupapi.SetupDiEnumDeviceInterfaces.argtypes = (ctypes.c_void_p, ctypes.c_void_p,
                                                ctypes.POINTER(GUID), wintypes.DWORD,
                                                ctypes.POINTER(SP_DEVICE_INTERFACE_DATA))
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = (ctypes.c_void_p,
                                                      ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
                                                      ctypes.c_void_p, wintypes.DWORD,
                                                      ctypes.POINTER(wintypes.DWORD),
                                                      ctypes.c_void_p)
setupapi.SetupDiDestroyDeviceInfoList.argtypes = (ctypes.c_void_p,)
k32.CreateFileW.restype = ctypes.c_void_p
k32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p)
k32.CloseHandle.argtypes = (ctypes.c_void_p,)
k32.DeviceIoControl.argtypes = (ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
hid.HidD_GetAttributes.argtypes = (ctypes.c_void_p, ctypes.POINTER(HIDD_ATTRIBUTES))
hid.HidD_GetPreparsedData.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
hid.HidP_GetCaps.argtypes = (ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS))
hid.HidD_FreePreparsedData.argtypes = (ctypes.c_void_p,)

HID_GUID = GUID(0x4D1E55B2, 0xF16F, 0x11CF,
                (ctypes.c_byte * 8)(*[0x88, 0xCB, 0x00, 0x11, 0x11, 0x00, 0x00, 0x30]))

CONSUMER_NAMES = {0x00CD: "播放/暂停", 0x00E9: "音量+", 0x00EA: "音量-",
                  0x00E2: "静音", 0x00B0: "播放", 0x00B1: "暂停",
                  0x00B3: "快进", 0x00B4: "快退", 0x00B5: "下一曲",
                  0x00B6: "上一曲", 0x0223: "主页", 0x0224: "返回",
                  0x0041: "菜单选择", 0x00CF: "语音指令", 0x006F: "亮度+",
                  0x0070: "亮度-", 0x0192: "计算器", 0x0221: "搜索"}


def parse_descriptor(desc):
    """返回 [(usage_page, usage)]，够用来判断某个用途码是否存在。"""
    out = []
    page = 0
    local = []
    umin = umax = None
    i = 0
    n = len(desc)
    while i < n:
        b = desc[i]
        if b == 0xFE:                       # 长条目
            if i + 2 >= n:
                break
            size = desc[i + 1]
            i += 3 + size
            continue
        size = {0: 0, 1: 1, 2: 2, 3: 4}[b & 0x03]
        tag = b & 0xFC
        raw = desc[i + 1:i + 1 + size]
        val = int.from_bytes(raw, "little") if size else 0

        if tag == 0x04:                     # Usage Page
            page = val
        elif tag == 0x08:                   # Usage
            local.append(val)
        elif tag == 0x18:                   # Usage Minimum
            umin = val
        elif tag == 0x28:                   # Usage Maximum
            umax = val
        elif tag in (0x80, 0x90, 0xB0, 0xA0, 0xC0):   # Main item
            if umin is not None and umax is not None and umax >= umin:
                span = min(umax - umin + 1, 256)
                for u in range(umin, umin + span):
                    local.append(u)
            for u in local:
                out.append((page, u))
            local = []
            umin = umax = None
        i += 1 + size
    return out


def short(path):
    m = re.search(r"hid#([^#]+)#", path, re.I)
    return m.group(1) if m else path[-40:]


def main():
    only_consumer = "--all" not in sys.argv
    grep = None
    if "--grep" in sys.argv:
        grep = sys.argv[sys.argv.index("--grep") + 1].upper()

    hdev = setupapi.SetupDiGetClassDevsW(ctypes.byref(HID_GUID), None, None,
                                         DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if hdev is None or hdev == -1:
        print("SetupDiGetClassDevs 失败")
        return 1

    found = 0
    try:
        index = 0
        while True:
            did = SP_DEVICE_INTERFACE_DATA()
            did.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            if not setupapi.SetupDiEnumDeviceInterfaces(hdev, None, ctypes.byref(HID_GUID),
                                                        index, ctypes.byref(did)):
                break
            index += 1
            need = wintypes.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(did), None, 0,
                                                      ctypes.byref(need), None)
            buf = ctypes.create_string_buffer(need.value)
            ctypes.memmove(ctypes.addressof(buf), ctypes.byref(wintypes.DWORD(8)), 4)
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(did),
                                                             buf, need.value,
                                                             ctypes.byref(need), None):
                continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)

            h = k32.CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                None, OPEN_EXISTING, 0, None)
            if not h or h == ctypes.c_void_p(-1).value:
                continue
            try:
                attrs = HIDD_ATTRIBUTES()
                attrs.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
                if not hid.HidD_GetAttributes(h, ctypes.byref(attrs)):
                    continue
                vidpid = "%04X:%04X" % (attrs.VendorID, attrs.ProductID)
                if grep and grep not in vidpid and grep not in path.upper():
                    continue

                pp = ctypes.c_void_p()
                caps = HIDP_CAPS()
                if not hid.HidD_GetPreparsedData(h, ctypes.byref(pp)):
                    continue
                hid.HidP_GetCaps(pp, ctypes.byref(caps))
                hid.HidD_FreePreparsedData(pp)

                # 取报表描述符
                dsize = wintypes.DWORD(4096)
                dbuf = ctypes.create_string_buffer(dsize.value)
                if not k32.DeviceIoControl(h, IOCTL_HID_GET_REPORT_DESCRIPTOR, None, 0,
                                           ctypes.byref(dbuf), dsize.value,
                                           ctypes.byref(dsize), None):
                    continue
                usages = parse_descriptor(bytearray(dbuf[:dsize.value]))
                consumer = sorted({u for (pg, u) in usages if pg == 0x0C})
                if only_consumer and caps.UsagePage != 0x0C:
                    continue
                found += 1
                print("=" * 70)
                print("%s  TLC=%04X/%04X  入报文字节=%d" %
                      (vidpid, caps.UsagePage, caps.Usage, caps.InputReportByteLength))
                print("  路径: %s" % short(path))
                if consumer:
                    named = ["0x%04X %s" % (u, CONSUMER_NAMES.get(u, "")) for u in consumer]
                    print("  消费者页用途码(%d 个):" % len(consumer))
                    for line in named:
                        print("     " + line)
                    hit = [u for u in (0x00CD, 0x00E9, 0x00EA, 0x00E2) if u in consumer]
                    print("  → 可发播放/音量键: %s" %
                          ("是 " + ",".join("0x%04X" % u for u in hit) if hit else "否"))
                else:
                    print("  消费者页用途码: 无")
            finally:
                k32.CloseHandle(h)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    print("\n共检查 %d 个集合" % found)
    return 0


if __name__ == "__main__":
    sys.exit(main())
