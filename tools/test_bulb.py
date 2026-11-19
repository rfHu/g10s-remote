import os
import sys
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bridge

bridge.CFG = types.SimpleNamespace(hold_ms=300, dbl_ms=250, dry_run=True)
bridge.HWND = None

calls = []
bridge.tap_key = lambda vk, extra=0: calls.append(("enter", vk))
bridge.tap_right_click = lambda: calls.append(("right",))


def reset():
    bridge._bulb_down_at = 0.0
    bridge._bulb_hold_fired = False
    bridge._bulb_last_tap = 0.0
    calls.clear()


def case(name, expect):
    ok = calls == expect
    print("%-26s %s  实际=%s" % (name, "通过" if ok else "失败", calls))
    return ok


allok = True
# 1 单击：短按 → 回车
reset()
bridge.on_bulb_release(80)
allok &= case("单击 → 回车", [("enter", 0x0D)])

# 2 双击：第二下被吞
bridge.on_bulb_release(80)
allok &= case("双击 → 只发一次回车", [("enter", 0x0D)])

# 3 长按：定时器先触发
reset()
bridge._bulb_down_at = time.monotonic() - 0.4
bridge.fire_bulb_hold()
bridge.on_bulb_release(400)
allok &= case("长按（定时器先到）→ 右键", [("right",)])

# 4 长按：松开先到，靠实测时长兜底
reset()
bridge.on_bulb_release(400)
allok &= case("长按（松开先到）→ 右键", [("right",)])

# 5 长按之后立刻单击仍然正常
bridge.on_bulb_release(80)
allok &= case("长按后单击 → 回车", [("right",), ("enter", 0x0D)])

# 6 阈值边界：299ms 仍是单击
reset()
bridge.on_bulb_release(299)
allok &= case("299ms → 单击", [("enter", 0x0D)])

# 7 按住时的重复按下不重复计数
reset()
bridge._bulb_down_at = 0.0
bridge.on_tap_hold_key(True)
first = bridge._bulb_down_at
bridge.on_tap_hold_key(True)
allok &= case("重复按下不重置计时", []) and (bridge._bulb_down_at == first)

# 8 双击窗口之后的第三次按下重新计为单击
reset()
bridge.on_bulb_release(80)
bridge._bulb_last_tap = time.monotonic() - 0.4      # 模拟隔了 400ms
bridge.on_bulb_release(80)
allok &= case("间隔 400ms 的两次按 → 两次回车", [("enter", 0x0D), ("enter", 0x0D)])

print("\n全部通过" if allok else "\n有失败项")
