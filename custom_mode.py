"""自定义模式：配置读写、快捷键捕获状态、说明页本机控制服务。

只被 bridge.py 使用。配置放在 %APPDATA%\\G10S-Remote\\config.json，
升级 exe 后不丢；说明页由本机 127.0.0.1 的随机端口提供，同源调用这些接口。
"""
import ctypes
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

# ---------------- 源键定义 ----------------
# id 带通道前缀，避免 VK 与消费者页 usage 的数字撞车。
SOURCES = [
    {"id": "vk:0xB3",        "label": "播放",       "channel": "vk",       "code": 0xB3},
    {"id": "vk:0xA6",        "label": "返回（短按）", "channel": "vk",       "code": 0xA6},
    {"id": "vk:0xAC",        "label": "返回（长按）", "channel": "vk",       "code": 0xAC},
    {"id": "vk:0x5D",        "label": "灯泡",       "channel": "vk",       "code": 0x5D},
    {"id": "vk:0x21",        "label": "PgUp",       "channel": "vk",       "code": 0x21},
    {"id": "vk:0x22",        "label": "PgDn",       "channel": "vk",       "code": 0x22},
    {"id": "vk:0x26",        "label": "上",         "channel": "vk",       "code": 0x26},
    {"id": "vk:0x28",        "label": "下",         "channel": "vk",       "code": 0x28},
    {"id": "vk:0x25",        "label": "左",         "channel": "vk",       "code": 0x25},
    {"id": "vk:0x27",        "label": "右",         "channel": "vk",       "code": 0x27},
    {"id": "vk:0x2E",        "label": "DEL",        "channel": "vk",       "code": 0x2E},
    {"id": "consumer:0x41",  "label": "OK（飞鼠关）", "channel": "consumer", "code": 0x41},
    {"id": "consumer:0xCF",  "label": "麦克风",     "channel": "consumer", "code": 0xCF},
    {"id": "mouse:left",     "label": "OK（飞鼠开）", "channel": "mouse",    "code": 0x01},
]
SOURCE_BY_ID = {s["id"]: s for s in SOURCES}

VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_LWIN, VK_RWIN, VK_WIN_ALT = 0x5B, 0x5C, 0xFC  # 0xFC 是某些设备上报的 Win（VK_NONAME 保留值）
WIN_KEYS = {VK_LWIN, VK_RWIN, VK_WIN_ALT}
MOD_VKS = {VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN,
           0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, VK_RWIN, VK_WIN_ALT}
CANONICAL_MODS = (VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN)  # 输出顺序固定
MOD_LABEL = {VK_CONTROL: "Ctrl", VK_SHIFT: "Shift", VK_MENU: "Alt", VK_LWIN: "Win"}
MOUSE_BUTTONS = {"left": "鼠标左键", "right": "鼠标右键", "middle": "鼠标中键"}
# 静音、音量键按需求不参与改键；作为目标也要挡住，免得从后门绕过去。
FORBIDDEN_TARGETS = {0xAD, 0xAE, 0xAF}

VK_NAME = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x13: "Pause",
    0x14: "CapsLock", 0x1B: "Esc", 0x10: "Shift", 0x11: "Ctrl", 0x12: "Alt",
    0x20: "Space", 0x21: "PgUp", 0x22: "PgDn", 0x23: "End", 0x24: "Home",
    0x25: "←", 0x26: "↑", 0x27: "→", 0x28: "↓", 0x2C: "PrintScreen",
    0x2D: "Insert", 0x2E: "Delete", 0x2F: "Help",
    0x5B: "Win", 0x5C: "Win", 0xFC: "Win", 0x5D: "菜单", 0x5F: "Sleep",
    0x6A: "小键盘*", 0x6B: "小键盘+", 0x6C: "小键盘Enter",
    0x6D: "小键盘-", 0x6E: "小键盘.", 0x6F: "小键盘/",
    0x90: "NumLock", 0x91: "ScrollLock",
    0xA0: "左Shift", 0xA1: "右Shift", 0xA2: "左Ctrl", 0xA3: "右Ctrl",
    0xA4: "左Alt", 0xA5: "右Alt",
    0xA6: "返回", 0xA7: "前进", 0xA8: "刷新", 0xA9: "停止", 0xAA: "搜索",
    0xAB: "收藏", 0xAC: "Home", 0xAD: "静音", 0xAE: "音量-", 0xAF: "音量+",
    0xB0: "下一曲", 0xB1: "上一曲", 0xB2: "停止播放", 0xB3: "播放/暂停",
    0xB4: "邮件", 0xB5: "媒体选择", 0xB6: "应用1", 0xB7: "应用2",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
    0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'", 0xE2: "\\",
}
for _k in range(0x30, 0x3A):
    VK_NAME[_k] = chr(_k)
