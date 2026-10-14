#!/usr/bin/env python3
"""
split_webfont.py — 把体积巨大的中文（可变）字体切成按 unicode-range 按需加载的 WOFF2 分片，
并生成对应的 @font-face CSS。原理与 Google Fonts 提供中文字体的方式相同：浏览器只会下载
当前页面实际用到的字符所在的那几个分片，而不是整个 15 MB 的字体。

依赖：  pip install fonttools brotli

本站使用的命令（在仓库根目录执行，输入建议用 TTF；WOFF2 也行，但解压要多花几分钟）：

  python3 _font-tools/split_webfont.py \\
      --family "Sarasa Ui PD SC Web" --name sarasa-ui-pd-sc \\
      --font normal=SarasaUiVFPropDigitsSC.ttf \\
      --font italic=SarasaUiVFPropDigitsSC-Italic.ttf \\
      --wght 400:900 \\
      --out media/files/fonts/sarasa-ui-pd-sc

  python3 _font-tools/split_webfont.py \\
      --family "Noto Sans Mono Web" --name noto-sans-mono \\
      --font normal=NotoSansMono.ttf --wght 400:900 --pin wdth=100 \\
      --out media/files/fonts/noto-sans-mono
"""
import argparse
import concurrent.futures as cf
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

HERE = Path(__file__).resolve().parent

# 浏览器默认启用、或与中文排版（标点挤压、竖排、谚文组字）相关的 OpenType 特性。
# 其余特性（cvXX / ssXX / aalt / frac / sups …）只能通过 font-feature-settings 手动开启，
# 本站没有用到，保留它们会让拉丁分片因替换闭包多出数百个字形。需要时加 --all-features。
DEFAULT_FEATURES = [
    "ccmp", "locl", "calt", "rlig", "liga", "clig", "kern", "mark", "mkmk", "curs",
    "chws", "vchw", "halt", "vhal", "palt", "vpal", "vkrn", "vert", "vrt2",
    "ljmo", "vjmo", "tjmo",
]

# 全部 CJK 标点和基本拉丁字符（Google 的 latin 子集，含 —“”…）放进同一个 base 分片。
# chws 只能作用于同一个字体文件里的相邻字形；而且实测 Chrome 的 text-spacing-trim 在
# 「—」「A」这类来自另一个分片的字符后面会把标点挤压错（重叠或不挤），合并后排版与原字体完全一致。
PUNCT = "3000-303f,30fb,fe10-fe1f,fe30-fe6f,ff01-ff0f,ff1a-ff20,ff3b-ff40,ff5b-ff65,ffe0-ffee"

# 按频率切片之后剩下的字符：(分组名, 范围, 每片最多字符数)，按顺序匹配
REST_GROUPS = [
    ("kana", "3040-30ff,31f0-31ff", 400),
    ("hangul", "1100-11ff,3130-318f,a960-a97f,ac00-d7ff", 1000),
    ("han", "2e80-2fff,3100-312f,3190-31ef,3200-33ff,3400-4dbf,4e00-9fff,f900-faff,20000-3ffff", 400),
    ("misc", "0-10ffff", 400),
]

NAMED_ORDER = ["latin-ext", "vietnamese", "greek", "greek-ext", "cyrillic", "cyrillic-ext"]
MIN_FREQ_SLICE = 64


def parse_ranges(text):
    cps = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            cps.update(range(int(a, 16), int(b, 16) + 1))
        else:
            cps.add(int(part, 16))
    return cps


def css_unicode_range(cps):
    cps = sorted(cps)
    out, i = [], 0
    while i < len(cps):
        j = i
        while j + 1 < len(cps) and cps[j + 1] == cps[j] + 1:
            j += 1
        out.append(f"U+{cps[i]:X}" if i == j else f"U+{cps[i]:X}-{cps[j]:X}")
        i = j + 1
    return ",".join(out)


def plan_slices(cmap_cps, data):
    """返回 [(分片名, 码位集合)]，每个码位只属于一个分片。"""
    remaining = set(cmap_cps)
    slices = []

    def take(name, cps):
        got = remaining & cps
        if got:
            remaining.difference_update(got)
            slices.append((name, got))

    take("base", parse_ranges(PUNCT) | parse_ranges(data["named"]["latin"]))
    for name in NAMED_ORDER:
        if name in data["named"]:
            take(name, parse_ranges(data["named"][name]))
    # 频率切片与本字体取交集后可能只剩几个字（例如西文字体只命中零星符号），
    # 把相邻的小切片合并到至少 MIN_FREQ_SLICE 个字，避免一堆 1 KB 的小文件。
    pending, pending_name = set(), None
    for i, ranges in enumerate(data["sc_frequency"]):
        got = remaining & parse_ranges(ranges)
        if not got:
            continue
        pending_name = pending_name or f"f{i:03d}"
        pending |= got
        if len(pending) >= MIN_FREQ_SLICE:
            take(pending_name, pending)
            pending, pending_name = set(), None
    if pending:
        take(pending_name, pending)
    for group, ranges, size in REST_GROUPS:
        cps = sorted(remaining & parse_ranges(ranges))
        for n, k in enumerate(range(0, len(cps), size)):
            take(f"{group}-{n:02d}", set(cps[k:k + size]))
    assert not remaining
    return slices


