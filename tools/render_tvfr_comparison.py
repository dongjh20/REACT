#!/usr/bin/env python3
"""Reproducible, event-aligned historical/current comparison; no ROS required.

Uses measured odometry, not fitted paths or decoded old-video pixels. Historical
clips are selected by their actual reference-mode configuration, with provenance
and other configuration differences saved explicitly. Keep the two existing
renderer modules beside this script.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
import cv2
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, Rectangle, Patch
import plot_recorded_figures as plots
import render_recorded_video as video

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / 'result/ICRA-video'
DEFAULTS = {
    'before_32': '20260916_181033_397685',
    'before_13': '20260916_181033_397685',
    'current': '20260914_210201_219110',
}
# All presentation/window parameters are grouped here. Time is relative to the
# recorded switch event; no calendar timestamps are drawn anywhere.
STYLE = {
    'pre_seconds': 2., 'post_seconds': 16., 'sample_seconds': .02,
    'fps': 25, 'width': 1920, 'scene_height': 500,
    'title_height': 58, 'column_height': 44, 'footer_height': 40,
    'title_font_px': 34, 'column_font_px': 30, 'wmr_font_px': 24,
    'footer_font_px': 24, 'font_size': 17, 'legend_font_size': 15,
    'trajectory_figsize': (15, 4.8), 'transient_figsize': (15, 12),
    'transient_row_spacing': .20, 'transient_padding_inches': .08,
    'transient_legend_font_size': 15,
    # Anchors use axes fractions; rows are lateral speed, forward speed, travel.
    'transient_legend_locations': ('upper right', 'center right', 'upper left'),
    'transient_legend_anchors': ((.98, .98), (.98, .28), (.02, .98)),
    'transient_top_headroom': (.30, 0., .45),  # Fraction of the shared row y-range.
    'transition_label_x_seconds': 1.2,
    'transition_label_y_fraction': .88,
    'transition_marker_color': '#6c7883',
    'dpi': 180, 'trajectory_linewidth': 1.6, 'velocity_linewidth': 1.1,
    'trail_seconds': 6., 'trail_width_px': 2,
    'before_color': '#b96f46', 'current_color': '#286b91',
}
LABELS = ('Without TVFR', 'With TVFR')
NOTE = ('The historical method uses instantaneous formation switching (TVFR disabled); '
        'the comparison method uses TVFR in the exact same environment. '
        'Obstacle geometry, route, initial states, goal and trigger positions are checked. '
        'Trigger-rule equality and switch-state differences are reported separately. '
        'Other method settings (acceleration, preview and costs) still differ, '
        'so this is not a single-factor ablation. Original records are not modified.')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_clip(directory, event_index, relative_times):
    source, cfg, geometry, events, cars = plots.read_records(directory)
    if len(events) <= event_index:
        raise ValueError('Missing requested switch event')
    start = float(events[event_index]['t'])
    absolute = start + relative_times
    if any(absolute[0] < a[0, 0] or absolute[-1] > a[-1, 0] for a in cars):
        raise ValueError(f'{source}: comparison window exceeds measured coverage')
    samples = np.stack([np.column_stack([
        np.interp(absolute, a[:, 0], np.unwrap(a[:, j]) if j == 7 else a[:, j])
        for j in range(1, 8)]) for a in cars])
    return dict(source=source, cfg=cfg, geometry=geometry, events=events,
                event_index=event_index, event_time=start, times=relative_times,
                samples=samples, cars=cars)


def metrics(clip):
    mask = clip['times'] >= -1e-8
    a = clip['samples'][:, mask]
    time = clip['times'][mask]
    excess = np.maximum(0., np.abs(np.diff(a[:, :, 1], axis=1)).sum(axis=1)
                        - np.abs(a[:, -1, 1]-a[:, 0, 1]))
    rows = []
    for i, p in enumerate(a):
        rows.append(dict(wmr=i, excess_lateral_travel_m=float(excess[i]),
                         peak_abs_vy_mps=float(np.abs(p[:, 4]).max()),
                         minimum_vx_mps=float(p[:, 3].min()),
                         peak_abs_yaw_deg=float(np.degrees(np.abs(p[:, 6])).max()),
                         reverse_distance_m=float(np.trapz(np.maximum(-p[:, 3], 0), time))))
    return dict(per_wmr=rows, total_excess_lateral_travel_m=float(excess.sum()),
                peak_abs_vy_mps=max(r['peak_abs_vy_mps'] for r in rows),
                minimum_vx_mps=min(r['minimum_vx_mps'] for r in rows))


def verify_tvfr_label(clip, enabled):
    """Missing/zero duration retains instantaneous switching in the schema."""
    stage = clip['cfg']['experiment']['stages'][clip['event_index']]
    duration = float(stage.get('transition_duration', 0.))
    lateral_duration = float(stage.get('lateral_transition_duration', duration))
    if not np.isfinite(duration) or duration < 0 or (duration > 0) != enabled:
        raise ValueError(f'{clip["source"]}: transition_duration={duration} contradicts '
                         f'{LABELS[int(enabled)]}; select a correctly configured run')
    if not np.isfinite(lateral_duration) or lateral_duration < 0 or (lateral_duration > 0) != enabled:
        raise ValueError('Lateral transition duration contradicts the TVFR label')
    # Cross-check runtime parameter readback where it was archived. Older runs
    # predate this parameter and do not have a corresponding readback field.
    report = json.loads((clip['source']/'report.json').read_text())
    for params in report.get('actual_parameters', {}).values():
        values = params.get('formation_switch/transition_durations')
        if values is not None and not np.isclose(values[clip['event_index']], duration):
            raise ValueError('Runtime TVFR duration does not match config snapshot')
        lateral_values = params.get('formation_switch/lateral_transition_durations')
        if lateral_values is not None and not np.isclose(lateral_values[clip['event_index']], lateral_duration):
            raise ValueError('Runtime lateral TVFR duration does not match config snapshot')
    return dict(enabled=enabled, transition_duration_seconds=duration,
                lateral_transition_duration_seconds=lateral_duration,
                evidence='archived stage configuration; missing/zero means instantaneous switching')


def verify_matched_environment(pair):
    """Fail closed: never silently compare maps translated by 5 m again."""
    left, right = pair
    if left['geometry'] != right['geometry']:
        raise ValueError('Comparison environment mismatch: archived obstacle geometry')
    paths = [('initial_states',), ('map',), ('local_sensing',),
             ('experiment', 'route'), ('experiment', 'forest'), ('experiment', 'corridors'),
             ('global_goal',), ('formation_switch', 'trigger_x')]
    for path in paths:
        a, b = left['cfg'], right['cfg']
        for key in path:
            a, b = a[key], b[key]
        if a != b:
            raise ValueError('Comparison environment mismatch: '+'.'.join(path))
    encoded = json.dumps(left['geometry'], sort_keys=True, separators=(',', ':')).encode()
    return dict(exact_match=True, geometry_sha256=hashlib.sha256(encoded).hexdigest(),
                cylinder_count=len(left['geometry']['cylinders']), box_count=len(left['geometry']['boxes']),
                configuration_fields=['.'.join(path) for path in paths],
                coordinate_transform='none; both use unmodified recorded world coordinates')


def verify_completed_run(source):
    report = json.loads((Path(source)/'report.json').read_text())
    if report.get('passed') is not True:
        # A historical recorder can lose a one-shot startup service response.
        # Do not alter its report or ignore its failure: independently recheck
        # every acceptance condition with complete live readback, if available.
        if (Path(source)/'historical_replay_validation.json').is_file():
            from verify_historical_replay import validate
            fresh = validate(source)
            saved = json.loads((Path(source)/'historical_replay_validation.json').read_text())
            if fresh != saved:
                raise ValueError('Supplemental validation evidence changed; refusing stale acceptance')
            return dict(passed=True, report_sha256=digest(Path(source)/'report.json'),
                        original_recorder_passed=False, validation_kind='independent_complete_runtime_checks',
                        supplemental_validation_sha256=digest(Path(source)/'historical_replay_validation.json'))
        raise ValueError(f'{source}: full runtime validation failed; cannot publish this recording')
    return dict(passed=True, report_sha256=digest(Path(source)/'report.json'))


def trigger_audit(pair, require_match=False):
    """Equal rules are not a claim that callback times or dynamic states coincide."""
    configurations = []
    states = []
    for clip in pair:
        cfg = clip['cfg']
        switch = cfg['formation_switch']
        configurations.append({**{k: switch[k] for k in (
            'reference_vehicle', 'timer_period', 'trigger_x', 'second_require_settled',
            'second_settle_rms', 'second_settle_duration', 'odom_max_age')},
            'all_wmr_past_x': [s.get('all_wmr_past_x') for s in cfg['experiment']['stages']]})
        a = load_clip(clip['source'], clip['event_index'], np.array([0.]))['samples'][:, 0]
        states.append(dict(mean_position_m=a[:, :2].mean(axis=0).tolist(),
                           minimum_x_m=float(a[:, 0].min()), positions_m=a[:, :2].tolist(),
                           velocities_mps=a[:, 3:5].tolist()))
    equal = configurations[0] == configurations[1]
    if require_match and not equal:
        raise ValueError('Comparison trigger rules differ')
    return dict(exact_rule_match=equal, rules=configurations, states_at_switch=states,
                mean_position_difference_m=(np.array(states[1]['mean_position_m'])-
                                            np.array(states[0]['mean_position_m'])).tolist(),
                alignment='each run actual switch receipt is t=0; no spatial or per-WMR shifts')


def draw_obstacles(ax, geometry):
    for x, y, r in geometry['cylinders']:
        ax.add_patch(Circle((x, y), r, color='#264d85', zorder=1))
    for x, y, w, h in geometry['boxes']:
        ax.add_patch(Rectangle((x-w/2, y-h/2), w, h, color='#264d85', zorder=1))


def save_figure(fig, out, name):
    for extension in ('png', 'pdf'):
        fig.savefig(out / f'{name}.{extension}', dpi=STYLE['dpi'])
    plots.plt.close(fig)


def render_figures(pairs, output):
    plt = plots.plt
    fig, axes = plt.subplots(2, 2, figsize=STYLE['trajectory_figsize'])
    fig.subplots_adjust(left=.055, right=.985, top=.83, bottom=.13, hspace=.65, wspace=.13)
    for row, pair in enumerate(pairs):
        xy = np.concatenate([c['samples'][:, :, :2].reshape(-1, 2) for c in pair])
        lo, hi = xy.min(axis=0)-.6, xy.max(axis=0)+.6
        for col, c in enumerate(pair):
            ax = axes[row, col]
            draw_obstacles(ax, c['geometry'])
            colors = np.asarray(c['cfg']['visualization']['optimal_rgb']) / 255.
            zero = np.argmin(abs(c['times']))
            for i, a in enumerate(c['samples']):
                ax.plot(a[:, 0], a[:, 1], color=colors[i], lw=STYLE['trajectory_linewidth'], zorder=3)
                ax.plot(a[zero, 0], a[zero, 1], 'o', ms=4, color=colors[i], zorder=4)
            ax.set(xlim=(lo[0], hi[0]), ylim=(lo[1], hi[1]), xlabel='x [m]', ylabel='y [m]',
                   title=f'{("3 → 2", "1 → 3")[row]} columns | {LABELS[col]}')
            ax.set_aspect('equal', adjustable='box')
            ax.grid(alpha=.15)
    colors = np.asarray(pairs[0][1]['cfg']['visualization']['optimal_rgb'])/255.
    handles = [Line2D([], [], color=c, lw=2, label=f'WMR {i}') for i, c in enumerate(colors)]
    handles.append(Line2D([], [], color='#555', marker='o', ls='', label='Switch instant'))
    fig.legend(handles=handles, loc='upper center', ncol=8, frameon=False,
               fontsize=STYLE['legend_font_size'], columnspacing=1.1, handlelength=1.5)
    save_figure(fig, output, 'tvfr_trajectory_comparison')

    fig, axes = plt.subplots(3, 2, figsize=STYLE['transient_figsize'], layout='constrained')
    fig.set_constrained_layout_pads(h_pad=STYLE['transient_padding_inches'],
                                    w_pad=STYLE['transient_padding_inches'],
                                    hspace=STYLE['transient_row_spacing'], wspace=.04)
    colors = [STYLE['before_color'], STYLE['current_color']]
    for col, pair in enumerate(pairs):
        for method, c in enumerate(pair):
            for i, a in enumerate(c['samples']):
                axes[0, col].plot(c['times'], a[:, 4], color=colors[method],
                                 lw=STYLE['velocity_linewidth'], alpha=.7)
                axes[1, col].plot(c['times'], a[:, 3], color=colors[method],
                                 lw=STYLE['velocity_linewidth'], alpha=.7)
            values = [r['excess_lateral_travel_m'] for r in metrics(c)['per_wmr']]
            axes[2, col].bar(np.arange(7)+(method-.5)*.36, values, width=.36,
                            color=colors[method])
        axes[0, col].set_title(f'{("3 → 2", "1 → 3")[col]} columns', pad=14)
        for row, label in enumerate(('Lateral velocity [m/s]', 'Forward velocity [m/s]')):
            ax = axes[row, col]
            ax.set(xlabel='Time from transition [s]', ylabel=label,
                   xlim=(-STYLE['pre_seconds'], STYLE['post_seconds']))
            ax.axvline(0, color=STYLE['transition_marker_color'], ls='--', lw=1)
            ax.grid(alpha=.17)
        axes[2, col].set(xlabel='WMR index', ylabel='Excess lateral travel [m]', xticks=range(7))
        axes[2, col].grid(axis='y', alpha=.17)
    # Shared ranges prevent visual exaggeration across transitions.
    for row in range(3):
        lo = min(ax.get_ylim()[0] for ax in axes[row])
        hi = max(ax.get_ylim()[1] for ax in axes[row])
        for ax in axes[row]:
            ax.set_ylim(lo, hi+(hi-lo)*STYLE['transient_top_headroom'][row])
    # Match both endpoints' axes-relative y coordinate: a horizontal arrow
    # pointing to the event line, in the headroom above the measured curves.
    for ax in axes[0]:
        y = STYLE['transition_label_y_fraction']
        ax.annotate('Transition start', xy=(0., y),
                    xytext=(STYLE['transition_label_x_seconds'], y),
                    xycoords=ax.get_xaxis_transform(),
                    textcoords=ax.get_xaxis_transform(),
                    ha='left', va='center', fontsize=STYLE['font_size'],
                    color='#3c4650', annotation_clip=True,
                    arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0',
                                    color=STYLE['transition_marker_color'], lw=1.2,
                                    relpos=(0., .5), shrinkA=4, shrinkB=0))
    handles = [Line2D([], [], color=c, lw=3, label=l) for c, l in zip(colors, LABELS)]
    bar_handles = [Patch(facecolor=c, label=l) for c, l in zip(colors, LABELS)]
    for row in range(3):
        for ax in axes[row]:
            ax.legend(handles=bar_handles if row == 2 else handles,
                      loc=STYLE['transient_legend_locations'][row],
                      bbox_to_anchor=STYLE['transient_legend_anchors'][row],
                      fontsize=STYLE['transient_legend_font_size'], framealpha=.95,
                      borderaxespad=0)
    save_figure(fig, output, 'tvfr_transient_comparison')


class PairReplay:
    """Identical scales AND shared moving viewport; no independent camera zoom."""
    def __init__(self, pair, mesh_source):
        self.pair = pair
        self.width = STYLE['width']//2
        self.height = STYLE['scene_height']
        self.sprite, self.extent = video.mesh_sprite(mesh_source, pair[1]['cfg'])
        self.time = np.arange(-STYLE['pre_seconds'], STYLE['post_seconds'], 1/STYLE['fps'])
        self.pose = []
        for c in pair:
            self.pose.append(np.stack([np.column_stack([
                np.interp(self.time, c['times'], a[:, j]) for j in (0, 1, 6)])
                for a in c['samples']]))
        all_xy = np.stack(self.pose)[:, :, :, :2]
        centers = all_xy.mean(axis=(0, 1))
        self.cx = centers[:, 0]-1.0
        self.cy = float((all_xy[..., 1].min()+all_xy[..., 1].max())/2)
        half_x = max(abs(all_xy[..., 0]-self.cx).max()+1.8, 10.)
        half_y = abs(all_xy[..., 1]-self.cy).max()+1.9
        self.scale = min((self.width-24)/(2*half_x), (self.height-24)/(2*half_y))

    def scene(self, method, frame_index):
        c = self.pair[method]
        pose = self.pose[method]
        frame = np.full((self.height, self.width, 3), (250, 252, 250), np.uint8)
        def xy(points):
            return np.rint((np.asarray(points)-[self.cx[frame_index], self.cy])
                          *[self.scale, -self.scale]+[self.width/2, self.height/2]).astype(np.int32)
        for x, y, r in c['geometry']['cylinders']:
            cv2.circle(frame, tuple(xy([x, y])), max(1, round(r*self.scale)), (133, 77, 38), -1, cv2.LINE_AA)
        for x, y, w, h in c['geometry']['boxes']:
            cv2.rectangle(frame, tuple(xy([x-w/2, y+h/2])), tuple(xy([x+w/2, y-h/2])), (133, 77, 38), -1)
        absolute = c['event_time']+self.time[frame_index]
        stage = int(np.searchsorted([e['t'] for e in c['events']], absolute, side='right'))
        assignment = list(range(7)) if stage == 0 else c['events'][stage-1]['assignment'][:7]
        kind = 1 if stage == 0 else c['cfg']['experiment']['stages'][stage-1]['type']
        for a, b in video.formation_edges(kind, assignment):
            cv2.line(frame, tuple(xy(pose[a, frame_index, :2])), tuple(xy(pose[b, frame_index, :2])),
                     (120, 135, 205), 1, cv2.LINE_AA)
        begin = max(0, frame_index-round(STYLE['trail_seconds']*STYLE['fps']))
        for i, p in enumerate(pose):
            color = tuple(int(v) for v in c['cfg']['visualization']['optimal_rgb'][i][::-1])
            points = xy(p[begin:frame_index+1, :2])
            if len(points) > 1:
                cv2.polylines(frame, [points], False, color, STYLE['trail_width_px'], cv2.LINE_AA)
            px, py = xy(p[frame_index, :2])
            size = max(10, round(self.extent*self.scale))
            sprite = cv2.resize(self.sprite, (size, size), interpolation=cv2.INTER_AREA)
            rotation = cv2.getRotationMatrix2D((size/2, size/2), np.degrees(p[frame_index, 2]), 1)
            sprite = cv2.warpAffine(sprite, rotation, (size, size))
            x, y = px-size//2, py-size//2
            x0, y0, x1, y1 = max(0, x), max(0, y), min(self.width, x+size), min(self.height, y+size)
            if x1 > x0 and y1 > y0:
                piece = sprite[y0-y:y1-y, x0-x:x1-x]
                alpha = piece[:, :, 3:4]/255.
                frame[y0:y1, x0:x1] = np.rint(piece[:, :, :3]*alpha+frame[y0:y1, x0:x1]*(1-alpha)).astype(np.uint8)
            video.text(frame, f'WMR {i}', px, max(4, py-size//3-STYLE['wmr_font_px']),
                       STYLE['wmr_font_px'], color, centered=True)
        pixels = round(2*self.scale)
        cv2.line(frame, (25, self.height-28), (25+pixels, self.height-28), (75, 75, 75), 2)
        video.text(frame, '2 m', 25+pixels/2, self.height-56, 22, centered=True)
        cv2.rectangle(frame, (0, 0), (self.width-1, self.height-1), (180, 180, 180), 1)
        return frame

    def draw(self, k, transition):
        top = STYLE['title_height']+STYLE['column_height']
        height = top+self.height+STYLE['footer_height']
        frame = np.full((height, STYLE['width'], 3), 255, np.uint8)
        title = f'{transition} columns — Formation transition comparison'
        video.text(frame, title, STYLE['width']/2, 12, STYLE['title_font_px'], centered=True)
        for m in range(2):
            color = STYLE['before_color'] if m == 0 else STYLE['current_color']
            bgr = tuple(int(color[i:i+2], 16) for i in (5, 3, 1))
            video.text(frame, LABELS[m], (m+.5)*self.width, STYLE['title_height']+5,
                       STYLE['column_font_px'], bgr, centered=True)
            frame[top:top+self.height, m*self.width:(m+1)*self.width] = self.scene(m, k)
        video.text(frame, f'Time from switch: {self.time[k]:+.2f} s   |   1× Playback   |   Identical spatial scale',
                   STYLE['width']/2, top+self.height+8, STYLE['footer_font_px'], centered=True)
        return frame


def find_ffmpeg():
    exe = os.environ.get('FFMPEG_BINARY') or shutil.which('ffmpeg')
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise RuntimeError('Install ffmpeg or imageio-ffmpeg for web-ready H.264 output')


def render_movie(pairs, out, mesh_source, figures):
    ffmpeg = find_ffmpeg()
    count = 0
    with tempfile.TemporaryDirectory(prefix='.comparison_', dir=out) as tmp:
        pending = Path(tmp)/'tvfr_comparison.mp4'
        height = sum(STYLE[k] for k in ('title_height', 'column_height', 'scene_height', 'footer_height'))
        command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
                   '-s', f'{STYLE["width"]}x{height}', '-r', str(STYLE['fps']), '-i', '-', '-an',
                   '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p',
                   '-vf', 'setfield=prog', '-movflags', '+faststart', str(pending)]
        proc = subprocess.Popen(command, stdin=subprocess.PIPE)
        try:
            for pair_index, pair in enumerate(pairs):
                replay = PairReplay(pair, mesh_source)
                for k in range(len(replay.time)):
                    frame = replay.draw(k, ('3 → 2', '1 → 3')[pair_index])
                    proc.stdin.write(frame.tobytes())
                    count += 1
                    if k in (round(8*STYLE['fps']), len(replay.time)-1):
                        if not cv2.imwrite(str(figures/f'tvfr_preview_{pair_index}_{k}.png'), frame):
                            raise RuntimeError('Preview image write failed')
                    if count % 125 == 0:
                        print(f'Encoded {count} frames', flush=True)
        finally:
            proc.stdin.close()
            result = proc.wait()
        if result:
            raise RuntimeError(f'FFmpeg failed: {result}')
        cap = cv2.VideoCapture(str(pending))
        decoded = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame.shape[:2] != (height, STYLE['width']):
                raise RuntimeError('Incorrect decoded dimensions')
            decoded += 1
        cap.release()
        if decoded != count:
            raise RuntimeError(f'Incomplete video: {decoded}/{count}')
        pending.replace(out/'tvfr_comparison.mp4')
    return dict(frames=count, decoded_frames=decoded, fps=STYLE['fps'],
                duration_seconds=count/STYLE['fps'], width=STYLE['width'], height=height,
                scan_type='Progressive', codec='H.264', pixel_format='yuv420p', faststart=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key, name in DEFAULTS.items():
        parser.add_argument('--'+key.replace('_', '-'), type=Path, default=RESULTS/name)
    parser.add_argument('--output', type=Path, default=RESULTS/'comparisons/tvfr_website')
    parser.add_argument('--figures-only', action='store_true')
    parser.add_argument('--require-matched-triggers', action='store_true',
                        help='Reject different all-WMR gates or other switch trigger settings')
    parser.add_argument('--website', type=Path, help='Optionally copy generated media into an existing website (never changes HTML)')
    args = parser.parse_args()
    if args.website and (not (args.website/'index.html').is_file() or args.figures_only):
        parser.error('--website requires an existing index.html and a full render, not --figures-only')
    paths = (args.before_32, args.before_13, args.current)
    run_validation = {str(p.resolve()): verify_completed_run(p) for p in paths}
    out = args.output.resolve()
    if any(out == p.resolve() or out in p.resolve().parents for p in paths):
        parser.error('Output must not overwrite a recorded input directory or its ancestor')
    figures = out/'figures'
    figures.mkdir(parents=True, exist_ok=True)
    plots.STYLE.update(font_size=STYLE['font_size'], legend_font_size=STYLE['legend_font_size'])
    plots.configure_font()
    times = np.linspace(-STYLE['pre_seconds'], STYLE['post_seconds'],
                        round((STYLE['pre_seconds']+STYLE['post_seconds'])/STYLE['sample_seconds'])+1)
    pairs = [(load_clip(old, index, times), load_clip(args.current, index, times))
             for old, index in ((args.before_32, 0), (args.before_13, 3))]
    for pair in pairs:
        verify_matched_environment(pair)
        trigger_audit(pair, args.require_matched_triggers)
        for method, clip in enumerate(pair):
            verify_tvfr_label(clip, enabled=bool(method))
    hashes = {str(p.resolve()): {f: digest(p/f) for f in plots.INPUTS} for p in paths}
    summary = dict(completed=False, description=NOTE, sources=hashes, runtime_validation=run_validation,
                   style=STYLE, transitions=[],
                   shared_display_mesh=dict(source=str(args.current/'mesh_geometry.npz'),
                                            sha256=digest(args.current/'mesh_geometry.npz')))
    for name, pair in zip(('3_to_2', '1_to_3'), pairs):
        entry = dict(transition=name, measurement_window_seconds=[0, STYLE['post_seconds']],
                     display_window_seconds=[-STYLE['pre_seconds'], STYLE['post_seconds']],
                     environment=verify_matched_environment(pair),
                     triggers=trigger_audit(pair, args.require_matched_triggers), methods=[])
        for label, c in zip(LABELS, pair):
            stage = c['cfg']['experiment']['stages'][c['event_index']]
            entry['methods'].append(dict(label=label, source=str(c['source']), metrics=metrics(c),
                                        tvfr=verify_tvfr_label(c, enabled=label == LABELS[1]),
                                        stage_parameters={k: v for k, v in stage.items() if k != 'slots'},
                                        switch_time=c['event_time']))
        summary['transitions'].append(entry)
    with (out/'comparison_samples.csv').open('w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['transition', 'method', 'wmr', 'time_from_switch_s', 'x_m', 'y_m', 'z_m',
                         'vx_mps', 'vy_mps', 'vz_mps', 'yaw_rad'])
        for name, pair in zip(('3_to_2', '1_to_3'), pairs):
            for label, c in zip(LABELS, pair):
                for i, samples in enumerate(c['samples']):
                    writer.writerows([name, label, i, t, *a] for t, a in zip(times, samples))
    render_figures(pairs, figures)
    if not args.figures_only:
        summary['video'] = render_movie(pairs, out, args.current, figures)
        summary['video_sha256'] = digest(out/'tvfr_comparison.mp4')
    for p, inputs in hashes.items():
        if any(digest(Path(p)/f) != h for f, h in inputs.items()):
            raise RuntimeError('Recorded input changed during export')
    summary['code_sha256'] = {p.name: digest(p) for p in (Path(__file__), Path(plots.__file__), Path(video.__file__))}
    summary['completed'] = True
    (out/'comparison_manifest.json').write_text(json.dumps(summary, indent=2)+'\n')
    if args.website:
        assets = {
            figures/'tvfr_trajectory_comparison.png': 'static/images/tvfr_trajectory_comparison.png',
            figures/'tvfr_transient_comparison.png': 'static/images/tvfr_transient_comparison.png',
            figures/f'tvfr_preview_0_{round(8*STYLE["fps"])}.png': 'static/images/tvfr_comparison_poster.png',
            out/'tvfr_comparison.mp4': 'static/videos/tvfr_comparison.mp4',
            out/'comparison_manifest.json': 'static/data/tvfr_comparison_manifest.json',
        }
        for source, relative in assets.items():
            destination = args.website/relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        print(f'WEBSITE MEDIA UPDATED {args.website.resolve()}', flush=True)
    print(f'OUTPUT {out}', flush=True)


if __name__ == '__main__':
    main()