for _k in range(0x41, 0x5B):
    VK_NAME[_k] = chr(_k)
for _k in range(0x60, 0x6A):
    VK_NAME[_k] = "小键盘%d" % (_k - 0x60)
for _k in range(0x70, 0x88):
    VK_NAME[_k] = "F%d" % (_k - 0x6F)


def normalize_mod(vk):
    """左右修饰键统一成规范 VK；非修饰键返回 None。"""
    if vk in (0xA0, 0xA1):
        return VK_SHIFT
    if vk in (0xA2, 0xA3):
        return VK_CONTROL
    if vk in (0xA4, 0xA5):
        return VK_MENU
    if vk in WIN_KEYS:
        return VK_LWIN
    if vk in CANONICAL_MODS:
        return vk
    return None


def format_combo(mods, vk):
    parts = [MOD_LABEL[m] for m in CANONICAL_MODS if m in mods]
    parts.append(VK_NAME.get(vk, "0x%02X" % vk))
    return "+".join(parts)


def normalize_mods(raw):
    return sorted({normalize_mod(int(m)) for m in (raw or [])} - {None},
                  key=CANONICAL_MODS.index)


def format_action(action):
    """动作 -> 给用户看的名字，配置和执行日志共用。"""
    mods = [MOD_LABEL[m] for m in CANONICAL_MODS if m in action.get("mods", [])]
    kind = action.get("kind")
    if kind == "key":
        vk = action.get("vk", 0)
        tail = VK_NAME.get(vk, "0x%02X" % vk)
    elif kind == "wheel":
        tail = "滚轮向上" if action.get("delta", 0) > 0 else "滚轮向下"
    elif kind == "button":
        tail = MOUSE_BUTTONS.get(action.get("button"), "鼠标按键")
    else:
        tail = "未知动作"
    return "+".join(mods + [tail])


def normalize_action(raw):
    """把一条动作规整成 kind 明确的字典；不合法返回 None。"""
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "key")
    try:
        mods = normalize_mods(raw.get("mods"))
        if kind == "key":
            vk = int(raw.get("vk", 0))
            canon = normalize_mod(vk)
            if canon:               # 0xA0/0xA1/0xA2/.../0xFC 统一成规范修饰键
                vk = canon
            if vk <= 0 or vk in FORBIDDEN_TARGETS:
                return None
            mods = [m for m in mods if m != vk]   # 修饰键作末键时不能同时留在修饰键里
            action = {"kind": "key", "mods": mods, "vk": vk}
        elif kind == "wheel":
            delta = int(raw.get("delta", 0))
            if delta == 0:
                return None
            action = {"kind": "wheel", "mods": mods,
                      "delta": 120 if delta > 0 else -120}
        elif kind == "button":
            button = str(raw.get("button") or "").lower()
            if button not in MOUSE_BUTTONS:
                return None
            action = {"kind": "button", "mods": mods, "button": button}
        else:
            return None
    except (TypeError, ValueError):
        return None
    action["label"] = format_action(action)
    return action


# ---------------- 配置 ----------------
CONFIG_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                          "G10S-Remote")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")


def default_config():
    # 默认关闭：里面还没有任何用户设定，不能悄悄接管快捷切换，必须用户主动开启。
    return {"schema": 1, "custom_enabled": False, "bindings": {}}


def normalize_config(raw):
    """只保留合法源键与合法目标，坏数据不会让程序启动时炸掉。"""
    cfg = default_config()
    if not isinstance(raw, dict):
        return cfg
    cfg["custom_enabled"] = bool(raw.get("custom_enabled", True))
    bindings = {}
    raw_bindings = raw.get("bindings")
    if isinstance(raw_bindings, dict):
        for src_id, entry in raw_bindings.items():
            if src_id not in SOURCE_BY_ID or not isinstance(entry, dict):
                continue
            raw_actions = entry.get("actions")
            if isinstance(raw_actions, list):
                actions = [a for a in (normalize_action(x) for x in raw_actions) if a]
            else:
                # 旧配置只有 mods + vk，按键盘动作迁移，已有的 Ctrl+Win 等不会丢。
                legacy = normalize_action({"kind": "key",
                                           "mods": entry.get("mods"),
                                           "vk": entry.get("vk", 0)})
                actions = [legacy] if legacy else []
            if not actions:
                continue
            note = entry.get("note")
            binding = {
                "actions": actions,
                "label": " / ".join(a["label"] for a in actions),
                "hold": bool(entry.get("hold", False)),
                "note": str(note)[:80] if note is not None else "",
            }
            key_action = next((a for a in actions if a["kind"] == "key"), None)
            if key_action:   # 兼容还读 mods/vk 的旧代码与日志
                binding["mods"] = key_action["mods"]
                binding["vk"] = key_action["vk"]
            bindings[src_id] = binding
    cfg["bindings"] = bindings
    return cfg


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return normalize_config(json.load(f))
    except (OSError, ValueError):
        return default_config()