def build_slice(task):
    src, cps, out_path, features, limits = task
    font = TTFont(src, lazy=True)
    opts = subset.Options()
    opts.layout_features = features
    opts.name_IDs = ["*"]
    opts.name_languages = ["*"]
    opts.notdef_outline = True
    opts.glyph_names = False
    sub = subset.Subsetter(opts)
    sub.populate(unicodes=cps)
    sub.subset(font)
    if limits and "fvar" in font:
        # optimize=False：不重做 IUP 优化，保留原字体的变化数据
        instancer.instantiateVariableFont(font, limits, inplace=True, optimize=False)
    font.flavor = "woff2"
    font.save(out_path)
    return out_path, font["maxp"].numGlyphs, sorted(font.getBestCmap())


def css_descriptors(font):
    """根据输出分片（已限制轴范围）算出 font-weight / font-stretch 描述符。"""
    axes = {a.axisTag: a for a in font["fvar"].axes} if "fvar" in font else {}
    desc = {}
    if "wght" in axes:
        desc["font-weight"] = f"{axes['wght'].minValue:g} {axes['wght'].maxValue:g}"
    else:
        desc["font-weight"] = str(font["OS/2"].usWeightClass)
    if "wdth" in axes:
        desc["font-stretch"] = f"{axes['wdth'].minValue:g}% {axes['wdth'].maxValue:g}%"
    return desc


