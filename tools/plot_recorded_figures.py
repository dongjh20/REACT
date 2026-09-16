#!/usr/bin/env python3
"""Standalone three-figure exporter (NumPy + Matplotlib only; no ROS/repo imports).

Inputs: config.json, geometry.json, events.json, odometry.npz.
Outputs: full_trajectory, formation_error_all, all_wmr_speed, each PNG + PDF.
Edit the STYLE dict and the three plot_* functions to customize the figures.
Calendar timestamps are never drawn; time axes are elapsed experiment seconds.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Circle, Rectangle

STYLE = {
    'figure_size': (18, 3.6),  # inches, identical for all three PNG/PDF canvases
    'font_size': 20,          # labels, ticks and stage annotations
    'legend_font_size': 20,   # the only separately configurable font size
    # Main data-curve widths (points), independently configurable per figure.
    'trajectory_linewidth': 1.8,
    'error_linewidth': 1.8,
    'speed_linewidth': 1.8,
    # Axes-relative positions: (0,0) bottom-left, (1,1) top-right.
    # loc selects which corner/edge of the legend is attached to the anchor.
    'speed_legend_loc': 'lower center',
    'speed_legend_anchor': (.5, .035),
    'speed_legend_columns': 8,
    'speed_legend_frame': False,  # False hides the speed legend background/border
    'trajectory_legend_loc': 'lower left',
    'trajectory_legend_anchor': (.012, .015),
    'trajectory_legend_columns': 4,  # two rows for seven WMRs; set to 7 for one row
    'trajectory_legend_row_spacing': .15,
    'trajectory_legend_border_padding': .2,
    'stage_line_spacing': 1.6,  # +0.5 compared with the previous 1.1 line spacing
    'stage_colors': ['#e6eff8', '#e9f4e8', '#fff2db', '#ede9f7', '#e4f3f1'],
    'stage_alpha': .7,
    'stage_label_y': .96,     # axes fraction, inside a reserved band above data
    'error_data_top': .67,    # leave room for two-line stage labels
    'speed_data_top': .76,    # leave room for one-line stage labels
    'obstacle_color': '#264d85',
    'error_color': '#195b96',
}
INPUTS = ('config.json', 'geometry.json', 'events.json', 'odometry.npz')


def configure_font():
    """Real Times New Roman, including PDF embedding; no silent replacement."""
    root = Path(os.environ.get('MLVF_TNR_FONT_DIR', '/mnt/c/Windows/Fonts'))
    for filename, weight, slant in (
            ('times.ttf', 'normal', 'normal'), ('timesbd.ttf', 'bold', 'normal'),
            ('timesi.ttf', 'normal', 'italic'), ('timesbi.ttf', 'bold', 'italic')):
        path = root / filename
        if not path.is_file():
            try:
                path = Path(font_manager.findfont(font_manager.FontProperties(
                    family='Times New Roman', weight=weight, style=slant), fallback_to_default=False))
            except ValueError as exc:
                raise RuntimeError('Install Times New Roman or set MLVF_TNR_FONT_DIR to its licensed font directory') from exc
        if font_manager.FontProperties(fname=str(path)).get_name() != 'Times New Roman':
            raise ValueError(f'Not Times New Roman: {path}')
        font_manager.fontManager.addfont(str(path))
    plt.rcParams.update({'font.family': 'Times New Roman', 'font.size': STYLE['font_size'],
                         'axes.labelsize': STYLE['font_size'], 'xtick.labelsize': STYLE['font_size'],
                         'ytick.labelsize': STYLE['font_size'], 'legend.fontsize': STYLE['legend_font_size'],
                         'pdf.fonttype': 42, 'ps.fonttype': 42,
                         'mathtext.fontset': 'custom', 'mathtext.rm': 'Times New Roman',
                         'mathtext.it': 'Times New Roman:italic',
                         'mathtext.bf': 'Times New Roman:bold',
                         'mathtext.cal': 'Times New Roman:italic',
                         'mathtext.sf': 'Times New Roman', 'mathtext.tt': 'Times New Roman',
                         'mathtext.fallback': None})


def read_records(directory):
    source = Path(directory).resolve()
    for name in INPUTS:
        if not (source / name).is_file():
            raise FileNotFoundError(source / name)
    cfg = json.loads((source / 'config.json').read_text())
    geometry = json.loads((source / 'geometry.json').read_text())
    events = json.loads((source / 'events.json').read_text())
    if cfg['formation_model']['longitudinal']:
        raise ValueError('This exporter implements the low-speed normalized graph, not the highway longitudinal graph')
    count = int(cfg['global_goal']['formation_size'])
    with np.load(source / 'odometry.npz', allow_pickle=False) as archive:
        cars = [archive[f'car{i}'].copy() for i in range(count)]
    for i, a in enumerate(cars):
        if a.ndim != 2 or a.shape[1] != 10 or len(a) < 2 or not np.isfinite(a).all():
            raise ValueError(f'Invalid/empty odometry for WMR {i}; expected [t,x,y,z,vx,vy,vz,yaw,stage,finished]')
        if np.any(np.diff(a[:, 0]) <= 0):
            raise ValueError(f'Non-increasing odometry timestamps for WMR {i}')
    return source, cfg, geometry, events, cars


def graph_error(positions, desired):
    """Original E=||Lhat(P)-Lhat(R)||_F^2; no square root/weights/N division."""
    def laplacian(p):
        a = np.sum((p[..., :, None, :] - p[..., None, :, :])**2, axis=-1)
        degree = a.sum(axis=-1)
        if np.any(degree <= 1e-9) or not np.isfinite(p).all():
            raise ValueError('Degenerate formation graph')
        return np.eye(p.shape[-2]) - a / np.sqrt(degree[..., :, None] * degree[..., None, :])
    return np.sum((laplacian(positions) - laplacian(desired))**2, axis=(-2, -1))


def formation_series(cfg, events, cars):
    """Same common interval and 0.02 s synchronization as the existing exporter."""
    moving = [a[np.linalg.norm(a[:, 4:7], axis=1) > .03, 0] for a in cars]
    nonempty = [a for a in moving if len(a)]
    if not nonempty:
        raise ValueError('No recorded motion; cannot plot a completed experiment')
    start = max(max(a[0, 0] for a in cars), min(a[0] for a in nonempty) - .5)
    end = min(a[-1, 0] for a in cars)
    if end <= start:
        raise ValueError('No common measured time interval')
    stages = cfg['experiment']['stages']
    if len(events) != len(stages):
        raise ValueError('Incomplete switching data; refusing to label it as a full experiment')
    switches = np.asarray([e['t'] for e in events], dtype=float)
    if not np.isfinite(switches).all() or np.any(np.diff(switches) <= 0):
        raise ValueError('Invalid switch timestamps')
    times = np.unique(np.concatenate((np.arange(start, end, .02), switches, [end])))
    if times[0] < start or times[-1] > end:
        raise ValueError('Switch outside common measured time interval')
    xyz = np.stack([np.column_stack([np.interp(times, a[:, 0], a[:, j])
                                    for j in (1, 2, 3)]) for a in cars], axis=1)
    reference_stage = np.searchsorted(switches, times, side='right')
    count = len(cars)
    targets = [np.array([[cfg['global_goal'][f'relative_pos_{i}'][axis]
                         for axis in 'xyz'] for i in range(count)])]
    for stage, event in zip(stages, events):
        assignment = event['assignment'][:count]
        if (sorted(assignment) != list(range(count)) or
                len(event['assignment']) <= count or event['assignment'][count] != stage['type']):
            raise ValueError('Invalid recorded slot assignment/type')
        slots = np.asarray(stage['slots'], dtype=float)
        if slots.shape != (count, 3):
            raise ValueError('Invalid desired slots')
        targets.append(slots[assignment])
    targets = np.asarray(targets) * cfg['global_goal']['swarm_scale']
    error = graph_error(xyz, targets[reference_stage])
    return times, error, reference_stage, start, end


def plot_full_trajectory(cfg, geometry, cars):
    fig, ax = plt.subplots(figsize=STYLE['figure_size'], layout='constrained')
    color = STYLE['obstacle_color']
    for x, y, radius in geometry['cylinders']:
        ax.add_patch(Circle((x, y), radius, color=color, zorder=2))
    for x, y, width, height in geometry['boxes']:
        ax.add_patch(Rectangle((x-width/2, y-height/2), width, height, color=color, zorder=2))
    colors = np.asarray(cfg['visualization']['optimal_rgb']) / 255.
    for i, a in enumerate(cars):
        ax.plot(a[:, 1], a[:, 2], color=colors[i], lw=STYLE['trajectory_linewidth'], label=f'WMR {i}', zorder=3)
        ax.scatter(*a[0, 1:3], color=colors[i], s=22, marker='o', zorder=4)
        ax.scatter(*a[-1, 1:3], color=colors[i], s=32, marker='s', zorder=4)
    xlo, xhi = min(a[:, 1].min() for a in cars)-.6, max(a[:, 1].max() for a in cars)+.6
    lower = [a[:, 2].min() for a in cars] + [y-r for x, y, r in geometry['cylinders']] + [y-h/2 for x, y, w, h in geometry['boxes']]
    upper = [a[:, 2].max() for a in cars] + [y+r for x, y, r in geometry['cylinders']] + [y+h/2 for x, y, w, h in geometry['boxes']]
    ax.set(xlim=(xlo, xhi), ylim=(min(lower)-.4, max(upper)+.4),
           xlabel='x [m]', ylabel='y [m]')
    ax.grid(alpha=.12)
    ax.legend(loc=STYLE['trajectory_legend_loc'], bbox_to_anchor=STYLE['trajectory_legend_anchor'],
              ncol=min(len(cars), STYLE['trajectory_legend_columns']),
              fontsize=STYLE['legend_font_size'], framealpha=.95,
              borderaxespad=0, columnspacing=1., handlelength=1.7,
              labelspacing=STYLE['trajectory_legend_row_spacing'],
              borderpad=STYLE['trajectory_legend_border_padding'])
    # Fit equal metric scales to the full layout box instead of shrinking the
    # axes vertically. Expand limits only; never stretch or crop recorded geometry.
    fig.canvas.draw()
    ratio = ax.bbox.height / ax.bbox.width
    ylo, yhi = ax.get_ylim()
    if (xhi-xlo)*ratio >= yhi-ylo:
        half = (xhi-xlo)*ratio/2
        middle = (ylo+yhi)/2
        ax.set_ylim(middle-half, middle+half)
    else:
        half = (yhi-ylo)/ratio/2
        middle = (xlo+xhi)/2
        ax.set_xlim(middle-half, middle+half)
    ax.set_aspect('equal', adjustable='datalim')
    return fig


def draw_stages(ax, cfg, events, start, end, with_columns=False):
    boundaries = np.r_[start, [e['t'] for e in events], end] - start
    columns = [3] + [{1: 3, 2: 2, 3: 1}[s['type']] for s in cfg['experiment']['stages']]
    for k, count in enumerate(columns):
        ax.axvspan(boundaries[k], boundaries[k+1], color=STYLE['stage_colors'][k % len(STYLE['stage_colors'])],
                   alpha=STYLE['stage_alpha'], zorder=0)
        label = f'Stage {k+1}'
        if with_columns:
            label += f':\n{count} column' + ('s' if count != 1 else '')
        ax.text((boundaries[k]+boundaries[k+1])/2, STYLE['stage_label_y'], label,
                transform=ax.get_xaxis_transform(), ha='center', va='top',
                fontsize=STYLE['font_size'], linespacing=STYLE['stage_line_spacing'], zorder=4)
    for event in events:
        ax.axvline(event['t']-start, color='#bb6620', ls='--', lw=.8, zorder=1)


def plot_formation_error(cfg, events, series):
    times, error, stage, start, end = series
    fig, ax = plt.subplots(figsize=STYLE['figure_size'], layout='constrained')
    draw_stages(ax, cfg, events, start, end, with_columns=True)
    for k in range(len(events)+1):
        selected = stage == k
        # Break at reference jumps, rather than smoothing/interpolating across switches.
        ax.plot(times[selected]-start, error[selected], color=STYLE['error_color'], lw=STYLE['error_linewidth'])
    ax.set(xlim=(0, end-start), ylim=(0, max(float(error.max())/STYLE['error_data_top'], 1e-9)),
           xlabel='Time [s]', ylabel=r'Formation Error $\,~f_\mathrm{e}$')
    ax.grid(axis='y', alpha=.25)
    return fig


def plot_robot_speed(cfg, events, cars, series):
    _, _, _, start, end = series
    fig, ax = plt.subplots(figsize=STYLE['figure_size'], layout='constrained')
    colors = np.asarray(cfg['visualization']['optimal_rgb']) / 255.
    peak = float(cfg['motion']['max_vel'])
    for i, a in enumerate(cars):
        a = a[(a[:, 0] >= start) & (a[:, 0] <= end)]
        speed = np.linalg.norm(a[:, 4:7], axis=1)
        peak = max(peak, float(speed.max()))
        ax.plot(a[:, 0]-start, speed,
                color=colors[i], lw=STYLE['speed_linewidth'], label=f'WMR {i}')
    reference = cfg['motion']['max_vel']
    ax.axhline(reference, color='#333333', ls='--', lw=1, label=f'Desired Speed {reference:g}m/s')
    draw_stages(ax, cfg, events, start, end)
    ax.set(xlim=(0, end-start), ylim=(0, max(peak/STYLE['speed_data_top'], 1e-9)),
           xlabel='Time [s]', ylabel='Speed [m/s]')
    ax.grid(alpha=.2)
    legend = ax.legend(loc=STYLE['speed_legend_loc'], bbox_to_anchor=STYLE['speed_legend_anchor'],
              ncol=min(len(cars)+1,STYLE['speed_legend_columns']), fontsize=STYLE['legend_font_size'],
              borderaxespad=0, frameon=STYLE['speed_legend_frame'],
              facecolor='white', edgecolor='#555555', framealpha=.95,
              columnspacing=1., handlelength=1.7)
    # This legend sits inside the axes; do not let its initial width reserve
    # external margins on the first constrained-layout pass (notably at large fonts).
    legend.set_in_layout(False)
    return fig


def render_three(directory, output=None, overwrite=True, dpi=180):
    if dpi <= 0:
        raise ValueError('DPI must be positive')
    source, cfg, geometry, events, cars = read_records(directory)
    series = formation_series(cfg, events, cars)
    configure_font()
    destination = Path(output).resolve() if output else source / 'figures' / 'three_plots'
    if destination == source:
        raise ValueError('Choose a figure output directory, not the raw recording directory')
    destination.mkdir(parents=True, exist_ok=overwrite)
    hashes = {name: hashlib.sha256((source/name).read_bytes()).hexdigest() for name in INPUTS}
    manifest = {'source': str(source), 'output': str(destination), 'completed': False,
                'input_sha256': hashes, 'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'font': 'Times New Roman', 'calendar_timestamp_drawn': False,
                'style': STYLE, 'overwrite': overwrite,
                'formation_error': '||Lhat(actual)-Lhat(desired)||_F^2; all-pair squared-distance adjacency; no sqrt/weights/N division',
                'speed': 'norm(odometry vx,vy,vz), not position differences or desired speed',
                'start_ros_time': series[3], 'end_ros_time': series[4],
                'formation_samples': len(series[0]), 'odom_samples': [len(a) for a in cars],
                'outputs': []}
    try:
        figures = [('full_trajectory', plot_full_trajectory(cfg, geometry, cars)),
                   ('formation_error_all', plot_formation_error(cfg, events, series)),
                   ('all_wmr_speed', plot_robot_speed(cfg, events, cars, series))]
        for name, fig in figures:
            for suffix in ('png', 'pdf'):
                path = destination / f'{name}.{suffix}'
                fig.savefig(path, dpi=dpi)
                manifest['outputs'].append(path.name)
            plt.close(fig)
        for name, digest in hashes.items():
            if hashlib.sha256((source/name).read_bytes()).hexdigest() != digest:
                raise RuntimeError(f'Recorded input changed during export: {name}')
        manifest['completed'] = True
    finally:
        plt.close('all')
        (destination/'plot_manifest.json').write_text(json.dumps(manifest, indent=2))
    print(f'FIGURES OUTPUT {destination}', flush=True)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='Recorded experiment directory')
    parser.add_argument('--output', type=Path, help='Figure output directory; default <run>/figures/three_plots')
    overwrite_group = parser.add_mutually_exclusive_group()
    overwrite_group.add_argument('--overwrite', dest='overwrite', action='store_true', help='Overwrite the three figures (default)')
    overwrite_group.add_argument('--no-overwrite', dest='overwrite', action='store_false', help='Refuse an existing output directory')
    parser.set_defaults(overwrite=True)
    parser.add_argument('--dpi', type=int, default=180)
    args = parser.parse_args()
    render_three(args.directory, args.output, args.overwrite, args.dpi)