def save_config(cfg):
    cfg = normalize_config(cfg)
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)
    return cfg


def build_tables(cfg):
    """把配置转成 bridge.py 的 action 元组：{源键: (kind, target, 文案[, 源ID])}。

    hold=True 的绑定用 kind="hold"，第 4 项是源键 ID，供保持状态判断「同键再按」。
    """
    vk_table, consumer_table, mouse_table = {}, {}, {}
    for src_id, entry in cfg.get("bindings", {}).items():
        src = SOURCE_BY_ID.get(src_id)
        if src is None:
            continue
        sub_actions = []
        for action in entry.get("actions", []):
            mods = tuple(action.get("mods") or [])
            label = "%s → %s" % (src["label"], action.get("label") or action.get("kind"))
            if action["kind"] == "key":
                if entry.get("hold"):
                    sub_actions.append(("hold", (mods, action["vk"]),
                                        label + "（保持按下）", src_id))
                else:
                    sub_actions.append(("chord", (mods, action["vk"]), label))
            elif action["kind"] == "wheel":
                sub_actions.append(("wheel", (mods, action["delta"]), label))
            elif action["kind"] == "button":
                sub_actions.append(("button", (mods, action["button"]), label))
        if not sub_actions:
            continue
        action = (sub_actions[0] if len(sub_actions) == 1
                  else ("multi", sub_actions, entry.get("label") or src["label"]))
        if src["channel"] == "vk":
            vk_table[src["code"]] = action
        elif src["channel"] == "consumer":
            consumer_table[src["code"]] = action
        else:
            mouse_table[src["code"]] = action
    return vk_table, consumer_table, mouse_table


def hold_sources(cfg):
    """当前配置了保持按下的源键 ID 集合。"""
    return {sid for sid, e in cfg.get("bindings", {}).items() if e.get("hold")}


# ---------------- 快捷键捕获 ----------------
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_u32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
_u32.GetAsyncKeyState.restype = ctypes.c_short


def current_modifiers():
    """捕获开始时把已经按住的修饰键算进去（用户常常先按 Ctrl 再点按钮）。"""
    pairs = ((0xA0, VK_SHIFT), (0xA1, VK_SHIFT),
             (0xA2, VK_CONTROL), (0xA3, VK_CONTROL),
             (0xA4, VK_MENU), (0xA5, VK_MENU),
             (VK_LWIN, VK_LWIN), (0x5C, VK_LWIN))
    return {canon for raw, canon in pairs if _u32.GetAsyncKeyState(raw) & 0x8000}


class CaptureState:
    """低级钩子把按键喂进来；说明页轮询结果。全程不加锁于钩子之外。"""

    def __init__(self):
        self.lock = threading.RLock()
        self.active = False
        self.source = None
        self.status = "idle"
        self.mods = set()
        self.result = None
        self.deadline = 0.0

    def start(self, source_id, timeout=10.0):
        if source_id not in SOURCE_BY_ID:
            raise ValueError("未知源键: %r" % (source_id,))
        with self.lock:
            self.active = True
            self.source = source_id
            self.status = "waiting"
            self.mods = current_modifiers()
            self.result = None
            self.deadline = time.time() + timeout

    def mod_down(self, vk):
        canon = normalize_mod(vk)
        if canon is None:
            return False
        with self.lock:
            if self.active:
                self.mods.add(canon)
                return True
        return False

    def mod_up(self, vk):
        canon = normalize_mod(vk)
        if canon is None:
            return False
        with self.lock:
            if self.active:
                self.mods.discard(canon)
                return True
        return False

    def key(self, vk):
        """非修饰键按下：组合到此结束。"""
        vk = normalize_mod(vk) or vk   # 左/右修饰键变体统一成规范值
        with self.lock:
            if not self.active:
                return False
            mods = tuple(m for m in sorted(self.mods, key=CANONICAL_MODS.index)
                         if m != vk)   # 修饰键本身作末键时不要既当修饰又当末键
            self.result = {"mods": mods, "vk": vk,
                           "label": format_combo(mods, vk)}
            self.active = False
            self.status = "done"
            return True

    def cancel(self):
        with self.lock:
            self.active = False
            self.source = None
            self.status = "idle"
            self.mods.clear()
            self.result = None

    def check_timeout(self):
        with self.lock:
            if self.active and time.time() > self.deadline:
                self.active = False
                self.status = "timeout"
                self.mods.clear()

    def poll(self):
        with self.lock:
            if self.status == "done":
                combo = self.result
                self.status = "idle"
                self.result = None
                self.source = None
                return {"state": "done", "combo": combo}
            if self.status == "timeout":
                self.status = "idle"
                self.source = None
                return {"state": "timeout"}
            if self.active:
                return {"state": "waiting", "source": self.source}
            return {"state": "idle"}


