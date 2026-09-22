#!/usr/bin/env python3
"""Combine the three website videos into the anonymous ICRA supplementary MP4."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
NAMES = ['formation_navigation_four_panels.mp4', 'real-transition.mp4', 'tvfr_comparison.mp4']
WEBSITE = 'https://anonymous.4open.science/w/REACT-F829/'


def find_ffmpeg(explicit):
    if explicit:
        return explicit
    if shutil.which('ffmpeg'):
        return shutil.which('ffmpeg')
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        fallback = Path('D:/RUANJIAN/python/anaconda/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win64-v4.2.2.exe')
        if fallback.is_file():
            return str(fallback)
    raise RuntimeError('FFmpeg not found. Supply --ffmpeg PATH or install imageio-ffmpeg.')


def probe(ffmpeg, path):
    # FFmpeg returns nonzero when no output is specified; the input report is useful.
    result = subprocess.run([ffmpeg, '-hide_banner', '-i', str(path)], capture_output=True)
    text = result.stderr.decode(errors='replace')
    m = re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)', text)
    if not m:
        raise RuntimeError(f'Cannot read {path}:\n{text}')
    return int(m[1])*3600 + int(m[2])*60 + float(m[3]), text


def validate(ffmpeg, path, fps):
    duration, report = probe(ffmpeg, path)
    size = path.stat().st_size
    if size >= 20_000_000 or duration >= 180:
        raise RuntimeError(f'Output exceeds limits: {duration:.2f} s, {size/1e6:.2f} MB. Increase playback speed or reduce --bitrate.')
    if 'h264' not in report or f'{fps} fps' not in report or '1920x1080' not in report:
        raise RuntimeError('Unexpected output codec, frame rate, or resolution.\n'+report)
    result = subprocess.run([ffmpeg, '-v', 'error', '-i', str(path), '-f', 'null', os.devnull], capture_output=True)
    if result.returncode or result.stderr.strip():
        raise RuntimeError('Decode check failed: '+result.stderr.decode(errors='replace'))
    print(f'Verified: {duration:.2f} s | {size/1e6:.2f} MB | H.264 | {fps} FPS | 1920x1080', flush=True)


def make_cards(work, args):
    from PIL import Image, ImageDraw, ImageFont
    if Path('C:/Windows/Fonts/arial.ttf').is_file():
        regular, bold, serif = [Path('C:/Windows/Fonts')/n for n in ['arial.ttf', 'arialbd.ttf', 'times.ttf']]
    else:
        fonts = Path('/usr/share/fonts/truetype/dejavu')
        regular, bold, serif = [fonts/n for n in ['DejaVuSans.ttf', 'DejaVuSans-Bold.ttf', 'DejaVuSerif.ttf']]
    for source, name in [(regular, 'arial.ttf'), (serif, 'times.ttf')]:
        shutil.copy2(source, work/name)
    def card(name, title, subtitle, small, ending=False):
        im = Image.new('RGB', (1920, 1080), '#FAFCFD')
        draw = ImageDraw.Draw(im)
        draw.rectangle((870, 310, 1050, 317), fill='#33778E')
        for text, y, size, color, fontpath in [
            (title, 410, 76, '#17364D', bold),
            (subtitle, 528, 39, '#475B68', regular),
            (small, 652, args.url_font_size if ending else 31, '#0B2239' if ending else '#61717D', bold if ending else regular),
        ]:
            font = ImageFont.truetype(str(fontpath), size)
            if draw.textlength(text, font=font) > 1720:
                raise RuntimeError('Title/URL too wide. Shorten text or decrease --url-font-size.')
            draw.text((960, y), text, font=font, fill=color, anchor='mm')
        im.save(work/name)
    card('title1.png', '1. Simulation', 'Continuous formation navigation', f'Playback: {args.simulation_speed:g}×')
    card('title2.png', '2. Real-World Experiment', 'Continuous formation navigation', f'Playback: {args.real_speed:g}×')
    card('title3.png', '3. Ablation Study', 'Formation transitions with and without TVFR', 'Playback: 1×')
    card('ending.png', 'CFOO', 'More details and videos:', args.website, True)
    (work/'simulation-label.txt').write_text(f'(b) Tracking view ({args.simulation_speed:g}× Playback)', encoding='utf-8')
    (work/'real-label.txt').write_text(f'{args.real_speed:g}× Playback', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--videos-dir', type=Path, default=ROOT/'static/videos')
    parser.add_argument('--output', type=Path, help='Default: VIDEOS_DIR/CFOO_ICRA_supplementary.mp4')
    parser.add_argument('--ffmpeg', help='FFmpeg executable path')
    parser.add_argument('--simulation-speed', type=float, default=1.5)
    parser.add_argument('--real-speed', type=float, default=1.25)
    parser.add_argument('--title-duration', type=float, default=4)
    parser.add_argument('--ending-duration', type=float, default=7)
    parser.add_argument('--fps', type=int, default=30)
    parser.add_argument('--bitrate', type=int, default=900, help='Target video bitrate in kbit/s')
    parser.add_argument('--website', default=WEBSITE)
    parser.add_argument('--url-font-size', type=int, default=64)
    parser.add_argument('--dry-run', action='store_true', help='Check inputs and planned duration only')
    parser.add_argument('--check-only', action='store_true', help='Validate an existing output without encoding')
    args = parser.parse_args()
    if args.fps < 20 or min(args.simulation_speed, args.real_speed, args.title_duration, args.ending_duration, args.bitrate, args.url_font_size) <= 0:
        parser.error('FPS must be >=20 and all speeds, durations, bitrate and font size must be positive.')
    ffmpeg = find_ffmpeg(args.ffmpeg)
    output = (args.output or args.videos_dir/'CFOO_ICRA_supplementary.mp4').resolve()
    if args.check_only:
        validate(ffmpeg, output, args.fps)
        return
    sources = [(args.videos_dir/name).resolve() for name in NAMES]
    if output in sources:
        parser.error('Output must not overwrite a source video.')
    reports = [probe(ffmpeg, p) for p in sources]
    if '1800x1644' not in reports[0][1]:
        parser.error('Simulation dimensions changed; update the embedded playback-label coordinates before rendering.')
    durations = [reports[0][0]/args.simulation_speed, reports[1][0]/args.real_speed, reports[2][0]]
    total = sum(durations)+3*args.title_duration+args.ending_duration
    if total >= 179.8:
        parser.error(f'Planned duration {total:.2f}s is too long. Increase --simulation-speed or --real-speed.')
    print(f'Planned: {total:.2f} s | {args.fps} FPS | {args.bitrate} kbit/s\nOutput: {output}', flush=True)
    if args.dry_run:
        return
    with tempfile.TemporaryDirectory(prefix='cfoo-video-') as directory:
        work = Path(directory)
        make_cards(work, args)
        sequence = [work/'title1.png', sources[0], work/'title2.png', sources[1], work/'title3.png', sources[2], work/'ending.png']
        command = [ffmpeg, '-hide_banner', '-y', '-filter_complex_threads', '2']
        for i, path in enumerate(sequence):
            if i % 2 == 0:
                command += ['-loop', '1', '-framerate', str(args.fps), '-t', str(args.ending_duration if i == 6 else args.title_duration)]
            command += ['-i', str(path)]
        filters = [f'[{i}:v]setpts=PTS-STARTPTS,setsar=1,format=yuv420p[v{i}]' for i in [0, 2, 4, 6]]
        # Explicit trim avoids old FFmpeg versions preserving the original EOF
        # timestamp after setpts acceleration and padding with frozen frames.
        filters += [
            f'[1:v]setpts=(PTS-STARTPTS)/{args.simulation_speed},drawbox=x=3:y=385:w=600:h=37:color=0xfbfdfa:t=fill,drawtext=fontfile=times.ttf:textfile=simulation-label.txt:fontsize=31:fontcolor=0x333333:x=15:y=391,scale=-2:1080:flags=lanczos,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0xfafcfd,setsar=1,fps={args.fps},trim=duration={durations[0]},format=yuv420p[v1]',
            f'[3:v]setpts=(PTS-STARTPTS)/{args.real_speed},scale=1920:1080,setsar=1,fps={args.fps},trim=duration={durations[1]},drawtext=fontfile=arial.ttf:textfile=real-label.txt:fontsize=30:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=10:x=w-tw-30:y=30,format=yuv420p[v3]',
            f'[5:v]setpts=PTS-STARTPTS,scale=1920:642:flags=lanczos,pad=1920:1080:0:219:color=0xfafcfd,setsar=1,fps={args.fps},format=yuv420p[v5]',
            '[v0][v1][v2][v3][v4][v5][v6]concat=n=7:v=1:a=0[out]',
        ]
        command += ['-filter_complex', ';'.join(filters), '-map', '[out]', '-an', '-c:v', 'libx264', '-preset', 'medium', '-threads', '6', '-b:v', f'{args.bitrate}k', '-pix_fmt', 'yuv420p', '-profile:v', 'high', '-level:v', '4.1', '-r', str(args.fps), '-g', str(2*args.fps), '-passlogfile', 'pass']
        result_path = work/'CFOO_ICRA_supplementary.mp4'
        for number in [1, 2]:
            print(f'Encoding pass {number}/2...', flush=True)
            cmd = command+['-pass', str(number), '-nostats']
            cmd += ['-f', 'null', os.devnull] if number == 1 else ['-movflags', '+faststart', '-metadata', 'title=CFOO: Continuous Formation Navigation', str(result_path)]
            result = subprocess.run(cmd, cwd=work, capture_output=True)
            if result.returncode:
                raise RuntimeError(result.stderr.decode(errors='replace')[-5000:])
        validate(ffmpeg, result_path, args.fps)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(result_path, output)
        print(f'Saved: {output}', flush=True)


if __name__ == '__main__':
    main()
