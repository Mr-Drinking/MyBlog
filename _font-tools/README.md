# 网页字体修复说明（Safari 全部显示为苹方）

## 结论

眼下字体在**所有浏览器**里都没有加载成功（文件 404），只是你自己的 Windows / Android 设备看不出来。

1. **字体文件不在服务器上。** `assets/css/style.css` 里的 `@font-face` 指向 `assets/dynamic/fonts/sarasauipropdigitssc/*.woff2`，线上这些地址全部返回 404。仓库历史显示：2025-06 到 2026-09 用思源宋体 / 思源黑体时，字体文件从来没进过仓库；2026-09-05 更纱字体传上去了，但 2026-10-09 的 Publii 同步又把它们删了，CSS 却还在引用。
2. **原版文件即使在线，也只有较新的 Chrome 能用。** 这两个 WOFF2 解压后分别是 37.9 MiB 和 51.6 MiB。Chromium 144 及更早的版本解码 WOFF2 有 30 MiB 上限，超过就报 `OTS parsing error: Failed to convert WOFF 2.0 font to SFNT`；从 Chrome 145（2026 年 2 月）开始没有这个问题。实测：141、144 失败，145、146、148、155、158 都能加载。所以自动更新的 Chrome / Edge 没问题（你之前实测可行就是这种情况），但内核早于 145 的 Chromium 系浏览器会失败，国内不少浏览器和 App 内置浏览器的内核都比较旧。Safari 的 WOFF2 由系统解码，这里测不了，请用下面的 `original-test` 页实测。现在的分片每个解压后都不到 1 MB，不管哪个浏览器都碰不到这类上限。
3. **为什么你那边的 Chrome 看着正常。** 网页字体加载失败后会退回到 `sans-serif`，也就是系统字体。你的 Windows 用 Windows System Font Modifier 把微软雅黑 / Segoe UI 换成了更纱，Android 的系统字体也是更纱，退回的结果和本来想要的字体一模一样。苹果设备换不了系统字体，退回的就是苹方。其他访客在 Windows 上看到的是微软雅黑；在普通 Android 上看到的是思源黑体（Noto Sans CJK），它就是更纱汉字部分的来源，所以几乎看不出差别。
4. 「标点压缩」靠的是字体里的 `chws` / `halt` 特性，字体没加载，这个功能自然也没有。

## 修复思路

用 `split_webfont.py` 把字体切成按 `unicode-range` 按需加载的小文件（Google Fonts 提供中文字体也是这么做的）：

- 每个文件解压后都不到 1 MB（最大的是 808 KB），离 30 MB 上限很远；WOFF2 文件最大 303 KB，中位数 58 KB。
- 浏览器只下载当前页面用到的字所在的分片：本站文章页正体字体的下载量中位数约 1.45 MB，最长的文章约 3.9 MB。原先每个页面都要 preload 两个合计 32 MB 的文件。
- 汉字按 Google Fonts 的使用频率分组，常用字集中在少数几个分片里，跨页面可以复用缓存。
- 所有中文标点和基本拉丁字符放在同一个 `base` 分片里，并且这条规则写在 CSS 的最后。这两点都是为了让标点挤压（`chws`）在 Chrome 里与原字体完全一致；实测拆开或写在前面都会出错。
- 字重轴保留 400–900（原字体是 200–900，本站只用到 400 / 500 / 700），体积约减三成。因为下限正好是默认值 400，其余变化数据都不需要重算：实测各字重下的字宽与原字体完全相同，只有个别字形有肉眼看不出的抗锯齿差异。
- 只保留浏览器默认启用的、以及中文排版相关的 OpenType 特性；`cvXX` / `ssXX` 等需要手动开启的特性去掉了，需要的话加 `--all-features`。

这些文件放在 Publii 的媒体文件目录里，不再依赖主题的 dynamic fonts 机制，主题或 Publii 升级时不会再被删掉。

## 在 Publii 里的操作步骤

1. **主题设置 → Fonts**：把正文字体、标题字体都改回 System UI（系统字体）。这样生成的 CSS 里就不会再有指向 404 文件的 `@font-face`，页面里两个 32 MB 的 `<link rel="preload">` 也会消失。如果 Noto Sans Mono 是你通过主题覆盖（override）加的，也一并去掉。
2. **复制字体文件**：把本分支里 `media/files/fonts/` 下的 `sarasa-ui-pd-sc/` 和 `noto-sans-mono/` 两个目录复制到
   `文档/Publii/sites/<站点目录>/input/media/files/fonts/`。`original-test/` 只是测试用的，不要复制。
