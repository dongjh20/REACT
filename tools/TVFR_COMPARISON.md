# TVFR 网页对比结果：生成与调整

## 一键生成并更新网站媒体

在 WSL 中运行（网站 HTML 不会被此命令改写）：

```bash
cd /home/focus/ros2_ws/src/Multi-Lane-Vehicle-Formation-ROS2
python3 tools/icra/render_tvfr_comparison.py \
  --website /mnt/d/RUANJIAN/website/REACT
```

依赖：NumPy、Matplotlib、OpenCV、Pillow，以及 FFmpeg（含 libx264）或 Python 包
`imageio-ffmpeg`。绘图使用真实 Times New Roman；WSL 默认读取 Windows 字体。
缺依赖时，请在自己的 Python 虚拟环境安装，不需要 ROS：

```bash
python -m pip install numpy matplotlib opencv-python Pillow imageio-ffmpeg
```

本次验证使用的临时依赖位于 `/tmp/react-web-deps`，当前机器也可以直接：

```bash
PYTHONPATH=/tmp/react-web-deps python3 tools/icra/render_tvfr_comparison.py \
  --website /mnt/d/RUANJIAN/website/REACT
```

临时依赖在清理 `/tmp` 后需要重新安装。可以用 `FFMPEG_BINARY` 指定长期安装的 FFmpeg。

## 调整入口

`render_tvfr_comparison.py` 顶部的 `DEFAULTS` 选择实验记录，`STYLE` 统一设置：

- `pre_seconds` / `post_seconds`：默认切换前 2 s、切换后 16 s。
- `fps` / `width` / `scene_height`：视频帧率与画面大小。
- `title_font_px` / `column_font_px` / `wmr_font_px`：视频字体。
- `font_size` / `legend_font_size`：图中文字。
- `trajectory_figsize` / `transient_figsize`：两幅图尺寸。
- `transient_row_spacing` / `transient_padding_inches`：三行运动对比图的行间距和外边距。
- `transient_legend_locations` / `transient_legend_anchors`：三行图例的位置和轴内坐标锚点。
  当前第二行采用 `center right`、`(0.98, 0.28)`，第三行采用 `upper left`、`(0.02, 0.98)`。
- `transient_legend_font_size`：三行图例统一字体大小。
- `before_color` / `current_color`：运动曲线与柱状图的旧/新版本配色。
- `trajectory_linewidth` / `velocity_linewidth`：曲线宽度。
- `trail_seconds`：视频中保留的已执行轨迹时长。

两段视频依次播放，每段左右同步，默认总长 36 s，1× 播放。
不显示日历时间戳；时间轴仅表示相对切换事件的秒数。
两侧采用共同相机、相同比例尺，并复用归档 Mesh；只有显示采样所需的插值，没有轨迹平滑。

只重画图片：

```bash
python3 tools/icra/render_tvfr_comparison.py --figures-only
```

只输出到指定目录、不更新网站：

```bash
python3 tools/icra/render_tvfr_comparison.py --output /tmp/tvfr-preview
```

`--before-32`、`--before-13`、`--current` 可分别指定三个原始记录目录。
网站 `tools` 下提供同名脚本及两个依赖模块的副本；从网站副本运行时应显式传入这三个路径。

## 输出和覆盖规则

默认输出：`result/ICRA-video/comparisons/tvfr_website/`。

- `tvfr_comparison.mp4`：H.264 / yuv420p / Progressive，含 faststart。
- `figures/tvfr_trajectory_comparison.png` 和 `.pdf`：两个切换的左右轨迹对比。
- `figures/tvfr_transient_comparison.png` 和 `.pdf`：横向速度、前向速度、各 WMR 横向多走距离。
- `figures/tvfr_preview_*.png`：视频关键帧。
- `comparison_samples.csv`：统一时间采样的绘图数据。
- `comparison_manifest.json`：输入哈希、代码哈希、窗口、阶段参数、统计与视频信息。

同目录重复运行覆盖上述生成结果，不修改原始记录。视频只有在编码并逐帧解码验证通过后才替换。
`--website` 只覆盖五个固定命名的生成媒体/清单，不修改网站其他视频、图片、HTML 或 CSS。

## 历史记录与解释边界

Without TVFR：3→2 和 1→3 均来自 `20260914_145854_166506`。
这轮尚未启用平滑时变参考，阶段配置未设置 `transition_duration`，沿用瞬时切换语义。
With TVFR：两段均来自 `20260914_210201_219110`，参考过渡分别为 12 s 和 10 s。

脚本在导出前检查 TVFR 配置，左侧必须省略/为零、右侧必须大于零；有实际参数回读时一并交叉核对。
误选此前已启用 TVFR 的运行（例如 `20260914_171816_922224`）会直接报错，不再只改标签。

两轮还存在走廊位置/过渡空间、触发条件、加速度和其他优化参数差异，完整配置快照和哈希保留在清单中。
图中保留真实世界坐标，每侧使用各自归档障碍地图，不平移或修改测量数据。
标签准确表示 TVFR 的开关状态，但这仍不是只有 TVFR 一个变量改变的受控消融。
网页按要求移除了原来的说明段落及 Source records and measurements 链接。

横向多走距离在切换后 0～16 s 计算：y 坐标总变差减去首尾净位移的绝对值。
该量不把正常的单调横向换位全部当作抖动，也不替代原编队误差定义。

## 验证

```bash
python3 -m unittest discover -s tools/icra -p test_tvfr_comparison.py -v
```

网站 `tools/verify_tvfr_site.py` 使用 Playwright Chromium 验证桌面/手机布局、公式、
标签页、视频播放和第二段拖动。测试服务只监听本机，并阻止外部 CDN/统计请求，确认新增部分离线可用。
MathJax 3.2.2 的 TeX-SVG 组件本地保存在 `static/vendor/mathjax`，保留 Apache 2.0 许可证。
来源：https://docs.mathjax.org/en/v3.2/web/components/combined.html

```bash
PYTHONPATH=/tmp/react-web-deps PLAYWRIGHT_BROWSERS_PATH=/tmp/react-playwright \
python3 /mnt/d/RUANJIAN/website/REACT/tools/verify_tvfr_site.py \
  --site /mnt/d/RUANJIAN/website/REACT \
  --output /tmp/react-site-check
```

## 网站修改范围

实物实验后增加 TVFR 对比区、三项附加代价说明区；移除动态障碍小节的 HTML，原媒体仍保留。
修正重复视频 ID、一个多余闭合标签；原脚本使用原生 DOMContentLoaded，取消其 jQuery CDN 依赖，
并在不存在回顶部按钮时跳过滚动处理。原网站其他未提交修改保持不动。
