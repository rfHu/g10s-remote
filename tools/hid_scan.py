# -*- coding: utf-8 -*-
"""
HID 设备静态扫描（Windows，纯 ctypes，无需安装任何第三方库）

用途：判断一个遥控器/飞鼠到底往电脑发什么。
关键看每个 HID 顶层集合的 UsagePage：
  0x0001 通用桌面（键盘/鼠标）
  0x000C 消费类控制（音量、播放、主页、返回等媒体键）
  0x0005 游戏手柄
  0xFF00~0xFFFF 厂商自定义 —— 语音这类私有数据只会出现在这里

用法：
  python hid_scan.py            # 表格形式列出全部 HID 集合
  python hid_scan.py --desc     # 额外打印每个设备的原始报表描述符（十六进制）
"""

import ctypes
import ctypes.wintypes as wt
import sys
import uuid

setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10
GUID_DEVINTERFACE_HID = uuid.UUID("{4D1E55B2-F16F-11CF-88CB-001111000030}")
IOCTL_HID_GET_REPORT_DESCRIPTOR = 0x000B0000


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wt.DWORD),
        ("Data2", wt.WORD),
        ("Data3", wt.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wt.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", wt.ULONG),
        ("VendorID", wt.USHORT),
        ("ProductID", wt.USHORT),
        ("VersionNumber", wt.USHORT),
    ]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wt.USHORT),
        ("UsagePage", wt.USHORT),
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
        ("NumberFeatureDataIndices", wt.USHORT),
    ]


def _bind():
    """必须声明 restype，否则 SetupDiGetClassDevsW 的 HANDLE 在 64 位上会被截断成 int32"""
    setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    setupapi.SetupDiGetClassDevsW.argtypes = [
        ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p, wt.DWORD,
    ]
    setupapi.SetupDiEnumDeviceInterfaces.restype = wt.BOOL
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wt.DWORD,
        ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ]
    setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wt.BOOL
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
        ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p, wt.DWORD, wt.DWORD, ctypes.c_void_p,
        wt.DWORD, wt.DWORD, ctypes.c_void_p,
    ]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.DeviceIoControl.restype = wt.BOOL
    kernel32.DeviceIoControl.argtypes = [
        ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, wt.DWORD,
        ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p,
    ]
    hid.HidD_GetAttributes.restype = wt.BOOL
    hid.HidD_GetAttributes.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDD_ATTRIBUTES)]
    hid.HidD_GetProductString.restype = wt.BOOL
    hid.HidD_GetProductString.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, wt.ULONG]
    hid.HidD_GetPreparsedData.restype = wt.BOOL
    hid.HidD_GetPreparsedData.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    hid.HidP_GetCaps.restype = ctypes.c_long
    hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
    hid.HidD_FreePreparsedData.restype = wt.BOOL
    hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]


def guid_to_struct(u):
    g = GUID()
    g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
    rest = u.bytes[8:]
    for i in range(8):
        g.Data4[i] = rest[i]
    return g


def open_hid(path):
    return kernel32.CreateFileW(
        path, 0,  # GENERIC_READ=0x80000000 也行，先试 0
        3, None, 3, 0, None,
    )


def get_report_descriptor(handle, maxlen=4096):
    buf = (ctypes.c_ubyte * maxlen)()
    returned = wt.DWORD(0)
    ok = kernel32.DeviceIoControl(
        handle, IOCTL_HID_GET_REPORT_DESCRIPTOR, None, 0,
        buf, maxlen, ctypes.byref(returned), None,
    )
    if not ok:
        return None
    return bytes(bytearray(buf[: returned.value]))


def parse_descriptor(data):
    """极简 HID 报表描述符解析：返回 (顶层集合列表, 报表长度表)"""
    collections = []
    stack = []
    g_usage_page = 0
    g_report_id = 0
    g_report_size = 0
    g_report_count = 0
    l_usages = []
    reports = {}
    i = 0
    n = len(data)
    while i < n:
        prefix = data[i]
        if prefix == 0xFE:  # long item
            if i + 1 >= n:
                break
            size = data[i + 1]
            i += 3 + size
            continue
        b_size = prefix & 0x03
        b_type = (prefix >> 2) & 0x03
        b_tag = (prefix >> 4) & 0x0F
        length = 4 if b_size == 3 else b_size
        val = 0
        if length:
            chunk = data[i + 1: i + 1 + length]
            if len(chunk) < length:
                break
            val = int.from_bytes(chunk, "little")
        i += 1 + length

        if b_type == 1:  # global
            if b_tag == 0x0:  # usage page
                g_usage_page = val & 0xFFFF
            elif b_tag == 0x7:  # report size
                g_report_size = val
            elif b_tag == 0x8:  # report id
                g_report_id = val
            elif b_tag == 0x9:  # report count
                g_report_count = val
        elif b_type == 2:  # local
            if b_tag in (0x0, 0x1):
                l_usages.append(val)
        elif b_type == 0:  # main
            if b_tag == 0xA:  # collection
                usage = l_usages[0] if l_usages else 0
                up = g_usage_page
                # 局部 usage 若带了显式 page（4 字节形式）则取高位
                if l_usages and (usage >> 16):
                    up = (usage >> 16) & 0xFFFF
                node = {
                    "usage_page": up,
                    "usage": usage & 0xFFFF,
                    "type": val,
                    "depth": len(stack),
                    "reports": [],
                }
                collections.append(node)
                stack.append(node)
            elif b_tag in (0x8, 0x9, 0xB):  # input / output / feature
                if stack:
                    cur = stack[-1]
                    if b_tag == 0x8:
                        cur["reports"].append(
                            (g_report_id, g_report_size, g_report_count)
                        )
                kind = {0x8: "in", 0x9: "out", 0xB: "feat"}[b_tag]
                key = (kind, g_report_id)
                bits = g_report_size * g_report_count
                reports[key] = reports.get(key, 0) + bits
            elif b_tag == 0xC:  # end collection
                if stack:
                    stack.pop()
            l_usages = []
    return collections, reports


