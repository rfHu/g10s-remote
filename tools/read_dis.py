"""
read_dis.py —— 读取蓝牙遥控器的设备信息（DIS）与 ATVV 语音服务特征

用途：
    1. 拿到固件版本（Windows 缓存里没有，只能主动连 GATT 读）
    2. 确认 ATVV 语音服务的特征是否齐全，判断语音链路有没有戏

准备：
    pip install bleak

运行：
    python read_dis.py

注意：
    遥控器会休眠。连不上就先按一下它任意一个键唤醒，再重跑。
    连接时会短暂抢占蓝牙链路，遥控器按键可能停顿一两秒，属正常。
"""

import asyncio

from bleak import BleakClient, BleakScanner

TARGET_ADDR = "C4:19:D1:9B:34:D1"

DIS = "0000180a"
ATVV = "ab5e0001"
BATTERY = "0000180f"

DIS_NAMES = {
    "00002a29": "厂商名 Manufacturer",
    "00002a24": "型号 Model Number",
    "00002a25": "序列号 Serial Number",
    "00002a27": "硬件版本 Hardware Revision",
    "00002a26": "固件版本 Firmware Revision",
    "00002a28": "软件版本 Software Revision",
    "00002a2a": "法规认证 IEEE Regulatory",
    "00002a50": "PnP ID",
    "00002a19": "电量 Battery Level",
}


def label(uuid: str) -> str:
    return DIS_NAMES.get(uuid.lower()[:8], "")


def decode(raw: bytes) -> str:
    if not raw:
        return "(空)"
    try:
        txt = raw.decode("utf-8").strip("\x00").strip()
        if txt and txt.isprintable():
            return "{}   [hex: {}]".format(repr(txt), raw.hex())
    except UnicodeDecodeError:
        pass
    return "[hex: {}]".format(raw.hex())


def decode_pnp(raw: bytes) -> str:
    if len(raw) < 7:
        return "[hex: {}]".format(raw.hex())
    src = raw[0]
    vid = int.from_bytes(raw[1:3], "little")
    pid = int.from_bytes(raw[3:5], "little")
    ver = int.from_bytes(raw[5:7], "little")
    return "VID=0x{:04X}  PID=0x{:04X}  产品版本=0x{:04X} (vendorSource={})".format(
        vid, pid, ver, src
    )


async def find_target():
    print("[*] 扫描 10 秒，目标 {}".format(TARGET_ADDR))
    dev = await BleakScanner.find_device_by_filter(
        lambda d, ad: d.address.upper() == TARGET_ADDR, timeout=10.0
    )
    if dev is not None:
        return dev
    print("[!] 没扫到目标。附近可见设备如下（先按一下遥控器任意键唤醒再重跑）：")
    found = await BleakScanner.discover(timeout=6.0, return_adv=True)
    for addr, pair in found.items():
        d, adv = pair
        print("    {}  {}".format(addr, d.name or adv.local_name or "(无名)"))
    return None


async def main() -> int:
    dev = await find_target()
    if dev is None:
        return 1

    print("[*] 连接 {} ...".format(dev.address))
    async with BleakClient(dev) as client:
        print("[+] 已连接：{}\n".format(client.is_connected))

        print("=== GATT 服务与特征 ===")
        for svc in client.services:
            print("  svc {}".format(svc.uuid))
            for ch in svc.characteristics:
                print(
                    "      char {}  props={}  {}".format(
                        ch.uuid, ",".join(ch.properties), label(ch.uuid)
                    )
                )

        for prefix, title in ((DIS, "DIS 设备信息"), (ATVV, "ATVV 语音服务"), (BATTERY, "电量")):
            svc = None
            for s in client.services:
                if s.uuid.lower().startswith(prefix):
                    svc = s
                    break
            print("\n=== {} ===".format(title))
            if svc is None:
                print("  (设备没有这个服务)")
                continue
            if not svc.characteristics:
                print("  (服务下没有特征)")
            for ch in svc.characteristics:
                name = label(ch.uuid)
                if "read" not in ch.properties:
                    print("  {}  {}  props={}  (不可读)".format(ch.uuid, name, ",".join(ch.properties)))
                    continue
                try:
                    raw = await client.read_gatt_char(ch)
                except Exception as exc:
                    print("  {}  {}  读取失败: {}".format(ch.uuid, name, exc))
                    continue
                if ch.uuid.lower().startswith("00002a50"):
                    value = decode_pnp(raw)
                else:
                    value = decode(raw)
                print("  {}  {}\n      -> {}".format(ch.uuid, name, value))

    print("\n[*] 完成。断开连接。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
