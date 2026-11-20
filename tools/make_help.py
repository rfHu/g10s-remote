"""生成按键说明页 help.html —— 单文件，所有图片以 base64 内嵌。

产物会被 build.bat 打进 exe，运行时由 bridge.py 的 open_help() 打开，
不产生任何外部文件。

设计要点：
- 遥控器图是从原图裁掉白边后的 assets/remote-crop.jpg，本体撑满画布；
- 卡片用按键锚点定位，JS 兜底推开重叠；
- 连线在运行时按 DOM 实际像素坐标绘制（viewBox = 真实尺寸），
  因此线宽恒定、端点精准、窗口缩放自动重算；
- hover 卡片会高亮对应连线与遥控器上的按键光圈。

用法：python tools/make_help.py
"""
import base64
import html as html_mod
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REMOTE = os.path.join(ROOT, "assets", "remote-crop.jpg")
ICON_DIALOG = os.path.join(ROOT, "app-preview-dialog.png")
ICON_WATCH = os.path.join(ROOT, "app-preview-watch.png")
WHALE = os.path.join(ROOT, "assets", "whale-chan.png")
QR_WECHAT = os.path.join(ROOT, "assets", "qr-wechat-crop.jpg")
QR_ALIPAY = os.path.join(ROOT, "assets", "qr-alipay-crop.jpg")
VERSION_FILE = os.path.join(ROOT, "VERSION")
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")
OUT = os.path.join(ROOT, "help.html")


def read_version():
    """版本号单一来源：VERSION 文件。禁止在别处手写版本号。"""
    with open(VERSION_FILE, encoding="utf-8") as f:
        return f.read().strip()


def data_uri(path, mime):
    with open(path, "rb") as f:
        return "data:%s;base64,%s" % (mime, base64.b64encode(f.read()).decode())


# 按键中心点，单位是「裁剪后遥控器图」的百分比 —— 从叠加 10% 网格的标定图上读出。
# 连线终点就用这些坐标定位，务必与图片保持一致。
KEY = {
    "power": (28.0, 12.0),
    "play": (30.0, 16.5),
    "back": (70.0, 16.5),   # 右上角飞鼠 / 陀螺仪开关，未接管
    "ring": (50.0, 27.5),   # 方向环中心圆圈，OK 键
    "updown": (50.0, 21.0), # 方向环上箭头：上 / 下（观看模式=滚轮）
    "dir": (42.0, 29.0),    # 方向环左箭头：左 / 右方向
    "ret": (30.0, 41.0),    # 房子 + 弯箭头
    "lamp": (70.0, 41.0),   # ≡ + 灯泡
    "pgup": (30.0, 48.0),
    "pgdn": (30.0, 55.0),
    "pgpair": (30.0, 51.5),  # PgUp / PgDn 合起来讲时的锚点
    "mic": (50.0, 54.0),
    "volup": (75.0, 48.0),
    "voldn": (75.0, 55.0),
    "volpair": (75.0, 51.5),  # VOL+ / VOL- 合起来讲时的锚点
    "del": (43.0, 65.0),
    "mute": (62.0, 65.0),
}

