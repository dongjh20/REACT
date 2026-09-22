# ICRA 投稿展示视频：生成与加速说明

脚本：`tools/render_icra_submission.py`

将网站的三个现有视频串联合成投稿视频，自动生成三张开头标题页和片尾网址页。脚本只读取原视频，不修改网站页面或原视频，不需要 ROS 或仿真环境。

## 快速使用

在 PowerShell 中进入网站目录并运行：

```powershell
cd D:\RUANJIAN\website\REACT
python tools/render_icra_submission.py
```

默认输出到：

```text
static/videos/CFOO_ICRA_supplementary.mp4
```

**成功生成并通过检查后会覆盖同名成片**；需要保留旧版时，可先复制旧文件，或通过 `--output` 指定新文件名。原始三个视频始终保留。

仅查看输入文件、预计时长和输出位置，不生成视频：

```powershell
python tools/render_icra_submission.py --dry-run
```

检查已生成的视频格式、时长、大小和完整解码：

```powershell
python tools/render_icra_submission.py --check-only
```

## 当前默认版本

| 顺序 | 内容 | 播放方式 / 时长 |
| --- | --- | --- |
| 1 | 1. Simulation 标题页 | 4 秒 |
| 2 | `formation_navigation_four_panels.mp4` | 1.5× |
| 3 | 2. Real-World Experiment 标题页 | 4 秒 |
| 4 | `real-transition.mp4` | 1.25× |
| 5 | 3. Ablation Study 标题页 | 4 秒 |
| 6 | `tvfr_comparison.mp4` | 原速 1× |
| 7 | CFOO、More details and videos:、匿名网址 | 7 秒 |

- 片尾网址：`https://anonymous.4open.science/w/REACT-F829/`
- 网址使用 **64 px 深色粗体**，整行居中，便于阅读和截图。
- 输出为 **1920×1080、30 FPS、H.264 High、yuv420p 的 MP4**，不带音轨。
- 用浅色留边保留仿真和消融视频的画面比例，不裁剪实验内容。
- 当前三个输入文件合成后约 **2 分 48 秒、18.97 MB**；更换输入视频或编码器版本后，以运行结束的实际检查结果为准。
- 采用两遍编码，默认目标码率 900 kbit/s；启用 faststart，方便播放器加载。

## 调整加速倍率和停留时长

例如将仿真改为 1.6×、实车改为 1.3×，标题页 4 秒、片尾 7 秒：

```powershell
python tools/render_icra_submission.py --simulation-speed 1.6 --real-speed 1.3 --title-duration 4 --ending-duration 7
```

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `--simulation-speed` | `1.5` | 仿真视频加速倍率；1 表示原速 |
| `--real-speed` | `1.25` | 实车视频加速倍率 |
| `--title-duration` | `4` | 每个开头标题页的秒数，共三页 |
| `--ending-duration` | `7` | 片尾网址页秒数 |
| `--fps` | `30` | 输出帧率，脚本要求至少 20 |
| `--bitrate` | `900` | 视频目标码率，单位 kbit/s |
| `--url-font-size` | `64` | 片尾网址字号，单位 px |
| `--website` | 上述匿名网址 | 片尾显示的网址 |
| `--videos-dir` | 网站的 `static/videos` | 三个输入视频所在目录 |
| `--output` | 输入目录下的默认成片名 | 自定义输出路径 |
| `--ffmpeg` | 自动查找 | FFmpeg 可执行文件路径 |

仿真画面中原有的 “1× Playback” 会被替换为实际倍率；实车画面右上角会标注实际倍率。消融视频保留原速及其原有 1× 标注，暂不提供消融加速参数。

预计总时长为：

```text
仿真原时长 / 仿真倍率 + 实车原时长 / 实车倍率 + 消融原时长
+ 3 × 标题页时长 + 片尾时长
```

## 20 MB 和 3 分钟限制

脚本在编码前检查预计时长，在编码后检查实际结果：

- 总时长必须小于 180 秒，预检查预留约 0.2 秒余量。
- 文件大小必须小于 **20,000,000 字节**，采用较严格的十进制 20 MB。
- 必须为 H.264、指定帧率、1920×1080，并能完整解码。
- 未通过检查时不会替换已有成片。

如果更换素材后超过大小限制，优先降低码率，例如：

```powershell
python tools/render_icra_submission.py --bitrate 850
```

如果时长超限，增加仿真或实车的倍率，或缩短标题页和片尾。降低帧率不会直接缩短时长；在固定码率模式下，降低码率才是控制文件大小的直接方式。

## 运行环境

- Python 3，安装 Pillow（用于生成标题页）。
- FFmpeg，需包含 `libx264`、`drawtext`、`scale`、`fps` 和 `concat`。
- Windows 下使用系统 Arial、Arial Bold 和 Times New Roman 字体；Linux 下使用 DejaVu 字体，排版会略有差别。

如果当前 Python 缺少依赖，可以在该环境安装：

```powershell
python -m pip install pillow imageio-ffmpeg
```

FFmpeg 的查找顺序为：`--ffmpeg` 参数、系统 PATH、当前 Python 的 `imageio_ffmpeg`，最后尝试本机已有路径：

```text
D:/RUANJIAN/python/anaconda/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win64-v4.2.2.exe
```

也可以明确指定：

```powershell
python tools/render_icra_submission.py --ffmpeg "D:/RUANJIAN/python/anaconda/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win64-v4.2.2.exe"
```

## 实现说明与素材更新注意事项

1. 所有标题页由代码生成，不依赖临时 PNG。修改标题英文可编辑脚本的 `make_cards()`。
2. 临时图片、字体副本、两遍编码日志和中间成片均位于临时目录，结束后自动清理。
3. 加速采用时间戳缩放，并显式裁定片段时长，避免旧版 FFmpeg 在加速后保留原片尾时间而出现停帧。
4. 仿真倍率标注的覆盖位置针对当前 **1800×1644** 四分屏素材；如果重新布局该素材，需核对代码中标注的位置。脚本发现仿真分辨率改变时会停止。
5. 消融视频的显示尺寸按当前 **1920×642** 素材设置；若其纵横比改变，请同步修改该片段的缩放和留边参数。
6. 网站标题、作者信息、追踪代码及 Git 远程设置均不受此脚本影响。运行脚本不会提交或推送仓库。