PAGE_NAMES = {
    0x0001: "通用桌面",
    0x0002: "模拟量",
    0x0005: "游戏设备",
    0x0006: "键盘/键位表",
    0x0007: "键盘",
    0x0008: "LED",
    0x0009: "按键",
    0x000B: "条码扫描",
    0x000C: "消费类控制(媒体键)",
    0x000D: "数字化仪",
    0x000E: "触觉",
    0x0012: "眼睛/头部追踪",
    0x0014: "辅助控制",
    0x0020: "传感器",
}


def page_name(p):
    if p in PAGE_NAMES:
        return PAGE_NAMES[p]
    if 0xFF00 <= p <= 0xFFFF:
        return "厂商自定义 ★"
    return ""


def main():
    _bind()
    show_desc = "--desc" in sys.argv
    g = guid_to_struct(GUID_DEVINTERFACE_HID)
    hdev = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(g), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    )
    if not hdev or hdev == 0xFFFFFFFFFFFFFFFF:
        print("SetupDiGetClassDevs 失败, err=%d" % ctypes.get_last_error())
        return
    did = SP_DEVICE_INTERFACE_DATA()
    did.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
    idx = 0
    rows = []
    while setupapi.SetupDiEnumDeviceInterfaces(
        hdev, None, ctypes.byref(g), idx, ctypes.byref(did)
    ):
        idx += 1
        need = wt.DWORD(0)
        setupapi.SetupDiGetDeviceInterfaceDetailW(
            hdev, ctypes.byref(did), None, 0, ctypes.byref(need), None
        )
        if not need.value:
            continue
        buf = ctypes.create_string_buffer(need.value)
        # cbSize 在 64 位上为 8，32 位上为 6
        cb = wt.DWORD(8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6)
        ctypes.memmove(buf, ctypes.byref(cb), 4)
        if not setupapi.SetupDiGetDeviceInterfaceDetailW(
            hdev, ctypes.byref(did), buf, need.value, ctypes.byref(need), None
        ):
            continue
        path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
        h = open_hid(path)
        if h is None or h == -1:
            continue
        attrs = HIDD_ATTRIBUTES()
        attrs.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
        if not hid.HidD_GetAttributes(h, ctypes.byref(attrs)):
            kernel32.CloseHandle(h)
            continue
        pname = ctypes.create_unicode_buffer(256)
        hid.HidD_GetProductString(h, pname, 512)
        caps = HIDP_CAPS()
        pp = ctypes.c_void_p()
        colls = []
        rep = {}
        if hid.HidD_GetPreparsedData(h, ctypes.byref(pp)):
            hid.HidP_GetCaps(pp, ctypes.byref(caps))
            hid.HidD_FreePreparsedData(pp)
        desc = get_report_descriptor(h)
        if desc:
            colls, rep = parse_descriptor(desc)
        rows.append(
            {
                "vid": attrs.VendorID,
                "pid": attrs.ProductID,
                "ver": attrs.VersionNumber,
                "name": pname.value.strip(),
                "path": path,
                "top_page": caps.UsagePage,
                "top_usage": caps.Usage,
                "in_len": caps.InputReportByteLength,
                "colls": colls,
                "rep": rep,
                "desc": desc,
            }
        )
        kernel32.CloseHandle(h)

    print("共发现 %d 个 HID 接口\n" % len(rows))
    for r in rows:
        print("=" * 78)
        print("VID=%04X PID=%04X  ver=%04X   名称=[%s]" % (r["vid"], r["pid"], r["ver"], r["name"]))
        print("顶层集合: page=0x%04X usage=0x%04X (%s)  输入报文字节=%d"
              % (r["top_page"], r["top_usage"], page_name(r["top_page"]), r["in_len"]))
        print("路径: %s" % r["path"])
        pages = {}
        for c in r["colls"]:
            k = c["usage_page"]
            pages[k] = pages.get(k, 0) + 1
        if pages:
            print("描述符里出现的 Usage Page：")
            for k in sorted(pages):
                print("   0x%04X  %-18s x%d" % (k, page_name(k), pages[k]))
        if r["rep"]:
            for k in sorted(r["rep"]):
                bits = r["rep"][k]
                print("   报文 %s id=%d  共 %d bit ≈ %d 字节"
                      % (k[0], k[1], bits, (bits + 7) // 8))
        if show_desc and r["desc"]:
            print("原始描述符: %s" % r["desc"].hex(" "))


if __name__ == "__main__":
    main()