# 每行：(按键锚点, 按键名, 映射动作, 说明, 是否被程序接管)
# kind = "on" 接管（实线 + 主题色）/ "off" 原生（虚线 + 灰）
DIALOG_LEFT = [
    ("power", "电源", "—", "红外信号，蓝牙连接下无效", "off"),
    ("play", "播放", "Ctrl+N", "新建对话", "on"),
    ("dir", "左 / 右方向", "—", "移动光标", "off"),
    ("ret", "返回", "Esc / 右键", "单击打断 AI 输出；按住不放出鼠标右键", "on"),
    ("pgpair", "PgUp / PgDn", "Ctrl+[ / Ctrl+]", "上一个 / 下一个对话", "on"),
    ("del", "DEL", "—", "退格，删掉刚打错的字", "off"),
]
DIALOG_RIGHT = [
    ("back", "飞鼠开关", "—", "开启陀螺仪控制鼠标；开启后 OK 键变鼠标左键", "off"),
    ("ring", "方向环 / OK", "方向键 / Alt+Tab", "走光标；中间 OK 切窗口", "on"),
    ("lamp", "灯泡", "回车", "发送消息", "on"),
    ("mic", "麦克风", "Ctrl+Win", "语音输入，按一下开始、再按一下结束", "on"),
    ("volpair", "VOL + / −", "—", "音量加减", "off"),
    ("mute", "静音", "—", "切换模式，不动音量", "on"),
]
WATCH_LEFT = [
    ("power", "电源", "—", "红外信号，蓝牙连接下无效", "off"),
    ("play", "播放", "播放 / 暂停", "原生媒体控制", "on"),
    ("dir", "左 / 右方向", "—", "光标移动、视频进退", "off"),
    ("ret", "返回", "浏览器后退 / 右键", "单击回到上一页；按住不放出鼠标右键", "on"),
    ("pgpair", "PgUp / PgDn", "Ctrl+Tab / Ctrl+Shift+Tab", "下一个 / 上一个标签页", "on"),
    ("del", "DEL", "—", "退格", "off"),
]
WATCH_RIGHT = [
    ("back", "飞鼠开关", "—", "开启陀螺仪控制鼠标；开启后 OK 键变鼠标左键", "off"),
    ("updown", "上 / 下", "滚轮", "滚动页面，比翻页更顺", "on"),
    ("ring", "OK", "Alt+Tab", "切窗口（飞鼠关时）", "on"),
    ("lamp", "灯泡", "回车", "确认输入框里的内容", "on"),
    ("mic", "麦克风", "Ctrl+Win", "语音输入", "on"),
    ("volpair", "VOL + / −", "—", "音量加减", "off"),
    ("mute", "静音", "—", "切换模式，不动音量", "on"),
]


def build_cards(rows):
    """生成一侧的卡片。top 先用按键锚点的 y%，运行时 JS 会推开重叠。"""
    out = []
    for key, name, action, desc, kind in rows:
        pos = KEY[key][1]
        out.append(
            '<div class="card %s" data-key="%s" data-kind="%s" data-pos="%.1f" '
            'style="top:%.1f%%">'
            '<div class="r1"><span class="k">%s</span><span class="v">%s</span></div>'
            '<div class="d">%s</div></div>'
            % (kind, key, kind, pos, pos, name, action, desc))
    return "\n".join(out)


def build_anchors():
    """遥控器图上的透明锚点，中心对准按键，供连线与 hover 高亮定位。"""
    out = []
    for key, (x, y) in KEY.items():
        out.append('<i class="anchor" data-key="%s" style="left:%.1f%%;top:%.1f%%"></i>'
                   % (key, x, y))
    return "\n".join(out)


def build_changelog():
    """把 CHANGELOG.md 解析成版本日志卡片（数据只有 CHANGELOG.md 一份来源）。"""
    with open(CHANGELOG, encoding="utf-8") as f:
        text = f.read()
    # 每个版本：## vX.Y.Z — YYYY-MM-DD，下面是 - 条目
    blocks = re.split(r"^##\s+", text, flags=re.M)[1:]
    out = []
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        title = lines[0]
        # 版本号允许带预发布后缀：v0.0.4-pre、v0.1.0-rc1
        m = re.match(r"(v[\d.]+(?:-[0-9A-Za-z.]+)?)\s*[—-]\s*(\d{4}-\d{2}-\d{2})", title)
        if m:
            ver, date = m.group(1), m.group(2)
        else:
            ver, date = title, ""
        items = [ln[1:].strip() for ln in lines[1:] if ln.startswith("- ")]
        lis = "\n".join("<li>%s</li>" % html_mod.escape(it) for it in items)
        out.append(
            '<div class="ver-block"><div class="ver-head">'
            '<span class="ver-tag">%s</span><span class="ver-date">%s</span></div>'
            '<ul>%s</ul></div>' % (html_mod.escape(ver), html_mod.escape(date), lis))
    return "\n".join(out)


TPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>G10S 遥控器按键说明</title>
<style>
  :root {
    --ink:#1f2328; --muted:#57606a; --faint:#8b949e;
    --line:#e3e5e8; --bg:#f4f5f7;
  }
  * { box-sizing:border-box; }
  body {
    margin:0; padding:0 0 64px; background:var(--bg); color:var(--ink);
    font:14px/1.6 "Segoe UI", "Microsoft YaHei", system-ui, sans-serif;
  }
  .topbar { background:#fff; border-bottom:1px solid var(--line); }
  .topbar-inner { max-width:1120px; margin:0 auto; padding:20px 28px 0; }
  h1 { margin:0 0 2px; font-size:20px; font-weight:650; letter-spacing:.2px; }
  h1 .ver { font-size:12px; font-weight:600; color:#57606a; background:#eef0f3;
            border:1px solid #dcdfe4; border-radius:6px; padding:2px 8px;
            margin-left:10px; vertical-align:middle; letter-spacing:0; }
  .sub { margin:0 0 16px; color:var(--muted); font-size:12.5px; }
  nav { display:flex; gap:6px; justify-content:center; }
  nav button {
    border:1px solid transparent; border-bottom:0; background:none;
    padding:9px 17px; border-radius:9px 9px 0 0; font:inherit; font-weight:550;
    color:var(--muted); cursor:pointer; transition:background .15s,color .15s;
  }
  nav button:hover { color:var(--ink); background:#f6f7f9; }
  nav button.on { color:var(--ink); background:var(--bg); border-color:var(--line); }
  main { max-width:1120px; margin:0 auto; padding:26px 28px; }
  section { display:none; }
  section.on { display:block; }
  .lead { margin:0; color:var(--muted); font-size:13.5px; }
  .lead b { color:var(--ink); }

  /* ---- 模式切换页 ---- */
  .modes { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px;
           margin:22px auto 0; max-width:860px; }
  section#p1 > .lead { text-align:center; }
  .mode {
    display:flex; flex-direction:column; align-items:center; text-align:center;
    background:#fff; border:1px solid var(--line); border-radius:14px; padding:24px 18px;
    box-shadow:0 1px 3px rgba(27,31,36,.04); min-height:216px;
  }
  .mode img { width:76px; height:76px; border-radius:18px; flex:none;
              box-shadow:0 2px 10px rgba(27,31,36,.14); }
  .mode h3 { margin:14px 0 5px; font-size:16px; }
  .mode p { margin:0; color:var(--muted); font-size:12.5px; line-height:1.55; }
  .mode.soon {
    justify-content:center; background:#fafbfc; border-style:dashed;
    box-shadow:none; color:var(--faint);
  }
  .mode.soon .soon-ico { font-size:34px; line-height:1; opacity:.55; }
  .mode.soon h3 { color:var(--muted); }
  .switch-how {
    position:relative; max-width:860px; background:#fff; border:1px solid var(--line);
    border-radius:14px; padding:20px 24px 18px 26px; margin:18px auto 0; overflow:hidden;
  }
  .switch-how::before { content:""; position:absolute; left:0; top:0; bottom:0;
                        width:3px; background:#5b8def; }
  .switch-how h3 { margin:0 0 10px; font-size:15px; }
  .switch-how ol { margin:0; padding-left:20px; color:#38414a; }
  .switch-how li { margin:6px 0; }
  kbd {
    display:inline-block; padding:1px 7px; margin:0 2px; border-radius:5px;
    background:#eef0f3; border:1px solid #dcdfe4;
    font:12px/1.7 ui-monospace, Consolas, monospace;
  }
  .hint { margin-top:14px; padding:12px 14px; border-radius:9px;
          background:#fff8e6; border:1px solid #f0dfae; color:#6b5417; font-size:12.5px; }
  .info-card {
    position:relative; max-width:860px; background:#fff; border:1px solid var(--line);
    border-radius:14px; padding:18px 24px 18px 26px; margin:14px auto 0; overflow:hidden;
  }
  .info-card::before { content:""; position:absolute; left:0; top:0; bottom:0;
                       width:3px; background:#24A15A; }
  .info-card h3 { margin:0 0 8px; font-size:15px; }
  .info-card p { margin:0; color:#38414a; font-size:13px; line-height:1.7; }

  /* ---- 连线图 ---- */
  .stage {
    --accent:#24A15A;
    position:relative; display:grid; grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);
    gap:30px; align-items:stretch; margin-top:24px;
  }
  .stage[data-mode="watch"] { --accent:#2563C7; }
  .side { position:relative; min-width:0; }
  .remote-frame {
    position:relative; height:720px; aspect-ratio:379/1164; margin:0 auto;
    border-radius:20px; background:#fafbfc;
    box-shadow:0 3px 18px rgba(27,31,36,.10), 0 0 0 1px rgba(27,31,36,.06);
  }
  .remote-frame img { display:block; width:100%; height:100%;
                      object-fit:cover; border-radius:20px; }
  .anchor {
    position:absolute; width:15px; height:15px; margin:-7.5px 0 0 -7.5px;
    border-radius:50%; border:1.5px solid var(--accent);
    background:rgba(255,255,255,.6); z-index:3; pointer-events:none;
    opacity:0; transform:scale(.6); transition:opacity .18s,transform .18s;
    box-shadow:0 0 0 4px rgba(255,255,255,.35);
  }
  .anchor.show { opacity:1; transform:scale(1); }
  .wires { position:absolute; inset:0; width:100%; height:100%;
           z-index:2; pointer-events:none; overflow:visible; }
  .wire { fill:none; stroke:var(--accent); stroke-width:1.6; stroke-linecap:round;
          opacity:.34; transition:opacity .18s,stroke-width .18s; }
  .wire.hot { opacity:.95; stroke-width:2.6; }
  .wire.off { stroke:#9aa1a9; stroke-dasharray:4 4; opacity:.42; }
  .wire.off.hot { opacity:.95; }
  .dot { fill:var(--accent); opacity:.65; transition:opacity .18s; }
  .dot.off { fill:#9aa1a9; opacity:.7; }
  .dot.hot { opacity:1; }
  .card {
    position:absolute; left:0; right:0; transform:translateY(-50%);
    background:#fff; border:1px solid var(--line); border-radius:11px;
    padding:9px 13px; box-shadow:0 1px 3px rgba(27,31,36,.05);
    z-index:4; cursor:default;
    transition:border-color .18s,box-shadow .18s;
  }
  .card:hover { border-color:var(--accent); box-shadow:0 5px 16px rgba(27,31,36,.11); }
  .card .r1 { display:flex; align-items:baseline; justify-content:space-between; gap:10px; }
  .card .k { font-size:11.5px; color:var(--faint); white-space:nowrap; }
  .card .v { font-size:14px; font-weight:650; color:var(--accent); white-space:nowrap; }
  .card .d { font-size:11.5px; color:var(--muted); }
  /* 原生键：弱化，但仍走同一套连线体系 */
  .card.off { background:#fbfcfd; }
  .card.off .v { color:#9aa1a9; font-weight:600; }
  .card.off .k { color:#8b949e; }

  .foot { margin-top:14px; color:var(--faint); font-size:12.5px; }

  /* ---- 联系我 ---- */
  .contact { max-width:760px; margin:0 auto; }
  .contact .intro { margin-bottom:22px; text-align:center; }
  .contact-list { display:grid; gap:12px; }
  .contact-item {
    display:flex; align-items:center; gap:16px; background:#fff;
    border:1px solid var(--line); border-radius:13px; padding:16px 20px;
    box-shadow:0 1px 3px rgba(27,31,36,.04);
  }
  .ci-icon {
    width:38px; height:38px; border-radius:10px; flex:none;
    display:flex; align-items:center; justify-content:center;
    background:#eef3ff; color:#3b6fd4;
  }
  .ci-icon svg { width:21px; height:21px; }
  .ci-body { min-width:0; }
  .ci-label { font-size:12px; color:var(--faint); }
  .ci-value { font-size:15px; font-weight:600; color:var(--ink);
              user-select:all; word-break:break-all; }
  .ci-copy {
    margin-left:auto; flex:none; border:1px solid var(--line); background:#fff;
    color:var(--muted); border-radius:8px; padding:5px 12px; font:inherit;
    font-size:12px; cursor:pointer; transition:.15s;
  }
  .ci-copy:hover { border-color:#3b6fd4; color:#3b6fd4; }
  .ci-copy.ok { border-color:#24A15A; color:#24A15A; }
  .ci-link {
    margin-left:auto; flex:none; border:1px solid var(--line); background:#fff;
    color:var(--muted); border-radius:8px; padding:5px 12px; font:inherit;
    font-size:12px; text-decoration:none; transition:.15s;
  }
  .ci-link:hover { border-color:#3b6fd4; color:#3b6fd4; }
  .ci-value a { color:inherit; text-decoration:none; border-bottom:1px dashed #b6bdc6; }
  .ci-value a:hover { color:#3b6fd4; border-bottom-color:#3b6fd4; }

  .reward { text-align:center;
    margin-top:26px; background:#fff; border:1px solid var(--line);
    border-radius:16px; padding:26px 28px;
    box-shadow:0 1px 3px rgba(27,31,36,.04);
  }
  .reward h3 { margin:0 0 6px; font-size:17px; }
  .reward > p { margin:0; color:var(--muted); font-size:13.5px; }
  .reward-main { display:flex; align-items:flex-start; justify-content:center;
                 gap:22px; margin-top:20px; flex-wrap:wrap; }
  .reward-whale {
    width:180px; flex:none; border-radius:12px;
    display:flex; align-items:center; justify-content:center; overflow:hidden;
  }
  .reward-whale img { max-width:100%; max-height:100%; object-fit:contain; }
  .qr-item { flex:none; text-align:center; cursor:zoom-in; }
  .qr-item img {
    width:168px; height:168px; object-fit:cover; border-radius:12px; display:block;
    border:1px solid var(--line); box-shadow:0 1px 4px rgba(27,31,36,.08);
    transition:transform .15s,box-shadow .15s;
  }
  .qr-item:hover img { transform:translateY(-2px); box-shadow:0 5px 16px rgba(27,31,36,.14); }
  .qr-item span { display:block; margin-top:7px; font-size:12px; color:var(--muted); }
  .reward-note { font-size:12.5px; color:var(--faint); line-height:1.7; }

  /* 点击放大 */
  .lightbox {
    position:fixed; inset:0; z-index:99; background:rgba(20,24,30,.78);
    display:none; align-items:center; justify-content:center; padding:24px;
    cursor:zoom-out;
  }
  .lightbox.on { display:flex; }
  .lightbox img { max-width:100%; max-height:100%; object-fit:contain;
                  border-radius:12px; box-shadow:0 12px 40px rgba(0,0,0,.4); }

  /* ---- 版本日志 ---- */
  .changelog { max-width:760px; margin:0 auto; }
  .changelog > .lead { text-align:center; margin-bottom:22px; }
  .ver-block {
    background:#fff; border:1px solid var(--line); border-radius:13px;
    padding:16px 20px; margin-bottom:14px;
    box-shadow:0 1px 3px rgba(27,31,36,.04);
  }
  .ver-head { display:flex; align-items:center; gap:12px; margin-bottom:8px; }
  .ver-tag { font-size:14px; font-weight:650; color:#1f6feb;
             background:#eef4ff; border:1px solid #d6e4ff;
             border-radius:6px; padding:2px 10px; }
  .ver-date { font-size:12px; color:var(--faint); }
  .ver-block ul { margin:0; padding-left:20px; color:#38414a; }
  .ver-block li { margin:4px 0; font-size:13px; }

  @media (max-width:900px){
    .stage { grid-template-columns:minmax(0,1fr) auto minmax(0,1fr); gap:14px; }
    .remote-frame { height:600px; }
    .card { padding:7px 9px; }
    .card .r1 { display:block; }
    .card .v { display:block; }
  }
</style>
</head>
<body>
<header class="topbar"><div class="topbar-inner">
  <h1>G10S 遥控器按键说明 <span class="ver">{{VERSION}}</span></h1>
  <p class="sub">同一批按键在两种模式下做不同的事 · 把鼠标移到卡片上可高亮对应按键</p>
  <nav>
    <button class="on" data-t="p1">模式切换</button>
    <button data-t="p2">对话模式</button>
    <button data-t="p3">观看模式</button>
    <button data-t="p4">联系我</button>
    <button data-t="p5">版本日志</button>
  </nav>
</div></header>

<main>
  <section id="p1" class="on">
    <p class="lead">遥控器有<b>两种模式</b>，同一批按键在不同模式下做不同的事。看托盘图标就知道当前在哪种模式。</p>
    <div class="modes">
      <div class="mode">
        <img src="{{ICON_DIALOG}}" alt="对话模式图标">
        <div><h3>对话模式</h3><p>用来跟 AI 对话、发消息</p></div>
      </div>
      <div class="mode">
        <img src="{{ICON_WATCH}}" alt="观看模式图标">
        <div><h3>观看模式</h3><p>用来浏览网页、看视频</p></div>
      </div>
      <div class="mode soon">
        <div class="soon-ico">✦</div>
        <h3>更多模式实现中……</h3>
        <p>敬请期待</p>
      </div>
    </div>
    <div class="switch-how">
      <h3>怎么切换</h3>
      <ol>
        <li>按遥控器上那颗<b>静音键</b>（底部右侧，喇叭带斜杠的图标）</li>
        <li>或者<b>左键单击</b>任务栏右下角的小图标</li>
        <li>也可以右键图标 → 模式 → 选择</li>
      </ol>
      <div class="hint">切换模式不会改变音量。静音键的静音动作会被程序立刻撤销，但你可能会看到音量浮窗闪一下 —— 这是遥控器硬件层面的行为，软件无法完全避免。</div>
    </div>
    <div class="info-card">
      <h3>关于语音输入</h3>
      <p>语音输入依赖<b>微信输入法</b>（或任意带语音输入功能的输入法）实现，本程序只负责把麦克风键映射成<b>按住说话</b>的快捷键。没装这类输入法时，麦克风键不会有语音输入效果。</p>
    </div>
  </section>

  <section id="p2">
    <p class="lead">主要面向 <b>WorkBuddy</b> 的使用场景：说话提问、发送、打断、在多个对话之间切换。</p>
    <div class="stage" data-mode="dialog">
      <div class="side left">{{CARDS_DIALOG_LEFT}}</div>
      <div class="remote-frame">
        <img src="{{REMOTE}}" alt="遥控器">
        {{ANCHORS}}
      </div>
      <div class="side right">{{CARDS_DIALOG_RIGHT}}</div>
      <svg class="wires" aria-hidden="true"></svg>
    </div>
    <p class="foot">方向键只在编辑区生效；想看长回答请切到观看模式用滚轮滚动。</p>
  </section>

  <section id="p3">
    <p class="lead">主要面向 <b>Chrome 内核浏览器</b>（Chrome、Edge、Brave 等）：浏览网页、切换标签页、看视频。</p>
    <div class="stage" data-mode="watch">
      <div class="side left">{{CARDS_WATCH_LEFT}}</div>
      <div class="remote-frame">
        <img src="{{REMOTE}}" alt="遥控器">
        {{ANCHORS}}
      </div>
      <div class="side right">{{CARDS_WATCH_RIGHT}}</div>
      <svg class="wires" aria-hidden="true"></svg>
    </div>
    <p class="foot">观看模式只接管上/下（滚轮）、灯泡、PgUp/PgDn、返回键和静音键，其余全部交还系统。</p>
  </section>

  <section id="p4">
    <div class="contact">
      <p class="lead intro">遇到问题、有建议，或者单纯想说句话，都欢迎联系我。</p>
      <div class="contact-list">
        <div class="contact-item">
          <span class="ci-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></svg></span>
          <div class="ci-body"><div class="ci-label">邮箱</div><div class="ci-value">2449663340@qq.com</div></div>
          <button class="ci-copy" data-copy="2449663340@qq.com">复制</button>
        </div>
        <div class="contact-item">
          <span class="ci-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9 9 0 0 1-4-.9L3 20l1-4.5A8.4 8.4 0 0 1 3 11.5 8.5 8.5 0 0 1 12 3a8.5 8.5 0 0 1 9 8.5z"/></svg></span>
          <div class="ci-body"><div class="ci-label">微信</div><div class="ci-value">ray2449663340</div></div>
          <button class="ci-copy" data-copy="ray2449663340">复制</button>
        </div>
        <div class="contact-item">
          <span class="ci-icon"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.58 2 12.25c0 4.53 2.87 8.37 6.84 9.73.5.1.68-.22.68-.49 0-.24-.01-1.05-.01-1.9-2.78.62-3.37-1.37-3.37-1.37-.45-1.18-1.11-1.49-1.11-1.49-.91-.64.07-.63.07-.63 1 .07 1.53 1.06 1.53 1.06.89 1.56 2.34 1.11 2.91.85.09-.66.35-1.11.63-1.37-2.22-.26-4.56-1.14-4.56-5.06 0-1.12.39-2.03 1.03-2.75-.1-.26-.45-1.3.1-2.71 0 0 .84-.28 2.75 1.05A9.4 9.4 0 0 1 12 6.99c.85 0 1.71.12 2.51.34 1.91-1.33 2.75-1.05 2.75-1.05.55 1.41.2 2.45.1 2.71.64.72 1.03 1.63 1.03 2.75 0 3.93-2.34 4.8-4.57 5.05.36.32.68.94.68 1.9 0 1.37-.01 2.47-.01 2.81 0 .27.18.59.69.49A10.26 10.26 0 0 0 22 12.25C22 6.58 17.52 2 12 2z"/></svg></span>
          <div class="ci-body"><div class="ci-label">GitHub（开源地址）</div><div class="ci-value"><a href="https://github.com/rfHu/g10s-remote" target="_blank" rel="noopener">github.com/rfHu/g10s-remote</a></div></div>
          <a class="ci-link" href="https://github.com/rfHu/g10s-remote" target="_blank" rel="noopener">访问 ↗</a>
        </div>
      </div>

      <div class="reward">
        <h3>制作不易，欢迎打赏一碗大白饭</h3>
        <p>如果这支遥控器帮到了你，一点心意就是继续维护的动力。</p>
        <div class="reward-main">
          <div class="reward-whale"><img src="{{WHALE}}" alt="鲸鱼娘"></div>
          <div class="qr-item"><img class="zoomable" src="{{QR_WECHAT}}" alt="微信收款码"><span>微信</span></div>
          <div class="qr-item"><img class="zoomable" src="{{QR_ALIPAY}}" alt="支付宝收款码"><span>支付宝</span></div>
          <div class="reward-note">扫码即可打赏<br>金额随意，心意领了</div>
        </div>
      </div>
    </div>
  </section>

  <section id="p5">
    <div class="changelog">
      <p class="lead">按版本倒序记录每次更新，完整历史见仓库的 CHANGELOG.md。</p>
      {{CHANGELOG}}
    </div>
  </section>
</main>

<div class="lightbox" id="lightbox"><img id="lightboxImg" alt="放大"></div>

<script>
(function () {
  var f = function (n) { return Math.round(n * 10) / 10; };

  // 卡片按锚点 y 定位；若相邻两张挨得太近（或超出画布），沿纵向推开 / 回收。
  function layout(side) {
    var cards = side.querySelectorAll('.card');
    var prevBottom = -Infinity, gap = 9;
    for (var i = 0; i < cards.length; i++) {
      var c = cards[i];
      var y = parseFloat(c.dataset.pos) / 100 * side.clientHeight;
      var h = c.offsetHeight;
      if (y - h / 2 < prevBottom + gap) y = prevBottom + gap + h / 2;
      c.style.top = y + 'px';
      prevBottom = y + h / 2;
    }
    // 若最后一张溢出底部，整体上移
    var overflow = prevBottom - side.clientHeight;
    if (overflow > 0) {
      for (var j = 0; j < cards.length; j++) {
        var cy = parseFloat(cards[j].style.top) - overflow;
        cards[j].style.top = Math.max(cards[j].offsetHeight / 2, cy) + 'px';
      }
    }
  }

  // 按 DOM 实际像素坐标画连线：viewBox = 真实尺寸，线宽不再被拉伸变形。
  function draw(stage) {
    var sr = stage.getBoundingClientRect();
    if (!sr.width || !sr.height) return;
    var svg = stage.querySelector('.wires');
    svg.setAttribute('viewBox', '0 0 ' + f(sr.width) + ' ' + f(sr.height));
    var out = '';
    stage.querySelectorAll('.card').forEach(function (card) {
      var key = card.dataset.key;
      var anchor = stage.querySelector('.anchor[data-key="' + key + '"]');
      if (!anchor) return;
      var cr = card.getBoundingClientRect(), ar = anchor.getBoundingClientRect();
      var isLeft = card.parentNode.classList.contains('left');
      var sx = (isLeft ? cr.right : cr.left) - sr.left;
      var sy = cr.top + cr.height / 2 - sr.top;
      var ex = ar.left + ar.width / 2 - sr.left;
      var ey = ar.top + ar.height / 2 - sr.top;
      var dx = Math.max(18, Math.min(70, Math.abs(ex - sx) * .5));
      var c1x = isLeft ? sx + dx : sx - dx;
      var c2x = isLeft ? ex - dx : ex + dx;
      var kind = card.dataset.kind === 'off' ? ' off' : '';
      out += '<path class="wire' + kind + '" data-key="' + key + '" d="M' + f(sx) + ',' + f(sy) +
             ' C' + f(c1x) + ',' + f(sy) + ' ' + f(c2x) + ',' + f(ey) +
             ' ' + f(ex) + ',' + f(ey) + '"/>';
      out += '<circle class="dot' + kind + '" data-key="' + key + '" cx="' + f(sx) +
             '" cy="' + f(sy) + '" r="2.7"/>';
    });
    svg.innerHTML = out;

    // hover 联动：卡片 ↔ 连线 ↔ 遥控器上的按键光圈
    stage.querySelectorAll('.card').forEach(function (card) {
      var key = card.dataset.key;
      function set(on) {
        stage.querySelectorAll('[data-key="' + key + '"]').forEach(function (el) {
          el.classList.toggle('hot', on);
          el.classList.toggle('show', on);
        });
      }
      card.addEventListener('mouseenter', function () { set(true); });
      card.addEventListener('mouseleave', function () { set(false); });
    });
  }

  function refresh() {
    document.querySelectorAll('section.on .stage').forEach(function (stage) {
      stage.querySelectorAll('.side').forEach(layout);
      draw(stage);
    });
  }

  function activate(id) {
    document.querySelectorAll('nav button').forEach(function (x) {
      x.classList.toggle('on', x.dataset.t === id);
    });
    document.querySelectorAll('section').forEach(function (x) {
      x.classList.toggle('on', x.id === id);
    });
    requestAnimationFrame(refresh);
  }
  document.querySelectorAll('nav button').forEach(function (b) {
    b.addEventListener('click', function () { activate(b.dataset.t); });
  });

  // 联系页：点「复制」写入剪贴板（兼容非安全上下文的 file:// 打开方式）
  document.querySelectorAll('.ci-copy').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var text = btn.dataset.copy;
      function done() {
        btn.textContent = '已复制'; btn.classList.add('ok');
        setTimeout(function () { btn.textContent = '复制'; btn.classList.remove('ok'); }, 1600);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () { fallback() });
      } else { fallback(); }
      function fallback() {
        var ta = document.createElement('textarea');
        ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select();
        try { document.execCommand('copy'); done(); } catch (e) {}
        document.body.removeChild(ta);
      }
    });
  });

  // 收款码点击放大
  var lb = document.getElementById('lightbox');
  var lbImg = document.getElementById('lightboxImg');
  document.querySelectorAll('.zoomable').forEach(function (img) {
    img.addEventListener('click', function () {
      lbImg.src = img.src; lb.classList.add('on');
    });
  });
  lb.addEventListener('click', function () { lb.classList.remove('on'); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') lb.classList.remove('on');
  });

  var m = location.hash.match(/^#(p[1-5])$/);
  if (m) activate(m[1]);

  window.addEventListener('load', refresh);
  var timer;
  window.addEventListener('resize', function () {
    clearTimeout(timer);
    timer = setTimeout(refresh, 80);
  });
})();
</script>
</body>
</html>
"""


def main():
    html = (TPL
            .replace("{{VERSION}}", "v" + read_version())
            .replace("{{CHANGELOG}}", build_changelog())
            .replace("{{ICON_DIALOG}}", data_uri(ICON_DIALOG, "image/png"))
            .replace("{{ICON_WATCH}}", data_uri(ICON_WATCH, "image/png"))
            .replace("{{WHALE}}", data_uri(WHALE, "image/png"))
            .replace("{{QR_WECHAT}}", data_uri(QR_WECHAT, "image/jpeg"))
            .replace("{{QR_ALIPAY}}", data_uri(QR_ALIPAY, "image/jpeg"))
            .replace("{{REMOTE}}", data_uri(REMOTE, "image/jpeg"))
            .replace("{{CARDS_DIALOG_LEFT}}", build_cards(DIALOG_LEFT))
            .replace("{{CARDS_DIALOG_RIGHT}}", build_cards(DIALOG_RIGHT))
            .replace("{{CARDS_WATCH_LEFT}}", build_cards(WATCH_LEFT))
            .replace("{{CARDS_WATCH_RIGHT}}", build_cards(WATCH_RIGHT))
            .replace("{{ANCHORS}}", build_anchors()))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print("已生成 %s  (%.0f KB)" % (OUT, os.path.getsize(OUT) / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
