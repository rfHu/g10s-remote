"""生成按键说明页 help.html —— 单文件，所有图片以 base64 内嵌。

产物会被 build.bat 打进 exe，运行时由 bridge.py 的 open_help() 打开，
不产生任何外部文件。

用法：python tools/make_help.py
"""
import base64
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REMOTE = os.path.join(ROOT, "assets", "remote.jpg")
ICON_DIALOG = os.path.join(ROOT, "app-preview-dialog.png")
ICON_WATCH = os.path.join(ROOT, "app-preview-watch.png")
OUT = os.path.join(ROOT, "help.html")


def data_uri(path, mime):
    with open(path, "rb") as f:
        return "data:%s;base64,%s" % (mime, base64.b64encode(f.read()).decode())


# 各键的坐标，单位是「裁剪后容器」的百分比 —— 用带 10% 网格的标定图
# 逐个读出来的，不再靠目测原图换算（换算基准一错，圆圈就整体偏移）。
KEY = {
    "power":  (42.0, 11.0),
    "play":   (40.0, 17.0),
    "back":   (62.0, 17.0),
    "ring":   (50.0, 28.0),   # 方向环中心，也是 OK 键
    "ret":    (36.0, 41.0),   # 房子 + 弯箭头
    "lamp":   (63.0, 41.0),   # ≡ + 灯泡
    "pgup":   (37.0, 48.0),
    "pgdn":   (37.0, 55.0),
    "mic":    (50.0, 51.0),
    "volup":  (65.0, 48.0),
    "voldn":  (65.0, 55.0),
    "del":    (42.0, 62.0),
    "mute":   (60.0, 62.0),
}


DIALOG_LEFT = [
    ("play", 17.0, "播放", "Ctrl+N", "新建对话"),
    ("ret", 41.0, "返回", "Esc", "打断 AI 输出"),
    ("pgup", 51.5, "PgUp / PgDn", "Ctrl+[ / Ctrl+]", "上一个 / 下一个对话"),
]

DIALOG_RIGHT = [
    ("ring", 28.0, "方向环 / OK", "方向键 / Alt+Tab", "走光标；中间 OK 切窗口"),
    ("lamp", 41.0, "灯泡", "回车", "发送消息（最好按的键给最高频）"),
    ("mic", 51.0, "麦克风", "Ctrl+Win", "语音输入，按一下开始、再按一下结束"),
    ("mute", 62.0, "静音", "—", "切换模式，不动音量"),
]

WATCH_LEFT = [
    ("play", 17.0, "播放", "播放 / 暂停", "原生媒体控制"),
    ("ret", 41.0, "返回", "浏览器后退", "回到上一页"),
    ("pgup", 51.5, "PgUp / PgDn", "Ctrl+Tab / Ctrl+Shift+Tab", "下一个 / 上一个标签页"),
]

WATCH_RIGHT = [
    ("ring", 28.0, "上 / 下", "滚轮", "滚动页面，比翻页更顺"),
    ("lamp", 41.0, "灯泡", "回车", "确认输入框里的内容"),
    ("mic", 51.0, "麦克风", "Ctrl+Win", "语音输入"),
    ("mute", 62.0, "静音", "—", "切换模式，不动音量"),
]


def build_side(rows, side, accent):
    """生成一侧的卡片与连线。

    side="left" 表示这些键位于遥控器左半边：线从卡片右沿连到按键；
    side="right" 相反。中间列（遥控器图）占 30%~70%。
    """
    cards, lines = [], []
    for key, cy, name, action, desc in rows:
        bx, by = KEY[key]
        px = 37.0 + bx * 0.26           # 遥控器列占整宽的 37%~63%
        edge = 37.0 if side == "left" else 63.0
        cards.append(
            '<div class="card" style="top:%.1f%%">'
            '<div class="k">%s</div><div class="v" style="color:%s">%s</div>'
            '<div class="d">%s</div></div>'
            % (cy, name, accent, action, desc))
        lines.append(
            '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
            'stroke-width=".9" opacity=".45"/>'
            '<circle cx="%.1f" cy="%.1f" r="2.4" fill="none" stroke="%s" '
            'stroke-width="1" opacity=".75"/>'
            % (edge, cy, px, by, accent, px, by, accent))
    return "\n".join(cards), "\n".join(lines)


TPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>G10S 遥控器按键说明</title>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 0 0 60px;
    font: 14px/1.6 "Segoe UI", "Microsoft YaHei", system-ui, sans-serif;
    background: #f4f5f7; color: #1f2328;
  }
  header { background: #fff; border-bottom: 1px solid #e3e5e8; padding: 18px 28px 0; }
  h1 { margin: 0 0 14px; font-size: 19px; font-weight: 600; letter-spacing: .3px; }
  nav { display: flex; gap: 4px; }
  nav button {
    border: 0; background: none; padding: 9px 16px; font: inherit; font-weight: 500;
    color: #57606a; cursor: pointer; border-bottom: 2px solid transparent;
  }
  nav button:hover { color: #1f2328; }
  nav button.on { color: #1f2328; border-bottom-color: #1f2328; }
  main { max-width: 1080px; margin: 0 auto; padding: 26px 20px; }
  section { display: none; }
  section.on { display: block; }

  /* ---- 模式切换页 ---- */
  .modes { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin: 22px 0 8px; }
  .mode {
    background: #fff; border: 1px solid #e3e5e8; border-radius: 12px;
    padding: 22px; text-align: center;
  }
  .mode img { width: 84px; height: 84px; border-radius: 18px; }
  .mode h3 { margin: 14px 0 4px; font-size: 16px; }
  .mode p { margin: 0; color: #57606a; font-size: 13px; }
  .switch-how {
    background: #fff; border: 1px solid #e3e5e8; border-radius: 12px;
    padding: 20px 24px; margin-top: 18px;
  }
  .switch-how h3 { margin: 0 0 10px; font-size: 15px; }
  .switch-how ol { margin: 0; padding-left: 20px; color: #38414a; }
  .switch-how li { margin: 6px 0; }
  kbd {
    display: inline-block; padding: 1px 7px; margin: 0 2px; border-radius: 5px;
    background: #eef0f3; border: 1px solid #dcdfe4; font: 12px/1.7 ui-monospace, Consolas, monospace;
  }
  .hint { margin-top: 14px; padding: 12px 14px; border-radius: 9px;
          background: #fff8e6; border: 1px solid #f0dfae; color: #6b5417; font-size: 13px; }

  /* ---- 连线图：左说明 · 遥控器 · 右说明 ---- */
  /* 图片四周有大片白边，遥控器只占中间一块，所以容器按那个比例定高、
     object-fit: cover 裁掉白边，遥控器才能撑满这一列。
     .col 里全是绝对定位元素，高度靠 grid 的 stretch 撑满行高（行高由图片列决定）。 */
  .stage { position: relative; display: grid; grid-template-columns: 37% 26% 37%; }
  .col { position: relative; }
  .remote { position: relative; aspect-ratio: 42 / 96; }
  .remote img { display: block; width: 100%; height: 100%; object-fit: cover;
                object-position: center; border-radius: 10px; }
  .lines { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; pointer-events: none; }
  .card {
    position: absolute; left: 0; right: 0; transform: translateY(-50%);
    background: #fff; border: 1px solid #e3e5e8; border-radius: 10px;
    padding: 8px 12px; box-shadow: 0 1px 2px rgba(27,31,36,.05);
  }
  .col.left .card { margin-right: 12px; }
  .col.right .card { margin-left: 12px; }
  .card .k { font-size: 11px; color: #6e7781; letter-spacing: .3px; line-height: 1.5; }
  .card .v { font-size: 14px; font-weight: 600; margin-top: 0; line-height: 1.4; }
  .card .d { font-size: 11.5px; color: #57606a; margin-top: 1px; line-height: 1.45; }
  .lead { margin: 0 0 18px; color: #57606a; font-size: 13.5px; }
  .lead b { color: #1f2328; }
  .native {
    margin-top: 20px; padding: 14px 16px; background: #fff;
    border: 1px solid #e3e5e8; border-radius: 10px;
  }
  .native h4 { margin: 0 0 10px; font-size: 13px; color: #57606a; font-weight: 600; }
  .native ul { display: flex; flex-wrap: wrap; gap: 8px 26px; margin: 0; padding: 0; list-style: none; }
  .native li { font-size: 12.5px; color: #6e7781; }
  .native b { color: #38414a; font-weight: 600; }
  .foot { margin-top: 14px; color: #6e7781; font-size: 12.5px; }
</style>
</head>
<body>
<header>
  <h1>G10S 遥控器按键说明</h1>
  <nav>
    <button class="on" data-t="p1">模式切换</button>
    <button data-t="p2">对话模式</button>
    <button data-t="p3">观看模式</button>
  </nav>
</header>

<main>
  <section id="p1" class="on">
    <p class="lead">遥控器有<b>两种模式</b>，同一批按键在不同模式下做不同的事。看托盘图标就知道当前在哪种模式。</p>
    <div class="modes">
      <div class="mode">
        <img src="{{ICON_DIALOG}}" alt="对话模式图标">
        <h3>对话模式</h3>
        <p>绿底 · 两个气泡<br>用来跟 AI 对话、发消息</p>
      </div>
      <div class="mode">
        <img src="{{ICON_WATCH}}" alt="观看模式图标">
        <h3>观看模式</h3>
        <p>蓝底 · 眼睛<br>用来浏览网页、看视频</p>
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
  </section>

  <section id="p2">
    <p class="lead">主要面向 <b>WorkBuddy</b> 的使用场景：说话提问、发送、打断、在多个对话之间切换。</p>
    <div class="stage">
      <div class="col left">{{CARDS_DIALOG_LEFT}}</div>
      <div class="remote"><img src="{{REMOTE}}" alt="遥控器"></div>
      <div class="col right">{{CARDS_DIALOG_RIGHT}}</div>
      <svg class="lines" viewBox="0 0 100 100" preserveAspectRatio="none">{{LINES_DIALOG}}</svg>
    </div>
    <div class="native">
      <h4>这些键保持系统原生行为，本程序没有改动</h4>
      <ul>
        <li><b>DEL</b> 退格，删掉刚打错的字</li>
        <li><b>VOL + / −</b> 音量加减</li>
        <li><b>左 / 右方向</b> 移动光标</li>
        <li><b>↻ 圆圈箭头</b> 未接管，保持原样</li>
        <li><b>电源</b> 红外信号，蓝牙连接下无效</li>
      </ul>
    </div>
    <p class="foot">方向键只在编辑区生效；想看长回答请切到观看模式用滚轮滚动。</p>
  </section>

  <section id="p3">
    <p class="lead">主要面向 <b>Chrome 内核浏览器</b>（Chrome、Edge、Brave 等）：浏览网页、切换标签页、看视频。</p>
    <div class="stage">
      <div class="col left">{{CARDS_WATCH_LEFT}}</div>
      <div class="remote"><img src="{{REMOTE}}" alt="遥控器"></div>
      <div class="col right">{{CARDS_WATCH_RIGHT}}</div>
      <svg class="lines" viewBox="0 0 100 100" preserveAspectRatio="none">{{LINES_WATCH}}</svg>
    </div>
    <div class="native">
      <h4>这些键保持系统原生行为，本程序没有改动</h4>
      <ul>
        <li><b>DEL</b> 退格</li>
        <li><b>VOL + / −</b> 音量加减</li>
        <li><b>左 / 右方向</b> 光标移动、视频快进</li>
        <li><b>↻ 圆圈箭头</b> 未接管，保持原样</li>
        <li><b>电源</b> 红外信号，蓝牙连接下无效</li>
      </ul>
    </div>
    <p class="foot">观看模式只接管上/下（滚轮）、灯泡、PgUp/PgDn 和静音键，其余全部交还系统。</p>
  </section>
</main>

<script>
  function activate(id) {
    document.querySelectorAll('nav button').forEach(function (x) {
      x.classList.toggle('on', x.dataset.t === id);
    });
    document.querySelectorAll('section').forEach(function (x) {
      x.classList.toggle('on', x.id === id);
    });
  }
  document.querySelectorAll('nav button').forEach(function (b) {
    b.addEventListener('click', function () { activate(b.dataset.t); });
  });
  var m = location.hash.match(/^#(p[123])$/);
  if (m) activate(m[1]);
</script>
</body>
</html>
"""


def main():
    cards_dl, lines_dl = build_side(DIALOG_LEFT, "left", "#24A15A")
    cards_dr, lines_dr = build_side(DIALOG_RIGHT, "right", "#24A15A")
    cards_wl, lines_wl = build_side(WATCH_LEFT, "left", "#2563C7")
    cards_wr, lines_wr = build_side(WATCH_RIGHT, "right", "#2563C7")
    html = (TPL
            .replace("{{ICON_DIALOG}}", data_uri(ICON_DIALOG, "image/png"))
            .replace("{{ICON_WATCH}}", data_uri(ICON_WATCH, "image/png"))
            .replace("{{REMOTE}}", data_uri(REMOTE, "image/jpeg"))
            .replace("{{CARDS_DIALOG_LEFT}}", cards_dl)
            .replace("{{CARDS_DIALOG_RIGHT}}", cards_dr)
            .replace("{{LINES_DIALOG}}", lines_dl + "\n" + lines_dr)
            .replace("{{CARDS_WATCH_LEFT}}", cards_wl)
            .replace("{{CARDS_WATCH_RIGHT}}", cards_wr)
            .replace("{{LINES_WATCH}}", lines_wl + "\n" + lines_wr))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print("已生成 %s  (%.0f KB)" % (OUT, os.path.getsize(OUT) / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