def write_test_page(out_dir, family, css_name):
    html = TEST_PAGE.replace("{{FAMILY}}", family).replace("{{CSS}}", css_name)
    (out_dir / "test.html").write_text(html, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--family", required=True, help="CSS 里使用的 font-family 名称")
    ap.add_argument("--name", required=True, help="输出文件名前缀，例如 sarasa-ui-pd-sc")
    ap.add_argument("--font", action="append", required=True, metavar="STYLE=PATH",
                    help="normal=正体字体文件 / italic=斜体字体文件，可重复")
    ap.add_argument("--out", required=True, help="输出目录（会先清空其中的旧 .woff2）")
    ap.add_argument("--wght", help="限制字重轴范围，例如 400:900。下限取字体默认值（400）时其余变化数据无需重算，"
                         "排版与原字体一致；本站只用到 400/500/700")
    ap.add_argument("--pin", action="append", default=[], metavar="AXIS=VALUE",
                    help="把某个轴固定为单一值并删除，例如 wdth=100")
    ap.add_argument("--display", default="swap", help="font-display，默认 swap")
    ap.add_argument("--all-features", action="store_true", help="保留全部 OpenType 特性")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    args = ap.parse_args()

    data = json.loads((HERE / "unicode-slices.json").read_text(encoding="utf-8"))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.woff2"):
        old.unlink()

    limits = {}
    if args.wght:
        lo, hi = (float(x) for x in args.wght.split(":"))
        limits["wght"] = (lo, hi)
    for pin in args.pin:
        tag, value = pin.split("=")
        limits[tag] = float(value)
    features = ["*"] if args.all_features else DEFAULT_FEATURES

    tmp = tempfile.TemporaryDirectory()
    faces = []  # (css style, 源文件, 文件名前缀)
    for spec in args.font:
        style, path = spec.split("=", 1)
        if style not in ("normal", "italic"):
            sys.exit(f"未知样式 {style!r}，只能是 normal 或 italic")
        src = Path(path)
        font = TTFont(src, lazy=True)
        if font.flavor:  # WOFF/WOFF2 先解压成 TTF，否则每个分片都要重新解压一次
            print(f"解压 {src.name}（WOFF2 解压较慢，几分钟属正常；直接给 TTF 会快很多）…", flush=True)
            font = TTFont(src)
            font.flavor = None
            src = Path(tmp.name) / (src.stem + ".ttf")
            font.save(src)
        faces.append((style, src, "regular" if style == "normal" else "italic"))

    css_rules, manifest, total = [], [], 0
    for style, src, prefix in faces:
        font = TTFont(src, lazy=True)
        cmap = set(font.getBestCmap())
        slices = plan_slices(cmap, data)
        print(f"{prefix}: {len(cmap)} 个字符 → {len(slices)} 个分片，{args.jobs} 个进程并行…", flush=True)
        t0 = time.time()
        tasks = [(str(src), sorted(cps), str(out_dir / f"{prefix}-{name}.woff2"), features, limits)
                 for name, cps in slices]
        results = {}
        with cf.ProcessPoolExecutor(max_workers=args.jobs) as pool:
            for out_path, glyphs, got in pool.map(build_slice, tasks):
                results[out_path] = (glyphs, got)

        covered, style_rules = set(), []
        sample = TTFont(tasks[0][2])
        desc = css_descriptors(sample)
        for (name, _), task in zip(slices, tasks):
            glyphs, got = results[task[2]]
            covered.update(got)
            file = Path(task[2]).name
            size = os.path.getsize(task[2])
            total += size
            manifest.append({"file": file, "chars": len(got), "glyphs": glyphs, "bytes": size})
            props = [f'font-family:"{args.family}"', f"font-style:{style}"]
            props += [f"{k}:{v}" for k, v in desc.items()]
            props += [f"font-display:{args.display}", f'src:url("{file}") format("woff2")',
                      f"unicode-range:{css_unicode_range(got)}"]
            style_rules.append("@font-face{" + ";".join(props) + "}")
        # 倒序输出，让 base 分片成为该样式的最后一条规则（同名字体里后定义的优先级最高）。
        # 实测 base 写在最前时，Chrome 的 text-spacing-trim 会把相邻标点挤压错，写在最后则与原字体一致。
        css_rules += reversed(style_rules)
        missing = cmap - covered
        if missing:
            sys.exit(f"错误：{len(missing)} 个字符没有进入任何分片：{css_unicode_range(missing)[:200]}")
        print(f"  完成，用时 {time.time() - t0:.0f}s，覆盖全部 {len(cmap)} 个字符", flush=True)

    header = (f"/* {args.family} — 由 _font-tools/split_webfont.py 生成，请勿手改。\n"
              f"   来源：{', '.join(Path(s).name for s in args.font)}；"
              f"{len(css_rules)} 个分片，浏览器按 unicode-range 只下载页面用到的分片。 */\n")
    css_name = f"{args.name}.css"
    (out_dir / css_name).write_text(header + "\n".join(css_rules) + "\n", encoding="utf-8")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    first = TTFont(faces[0][1], lazy=True)
    copyright_ = first["name"].getDebugName(0) or ""
    body = (HERE / "OFL-1.1.txt").read_text(encoding="utf-8")
    (out_dir / "OFL.txt").write_text(copyright_.strip() + "\n\n" + body, encoding="utf-8")
    write_test_page(out_dir, args.family, css_name)

    print(f"输出：{out_dir}/{css_name}，{len(manifest)} 个 WOFF2，合计 {total / 1048576:.1f} MB")


TEST_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Web font test — {{FAMILY}}</title>
<link rel="stylesheet" href="{{CSS}}">
<style>
body{margin:0 auto;max-width:42rem;padding:1rem;line-height:1.7;font-size:1.1rem;color:#17181e;background:#fff}
.t{font-family:"{{FAMILY}}",monospace}
#status{font:14px/1.5 -apple-system,system-ui,sans-serif;padding:.6rem .8rem;border-radius:6px;background:#eee}
#status.ok{background:#d9f5e5}#status.bad{background:#fde2e2}
</style></head><body>
<div id="status">检测中…</div>
<p style="font:13px -apple-system,system-ui,sans-serif;color:#666">对照：未加载成功时下面的文字会退回到等宽字体（monospace），一眼就能看出来。<br><span id="ua"></span></p>
<div class="t">
<h2 style="font-variation-settings:'wght' 700">欢迎来到 Mr. Drinking 的平台！</h2>
<p>「天地玄黄，宇宙洪荒。」这里是用来发布我们想发布的东西的平台（内容涵盖哲学、文学、政治学、历史学、科技数码等等）——如果其中有文章让你觉得实用乃至惊艳，那我们的目的就达到了……</p>
<p>The quick brown fox jumps over the lazy dog. 0123456789 《鄞年・思叙》2026 年版</p>
<p style="font-variation-settings:'wght' 500">字重 500：标签、菜单。</p>
<p><b style="font-variation-settings:'wght' 700">字重 700：粗体。</b></p>
<p style="font-style:italic">斜体：Italic text 与中文斜体混排。</p>
<p>生僻字：龘靐齉爩鱻麤</p>
</div>
<script>
document.getElementById('ua').textContent = navigator.userAgent;
var fam = "{{FAMILY}}";
var samples = ["永", "，。「」", "Aa", "龘"];
Promise.all(samples.map(function (s) { return document.fonts.load('400 16px "' + fam + '"', s); }))
  .catch(function () {})
  .then(function () { return document.fonts.ready; })
  .then(function () {
    var faces = [], loaded = 0, failed = 0;
    document.fonts.forEach(function (f) {
      if (f.family.replace(/"/g, '') !== fam) return;
      faces.push(f);
      if (f.status === 'loaded') loaded++;
      if (f.status === 'error') failed++;
    });
    var el = document.getElementById('status');
    var ok = loaded > 0 && failed === 0;
    el.className = ok ? 'ok' : 'bad';
    el.textContent = (ok ? '✅ 网页字体加载成功' : '❌ 网页字体加载失败') +
      '：共 ' + faces.length + ' 个分片，已加载 ' + loaded + ' 个（本页用到的），失败 ' + failed + ' 个。';
  });
</script>
</body></html>
"""

if __name__ == "__main__":
    main()