3. **工具与插件 → Custom HTML → Head**，加入：
   ```html
   <link rel="stylesheet" href="/media/files/fonts/sarasa-ui-pd-sc/sarasa-ui-pd-sc.css">
   <link rel="stylesheet" href="/media/files/fonts/noto-sans-mono/noto-sans-mono.css">
   ```
4. **工具与插件 → Custom CSS**，加入（用 `html:root` 是为了无论这段 CSS 放在主题 CSS 前面还是后面都能生效）：
   ```css
   html:root {
     --body-font: "Sarasa Ui PD SC Web", system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
     --heading-font: var(--body-font);
     --monospace-font: "Noto Sans Mono Web", var(--body-font), monospace;
   }
   ```
5. 渲染并同步，然后确认 `https://mrdrinking.pages.dev/media/files/fonts/sarasa-ui-pd-sc/sarasa-ui-pd-sc.css` 能打开（不是 404）。
6. 首页那段「Safari 似乎无法正常启用一些功能」的提示可以删掉了。

字体用的家族名是 `Sarasa Ui PD SC Web`，故意和你电脑里装的字体不同名。这样以后网页字体再出问题，你在自己的设备上也能看出来，不会被系统字体掩盖。

## 怎么验证（别只靠肉眼）

你的 Windows / Android 设备上，网页字体失败了看起来也是对的，所以要看浏览器实际用了什么字体：

- **测试页**：打开 `/media/files/fonts/sarasa-ui-pd-sc/test.html`。顶部显示 ✅ 才算成功；失败时示例文字会退回到等宽字体，一眼就能看出来。最好用 iPhone / Mac 的 Safari 打开一次。
- **Chrome**：开发者工具 → Elements → 选中一段正文 → Computed 面板最底部的 Rendered Fonts。成功时显示 `Sarasa Ui VF PropDigits SC — Network resource`；显示 `Local file` 就说明用的是你本机的字体。
- **Safari**：Web Inspector → Elements → 右侧的 Font 面板。
- **原版整包字体在 Safari 里能不能用**：打开预览分支上的 `/media/files/fonts/original-test/`。页面会分别显示原版正体、原版斜体和切片版能否加载，以及标点挤压是否生效。

## 换成新版字体时重新生成

这里的分片是用仓库历史里的 1.0.40.3 版生成的。换成你最新的构建（例如 1.0.42）时，在仓库根目录执行：

```sh
pip install fonttools brotli
python3 _font-tools/split_webfont.py \
    --family "Sarasa Ui PD SC Web" --name sarasa-ui-pd-sc \
    --font normal=Sarasa-Ui-VF-PropDigits-SC.ttf \
    --font italic=Sarasa-Ui-VF-PropDigits-SC-Italic.ttf \
    --wght 400:900 --out media/files/fonts/sarasa-ui-pd-sc
```

输入建议用 TTF；WOFF2 也能用，只是解压要多花几分钟。4 核机器上整套大约 4 分钟。脚本会先清空输出目录里的旧 `.woff2`，最后检查所有字符都被分到了某个分片里。生成后把目录重新复制进 Publii 即可，Custom HTML / CSS 不用改。

## 文件说明

| 路径 | 内容 |
| --- | --- |
| `_font-tools/split_webfont.py` | 切片脚本 |
| `_font-tools/unicode-slices.json` | 字符分组数据：Google Fonts 的拉丁 / 希腊 / 西里尔子集，以及 Noto Sans SC 的按频率分组 |
| `_font-tools/OFL-1.1.txt` | SIL OFL 1.1 许可证正文，生成时会附上字体的版权行写入输出目录 |
| `media/files/fonts/sarasa-ui-pd-sc/` | 更纱：正体、斜体各 161 个分片，以及 CSS、`manifest.json`、`test.html`、`OFL.txt` |
| `media/files/fonts/noto-sans-mono/` | Noto Sans Mono：15 个分片 |
| `media/files/fonts/original-test/` | 原版整包字体（1.0.40.3）和测试页，只用于在各浏览器里测试，不要放进 Publii |
