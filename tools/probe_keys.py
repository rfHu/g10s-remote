"""生成 probe.html 并用 Edge 无头模式跑一遍，精确提取遥控器上蓝色按键的坐标。

为什么要这么绕：项目是零依赖（没有 PIL），PowerShell 的 Add-Type 又被安全策略拦了，
所以借浏览器的 Canvas 读像素，把结果写进 DOM 再用 --dump-dom 抓回来。

用法：python tools/probe_keys.py
"""
import base64
import json
import os
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REMOTE = os.path.join(ROOT, "assets", "remote.jpg")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

PROBE = """<!DOCTYPE html><meta charset="utf-8">
<canvas id="c"></canvas><pre id="out">PENDING</pre>
<script>
const img = new Image();
img.onload = function () {
  const c = document.getElementById('c');
  c.width = img.width; c.height = img.height;
  const ctx = c.getContext('2d');
  ctx.drawImage(img, 0, 0);
  const W = c.width, H = c.height;
  const d = ctx.getImageData(0, 0, W, H).data;
  const pts = [];
  for (let y = 0; y < H; y += 2) {
    for (let x = 0; x < W; x += 2) {
      const i = (y * W + x) * 4;
      const r = d[i], g = d[i + 1], b = d[i + 2];
      // 遥控器上的按键图标是蓝青色：蓝通道高、红通道低
      if (b > 130 && b - r > 45 && g > 70) pts.push([x, y]);
    }
  }
  // 网格分桶聚类：同一按键的像素会落进相邻的桶
  const CELL = 70;
  const buckets = new Map();
  for (const [x, y] of pts) {
    const k = Math.floor(x / CELL) + ',' + Math.floor(y / CELL);
    if (!buckets.has(k)) buckets.set(k, []);
    buckets.get(k).push([x, y]);
  }
  const keep = [];
  for (const arr of buckets.values()) {
    if (arr.length >= 12) {
      let sx = 0, sy = 0;
      for (const p of arr) { sx += p[0]; sy += p[1]; }
      keep.push([Math.round(sx / arr.length), Math.round(sy / arr.length), arr.length]);
    }
  }
  document.getElementById('out').textContent =
    JSON.stringify({ w: W, h: H, n: pts.length, blobs: keep });
};
img.src = "{{IMG}}";
</script>"""


def main():
    with open(REMOTE, "rb") as f:
        uri = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()
    html = PROBE.replace("{{IMG}}", uri)

    tmp = os.path.join(tempfile.gettempdir(), "g10s-probe.html")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html)

    out = subprocess.run(
        [EDGE, "--headless=new", "--disable-gpu", "--virtual-time-budget=4000",
         "--dump-dom", "file:///" + tmp.replace("\\", "/")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    dom = out.stdout or ""
    start = dom.find('{"w"')
    if start < 0:
        print("没抓到结果，DOM 片段：", dom[:500])
        return 1
    end = dom.find("</pre>", start)
    data = json.loads(dom[start:end if end > 0 else None].strip())

    blobs = sorted(data["blobs"], key=lambda b: (b[1], b[0]))
    print("图片 %dx%d，蓝色像素 %d 个，聚合出 %d 个候选点："
          % (data["w"], data["h"], data["n"], len(blobs)))
    for x, y, n in blobs:
        print("  (%4d, %4d)  占比 (%.1f%%, %.1f%%)  像素 %d"
              % (x, y, x * 100.0 / data["w"], y * 100.0 / data["h"], n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