# ---------------- 本机控制服务 ----------------
class ControlServer:
    def __init__(self, help_path, on_config_saved=None, on_event=None,
                 on_set_mode=None, on_get_mode=None):
        self.help_path = help_path
        self.state = CaptureState()
        self.token = secrets.token_hex(16)
        self.on_config_saved = on_config_saved
        self.on_event = on_event or (lambda msg: None)
        self.on_set_mode = on_set_mode
        self.on_get_mode = on_get_mode
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self.httpd.server_address[1]
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self.httpd.serve_forever,
                                        name="g10s-control", daemon=True)
        self._thread.start()

    def stop(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass

    def _handler(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                pass

            def _send(self, code, body, content_type="application/json; charset=utf-8"):
                data = body.encode("utf-8") if isinstance(body, str) else body
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _json(self, code, obj):
                self._send(code, json.dumps(obj, ensure_ascii=False))

            def _authorized(self):
                return self.headers.get("X-G10S-Token") == owner.token

            def _body(self):
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    if n <= 0 or n > 1024 * 1024:
                        return {}
                    return json.loads(self.rfile.read(n).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return {}

            def do_GET(self):
                path = urlsplit(self.path).path
                if path in ("/", "/help.html"):
                    try:
                        with open(owner.help_path, encoding="utf-8") as f:
                            html = f.read()
                    except OSError as e:
                        owner.on_event("读取说明页失败: %r" % (e,))
                        self._send(500, "help.html missing", "text/plain; charset=utf-8")
                        return
                    # 静态打开（file://）时占位符不会被替换，页面会给出提示。
                    html = html.replace("{{SESSION_TOKEN}}", owner.token)
                    self._send(200, html, "text/html; charset=utf-8")
                    return
                if path == "/api/config":
                    if not self._authorized():
                        self._json(403, {"error": "forbidden"})
                        return
                    self._json(200, {"ok": True, "config": load_config()})
                    return
                if path == "/api/mode":
                    if not self._authorized():
                        self._json(403, {"error": "forbidden"})
                        return
                    mode = owner.on_get_mode() if owner.on_get_mode else None
                    self._json(200, {"ok": True, "mode": mode})
                    return
                if path == "/api/capture/result":
                    if not self._authorized():
                        self._json(403, {"error": "forbidden"})
                        return
                    self._json(200, {"ok": True, "result": owner.state.poll()})
                    return
                self._json(404, {"error": "not found"})

            def do_POST(self):
                path = urlsplit(self.path).path
                if not self._authorized():
                    self._json(403, {"error": "forbidden"})
                    return
                payload = self._body()
                if path == "/api/config":
                    cfg = save_config(payload.get("config", payload))
                    if owner.on_config_saved:
                        owner.on_config_saved(cfg)
                    owner.on_event("自定义配置已保存")
                    self._json(200, {"ok": True, "config": cfg})
                elif path == "/api/capture/start":
                    source = payload.get("source")
                    try:
                        owner.state.start(source)
                        owner.on_event("开始捕获快捷键：%s" %
                                       SOURCE_BY_ID[source]["label"])
                        self._json(200, {"ok": True})
                    except (ValueError, KeyError) as e:
                        self._json(400, {"error": str(e)})
                elif path == "/api/mode":
                    mode = payload.get("mode")
                    ok = bool(owner.on_set_mode and owner.on_set_mode(mode))
                    owner.on_event("说明页切换模式：%s%s" % (mode, "（已拒绝）" if not ok else ""))
                    self._json(200 if ok else 403, {"ok": ok})
                elif path == "/api/capture/cancel":
                    owner.state.cancel()
                    self._json(200, {"ok": True})
                else:
                    self._json(404, {"error": "not found"})

        return Handler
