#!/usr/bin/env python3
"""Offline four-panel replay. Keep plot_recorded_figures.py beside this file.

No ROS, previous video, compiler or simulator required. Inputs are measured
odometry, configuration, geometry, switches, report and archived mesh triangles.
Only output artifacts are replaced; recorded inputs are never changed.
"""
import argparse
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import plot_recorded_figures as plots

# All four independent font sizes are here. Overlay sizes are pixels; chart
# fonts are points (20 pt = 27.8 px at the default 100 dpi).
VIDEO_STYLE = {
    'title_font_px': 36,
    'panel_title_font_px': 32,
    'playback_text_color_bgr': (55, 55, 145),  # dark red, bold 1x text only
    'chart_font_pt': 20,
    'wmr_font_px': 24,
    'show_planned_trajectories': True,   # (b): active polynomial's remaining path
    'planned_linewidth_px': 2,
    'planned_alpha': .8,
    'planned_sample_period_s': .05,
    'planned_visible_fraction': 2/3,  # fraction of remaining polynomial TIME, display only
    'planned_dash_px': 9,
    'planned_gap_px': 6,
    'cursor_color_bgr': (125, 100, 75),  # muted slate blue; OpenCV order is blue, green, red
    'cursor_linewidth_px': 2,            # shared moving cursor width for (c) and (d)
    'width': 1800,
    'title_height': 64,
    'panel_title_height': 40,
    'route_height': 280,
    'tracking_height': 420,
    'chart_height': 360,
    'fps': 25,
    'tracking_history_seconds': 6.,
    'tracking_min_span_m': 18.,
    'trail_width_px': 2,
    'edge_width_px': 1,
}
TITLE = 'Five-stage continuous formation navigation experiment'
PANEL_TITLES = ('(a) Full route', '(b) Tracking view (1× Playback)',
                '(c) Formation error', '(d) Speed')


@lru_cache(maxsize=32)
def video_font(size, bold=False):
    path = plots.font_manager.findfont(plots.font_manager.FontProperties(
        family='Times New Roman', weight='bold' if bold else 'normal'), fallback_to_default=False)
    return ImageFont.truetype(path, size)


@lru_cache(maxsize=128)
def text_mask(text, size):
    font = video_font(size)
    x0, y0, x1, y1 = font.getbbox(text)
    mask = Image.new('L', (max(1, x1-x0), max(1, y1-y0)))
    ImageDraw.Draw(mask).text((-x0, -y0), text, font=font, fill=255)
    return np.asarray(mask, dtype=np.float32)[..., None]/255


def text(frame, label, x, y, size, color=(35, 35, 35), centered=False):
    alpha = text_mask(label, size)
    h, w = alpha.shape[:2]
    x = round(x-w/2) if centered else round(x)
    y = round(y)
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(frame.shape[1], x+w), min(frame.shape[0], y+h)
    if x1 > x0 and y1 > y0:
        a = alpha[y0-y:y1-y, x0-x:x1-x]
        roi = frame[y0:y1, x0:x1]
        roi[:] = np.rint(roi*(1-a)+np.asarray(color)*a).astype(np.uint8)


def tracking_title(frame, top):
    """Mixed-weight title on a shared baseline; preserve natural space advances."""
    height = VIDEO_STYLE['panel_title_height']
    region = Image.fromarray(cv2.cvtColor(frame[top:top+height],cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(region)
    size = VIDEO_STYLE['panel_title_font_px']
    regular, bold = video_font(size), video_font(size,True)
    ascent, descent = regular.getmetrics()
    baseline = (height+ascent-descent)/2
    x = 16.
    for label,font,color in [('(b) Tracking view (',regular,(35,35,35)),
                             ('1× Playback',bold,VIDEO_STYLE['playback_text_color_bgr'][::-1]),
                             (')',regular,(35,35,35))]:
        draw.text((x,baseline),label,font=font,fill=tuple(color),anchor='ls')
        x += font.getlength(label)
    frame[top:top+height] = cv2.cvtColor(np.asarray(region),cv2.COLOR_RGB2BGR)


class CurvePanel:
    """Reuse final plot style exactly; reveal data only to the current time.

    Capture one static background and one final plot using the same fixed
    layout. A moving reveal boundary exposes curves without per-frame layout,
    smoothing, or interpolation across formation-reference discontinuities.
    """
    def __init__(self, fig, data_lines):
        width, height = VIDEO_STYLE['width'], VIDEO_STYLE['chart_height']
        fig.set_size_inches(width/100, height/100)
        fig.set_dpi(100)
        fig.canvas.draw()
        fig.canvas.draw()  # settle constrained layout before freezing transforms
        fig.set_constrained_layout(False)
        self.ax = fig.axes[0]
        self.transform = self.ax.transData.frozen()
        self.full = cv2.cvtColor(np.asarray(fig.canvas.buffer_rgba()), cv2.COLOR_RGBA2BGR)
        for line in data_lines:
            line.set_visible(False)
        fig.canvas.draw()
        self.background = cv2.cvtColor(np.asarray(fig.canvas.buffer_rgba()), cv2.COLOR_RGBA2BGR)
        self.left = math.ceil(self.ax.bbox.x0)
        self.right = math.floor(self.ax.bbox.x1)
        self.top = round(height-self.ax.bbox.y1)
        self.bottom = round(height-self.ax.bbox.y0)
        self.height = height
        self.end = self.ax.get_xlim()[1]
        plots.plt.close(fig)

    def draw(self, elapsed):
        panel = self.background.copy()
        # Exclude pixels to the right of the current time, including future data.
        cursor = int(np.floor(self.transform.transform((elapsed, 0))[0]))
        cursor = max(self.left, min(self.right, cursor))
        panel[:, self.left:cursor] = self.full[:, self.left:cursor]
        # Span the entire black axes box, including stage labels and legend.
        for y in range(self.top, self.bottom+1, 10):
            cv2.line(panel, (cursor, y), (cursor, min(y+4, self.bottom)),
                     VIDEO_STYLE['cursor_color_bgr'], VIDEO_STYLE['cursor_linewidth_px'], cv2.LINE_AA)
        cv2.line(panel, (cursor, max(self.top, self.bottom-2)), (cursor, self.bottom),
                 VIDEO_STYLE['cursor_color_bgr'], VIDEO_STYLE['cursor_linewidth_px'], cv2.LINE_AA)
        return panel


def formation_edges(kind, assignment):
    """Same slot adjacency as formation_visual_edges.py / RViz (display only)."""
    if sorted(assignment) != list(range(7)):
        raise ValueError('Expected seven assigned WMR slots')
    inverse = [assignment.index(i) for i in range(7)]
    if kind == 3:
        edges = [(0,1), (1,2), (2,3), (3,4), (4,5), (5,6)]
    elif kind == 2:
        edges = [(0,1), (1,2), (2,3), (6,5), (5,4),
                 (0,6), (6,1), (1,5), (5,2), (2,4), (4,3)]
    else:
        edges = [(0,1), (0,2), (0,3), (0,4), (0,5), (0,6),
                 (1,2), (2,3), (3,4), (4,5), (5,6), (6,1)]
    return [(inverse[a], inverse[b]) for a,b in edges]


def mesh_sprite(source, cfg):
    with np.load(source/'mesh_geometry.npz', allow_pickle=False) as archive:
        triangles = archive['triangles'].copy()
    if triangles.ndim != 3 or triangles.shape[1:] != (3, 3) or not np.isfinite(triangles).all():
        raise ValueError('Invalid archived mesh triangles')
    extent = float(np.max(np.linalg.norm(triangles[:, :, :2], axis=2))*2.15)
    if extent <= 0:
        raise ValueError('Empty mesh extent')
    sprite = np.zeros((400, 400, 4), np.uint8)
    light = np.array([-.3, -.4, 1.]); light /= np.linalg.norm(light)
    base = np.asarray(cfg['visualization']['mesh_rgba'][:3][::-1])*255
    for tri in triangles[np.argsort(triangles[:, :, 2].mean(axis=1))]:
        normal = np.cross(tri[1]-tri[0], tri[2]-tri[0]); norm = np.linalg.norm(normal)
        if norm < 1e-12:
            continue
        shade = .5+.5*max(0, float(np.dot(normal/norm, light)))
        points = np.column_stack((tri[:, 0]/extent*400+200, 200-tri[:, 1]/extent*400)).astype(np.int32)
        cv2.fillConvexPoly(sprite, points, tuple(int(v) for v in base*shade)+(255,), cv2.LINE_AA)
    return sprite, extent


class RecordedPlans:
    """Select actual executed IDs, not the newest published/pending candidate."""
    def __init__(self, source, cars):
        self.plans = {}
        for raw in json.loads((source/'trajectories.json').read_text()):
            key = (int(raw['wmr']), int(raw['id']))
            duration = np.asarray(raw['duration'], dtype=float)
            coef = np.asarray(raw['coef'], dtype=float)
            if (key in self.plans or key[0] not in range(len(cars)) or duration.ndim != 1
                    or not len(duration) or np.any(duration <= 0)
                    or coef.shape != (3, 6*len(duration))
                    or not np.isfinite(duration).all() or not np.isfinite(coef).all()
                    or not np.isfinite([raw['start'], raw['received']]).all()):
                raise ValueError(f'Invalid/duplicate recorded polynomial {key}')
            self.plans[key] = {**raw, 'ends': np.cumsum(duration),
                               'coefficients': coef.reshape(3, -1, 6)}
        with np.load(source/'commands.npz', allow_pickle=False) as archive:
            self.commands = [archive[f'car{i}'].copy() for i in range(len(cars))]
        self.finish = []
        for i,(a,commands) in enumerate(zip(cars,self.commands)):
            if (commands.ndim != 2 or commands.shape[1] != 5 or len(commands) == 0
                    or not np.isfinite(commands).all() or np.any(np.diff(commands[:,0]) <= 0)
                    or np.any(commands[:,4] != commands[:,4].astype(int))):
                raise ValueError(f'Invalid command record for WMR {i}')
            for ident in np.unique(commands[:,4]).astype(int):
                if (i,ident) not in self.plans:
                    raise ValueError(f'Missing executed polynomial for WMR {i}, ID {ident}')
            finished = a[a[:,9] != 0,0]
            self.finish.append(float(finished[0]) if len(finished) else float('inf'))

    @staticmethod
    def evaluate(plan, local_times, derivative=0):
        times = np.clip(np.asarray(local_times,dtype=float),0,plan['ends'][-1])
        piece = np.minimum(np.searchsorted(plan['ends'],times,side='right'),len(plan['ends'])-1)
        local = times-np.r_[0,plan['ends'][:-1]][piece]
        coef = plan['coefficients'][:,piece,:]
        for _ in range(derivative):
            coef = coef[...,:-1]*np.arange(coef.shape[-1]-1,0,-1)
        result = np.zeros((3,len(times)))
        for column in range(coef.shape[-1]):
            result = result*local+coef[:,:,column]
        return result.T

    def active(self, i, t):
        if t >= self.finish[i]:
            return None
        commands = self.commands[i]
        j = np.searchsorted(commands[:,0],t,side='right')-1
        if j < 0:
            return None
        plan = self.plans[(i,int(commands[j,4]))]
        # Observer receipt is conservative: never reveal a plan not yet recorded.
        if t < max(plan['start'],plan['received']) or t >= plan['start']+plan['ends'][-1]:
            return None
        return plan

    def remaining(self, i, t):
        plan = self.active(i,t)
        if plan is None:
            return None
        start = t-plan['start']; end = plan['ends'][-1]
        fraction = VIDEO_STYLE['planned_visible_fraction']
        if not 0 < fraction <= 1:
            raise ValueError('planned_visible_fraction must be in (0,1]')
        end = start+(end-start)*fraction
        step = VIDEO_STYLE['planned_sample_period_s']
        if step <= 0:
            raise ValueError('planned_sample_period_s must be positive')
        # Include only boundaries in the visible time interval and its endpoint.
        times = np.unique(np.r_[np.arange(start,end,step),
                                plan['ends'][(plan['ends']>start)&(plan['ends']<end)], end])
        return self.evaluate(plan,times)[:,:2]

    def audit(self):
        rows = []
        for i,commands in enumerate(self.commands):
            errors = []; receipt_late = 0
            for ident in np.unique(commands[:,4]).astype(int):
                plan = self.plans[(i,ident)]
                samples = commands[(commands[:,4]==ident)&(commands[:,0]<self.finish[i])]
                local = samples[:,0]-plan['start']
                selected = (local>=0)&(local<plan['ends'][-1])
                # Finishing may emit a zero-velocity hold before the observer's
                # finish flag; audit moving commands only, report count explicitly.
                selected &= np.linalg.norm(samples[:,1:4],axis=1)>1e-7
                samples = samples[selected]; local = local[selected]
                if len(samples):
                    expected = self.evaluate(plan,local,derivative=1)
                    errors.extend(np.linalg.norm(expected-samples[:,1:4],axis=1))
                    receipt_late += int(np.count_nonzero(samples[:,0]<plan['received']))
            if not errors:
                raise ValueError(f'No moving command samples to validate WMR {i}')
            maximum = float(max(errors))
            if maximum > 1e-4:
                raise ValueError(f'Polynomial/command velocity mismatch for WMR {i}: {maximum}')
            rows.append({'wmr':i,'plans':sum(key[0]==i for key in self.plans),
                         'moving_commands_checked':len(errors),'max_velocity_error_m_s':maximum,
                         'commands_before_observer_plan_receipt':receipt_late})
        return {'passed':True,'per_wmr':rows,
                'selection':'latest command stamp <= frame time; matching WMR and trajectory ID; start/observer receipt gates; hide after recorded finish or expiry',
                'scope':'Published successful plans only; pending plans and failed optimizer iterations are not displayed. Observer callback latency is not reconstructed.'}


def dashed_polyline(image, points, color, width):
    """Uniform pixel-length dashes, independent of polynomial sampling density."""
    dash, gap = VIDEO_STYLE['planned_dash_px'], VIDEO_STYLE['planned_gap_px']
    if dash <= 0 or gap <= 0 or width <= 0:
        raise ValueError('Planned dash, gap and linewidth must be positive')
    phase = 0.
    for a,b in zip(points[:-1],points[1:]):
        delta = b-a; length = float(np.linalg.norm(delta))
        offset = 0.
        while offset < length-1e-9:
            drawing = phase < dash
            take = min((dash if drawing else dash+gap)-phase,length-offset)
            if drawing:
                p = np.rint(a+delta*offset/length).astype(int)
                q = np.rint(a+delta*(offset+take)/length).astype(int)
                cv2.line(image,tuple(p),tuple(q),color,width,cv2.LINE_AA)
            offset += take
            phase = (phase+take)%(dash+gap)


class Replay:
    def __init__(self, source, cfg, geometry, events, cars, series, fps):
        self.cfg, self.geometry, self.events = cfg, geometry, events
        self.start, self.end = series[3:5]
        self.fps = fps
        self.elapsed = np.arange(0, self.end-self.start, 1/fps)
        self.times = self.start+self.elapsed
        # Use measured positions, interpolate only for the display sampling rate.
        # Unwrap yaw before interpolation so +/-pi does not cause a false rotation.
        self.pose = np.asarray([np.column_stack([
            np.interp(self.times, a[:, 0], a[:, 1]),
            np.interp(self.times, a[:, 0], a[:, 2]),
            np.interp(self.times, a[:, 0], np.unwrap(a[:, 7]))]) for a in cars])
        self.stage = np.searchsorted([e['t'] for e in events], self.times, side='right')
        self.colors = [tuple(int(v) for v in rgb[::-1]) for rgb in cfg['visualization']['optimal_rgb']]
        self.sprite, self.extent = mesh_sprite(source, cfg)
        self.plans = RecordedPlans(source,cars) if VIDEO_STYLE['show_planned_trajectories'] else None
        self.plan_audit = self.plans.audit() if self.plans else None
        self.xlo = min(a[:, 1].min() for a in cars)-1
        self.xhi = max(a[:, 1].max() for a in cars)+1
        lower = [a[:, 2].min() for a in cars]+[y-r for x,y,r in geometry['cylinders']]+[y-h/2 for x,y,w,h in geometry['boxes']]
        upper = [a[:, 2].max() for a in cars]+[y+r for x,y,r in geometry['cylinders']]+[y+h/2 for x,y,w,h in geometry['boxes']]
        self.ylo, self.yhi = min(lower)-.5, max(upper)+.5
        error_fig = plots.plot_formation_error(cfg, events, series)
        self.error = CurvePanel(error_fig, list(error_fig.axes[0].lines[len(events):]))
        speed_fig = plots.plot_robot_speed(cfg, events, cars, series)
        self.speed = CurvePanel(speed_fig, list(speed_fig.axes[0].lines[:len(cars)]))
        heights = [VIDEO_STYLE['route_height'], VIDEO_STYLE['tracking_height'],
                   VIDEO_STYLE['chart_height'], VIDEO_STYLE['chart_height']]
        self.height = VIDEO_STYLE['title_height']+sum(heights)+4*VIDEO_STYLE['panel_title_height']
        self.width = VIDEO_STYLE['width']
        if self.width % 2 or self.height % 2:
            raise ValueError('H.264 width and total height must be even')
        self.template = np.full((self.height, self.width, 3), 255, np.uint8)
        title_mask = text_mask(TITLE, VIDEO_STYLE['title_font_px'])
        if title_mask.shape[1] > self.width-32 or title_mask.shape[0] > VIDEO_STYLE['title_height']:
            raise ValueError('Main title does not fit; reduce title_font_px or enlarge title area')
        text(self.template, TITLE, self.width/2, (VIDEO_STYLE['title_height']-title_mask.shape[0])/2,
             VIDEO_STYLE['title_font_px'], centered=True)
        self.panels = []
        top = VIDEO_STYLE['title_height']
        for label, height in zip(PANEL_TITLES, heights):
            if label == PANEL_TITLES[3]:
                # Same neutral-gray, one-pixel rule as the full-route border.
                cv2.line(self.template, (0, top), (self.width-1, top), (180,180,180), 1)
            if label == PANEL_TITLES[1]:
                tracking_title(self.template,top)
            else:
                text(self.template, label, 16, top+7, VIDEO_STYLE['panel_title_font_px'])
            top += VIDEO_STYLE['panel_title_height']
            self.panels.append((top, height))
            top += height

    def scene(self, k, tracking=False):
        width = self.width
        height = VIDEO_STYLE['tracking_height' if tracking else 'route_height']
        image = np.full((height, width, 3), 250, np.uint8)
        positions = self.pose[:, k, :2]
        if tracking:
            cx, cy = positions.mean(axis=0)
            span = max(VIDEO_STYLE['tracking_min_span_m'], float(np.ptp(positions[:, 0]))+6,
                       (float(np.ptp(positions[:, 1]))+3.5)*width/height)
            scale = width/span
        else:
            cx, cy = (self.xlo+self.xhi)/2, (self.ylo+self.yhi)/2
            scale = min((width-12)/(self.xhi-self.xlo), (height-12)/(self.yhi-self.ylo))
        def xy(points):
            points = np.asarray(points)
            return np.rint((points-[cx, cy])*[scale, -scale]+[width/2, height/2]).astype(np.int32)
        for x,y,r in self.geometry['cylinders']:
            center = xy([x,y]); radius = max(1, round(r*scale))
            if -radius < center[0] < width+radius and -radius < center[1] < height+radius:
                cv2.circle(image, tuple(center), radius, (133,77,38), -1, cv2.LINE_AA)
        for x,y,w,h in self.geometry['boxes']:
            cv2.rectangle(image, tuple(xy([x-w/2,y+h/2])), tuple(xy([x+w/2,y-h/2])), (133,77,38), -1)
        stage = self.stage[k]
        if tracking:
            assignment = list(range(7)) if stage == 0 else self.events[stage-1]['assignment'][:7]
            kind = 1 if stage == 0 else self.cfg['experiment']['stages'][stage-1]['type']
            for a,b in formation_edges(kind, assignment):
                cv2.line(image, tuple(xy(positions[a])), tuple(xy(positions[b])),
                         (90,90,255), VIDEO_STYLE['edge_width_px'], cv2.LINE_AA)
        begin = max(0, k-round(VIDEO_STYLE['tracking_history_seconds']*self.fps)) if tracking else 0
        if tracking and self.plans:
            overlay = image.copy()
            for i in range(7):
                path = self.plans.remaining(i,self.times[k])
                if path is not None and len(path)>1:
                    dashed_polyline(overlay,xy(path),self.colors[i],VIDEO_STYLE['planned_linewidth_px'])
            alpha = VIDEO_STYLE['planned_alpha']
            if not 0 <= alpha <= 1:
                raise ValueError('planned_alpha must be in [0,1]')
            cv2.addWeighted(overlay,alpha,image,1-alpha,0,dst=image)
        labels = []
        for i in range(7):
            points = xy(self.pose[i, begin:k+1, :2])
            if len(points) > 1:
                cv2.polylines(image, [points], False, self.colors[i], VIDEO_STYLE['trail_width_px'], cv2.LINE_AA)
            px, py = xy(positions[i])
            if not tracking:
                cv2.circle(image, (px, py), 4, self.colors[i], -1, cv2.LINE_AA)
                continue
            size = max(10, round(self.extent*scale))
            small = cv2.resize(self.sprite, (size,size), interpolation=cv2.INTER_AREA)
            rotation = cv2.getRotationMatrix2D((size/2,size/2), np.degrees(self.pose[i,k,2]), 1)
            rotated = cv2.warpAffine(small, rotation, (size,size))
            x, y = px-size//2, py-size//2
            x0,y0,x1,y1 = max(0,x),max(0,y),min(width,x+size),min(height,y+size)
            if x1>x0 and y1>y0:
                part = rotated[y0-y:y1-y,x0-x:x1-x]
                alpha = part[:,:,3:4]/255.
                roi = image[y0:y1,x0:x1]
                roi[:] = np.rint(part[:,:,:3]*alpha+roi*(1-alpha)).astype(np.uint8)
            labels.append((i, px, py-size//3-VIDEO_STYLE['wmr_font_px']))
        for i,px,py in labels:
            text(image, f'WMR {i}', px, max(2,py), VIDEO_STYLE['wmr_font_px'], self.colors[i], centered=True)
        if tracking and self.plans:
            cv2.line(image,(16,20),(60,20),(90,90,90),VIDEO_STYLE['trail_width_px'],cv2.LINE_AA)
            text(image,'Executed',70,7,VIDEO_STYLE['wmr_font_px'])
            dashed_polyline(image,np.array([[190,20],[234,20]]),(90,90,90),VIDEO_STYLE['planned_linewidth_px'])
            text(image,'Planned',244,7,VIDEO_STYLE['wmr_font_px'])
        cv2.rectangle(image, (0,0), (width-1,height-1), (180,180,180), 1)
        return image

    def draw(self, k):
        frame = self.template.copy()
        for (top,height), panel in zip(self.panels, [self.scene(k), self.scene(k, True),
                self.error.draw(self.elapsed[k]), self.speed.draw(self.elapsed[k])]):
            frame[top:top+height] = panel
        return frame


def render(directory, output=None, fps=None, preview_only=False):
    fps = VIDEO_STYLE['fps'] if fps is None else fps
    if fps <= 0:
        raise ValueError('FPS must be positive')
    source, cfg, geometry, events, cars = plots.read_records(directory)
    if len(cars) != 7 or len(events) != 4:
        raise ValueError('Expected the seven-WMR five-stage experiment')
    report = json.loads((source/'report.json').read_text())
    if not report.get('passed'):
        raise ValueError('Refusing to present a failed run as a completed experiment')
    inputs = (*plots.INPUTS, 'report.json', 'mesh_geometry.npz')
    if VIDEO_STYLE['show_planned_trajectories']:
        inputs += ('trajectories.json','commands.npz')
    hashes = {name: hashlib.sha256((source/name).read_bytes()).hexdigest() for name in inputs}
    series = plots.formation_series(cfg, events, cars)
    # Video-only override; never edits the standalone plotting script or data.
    plots.STYLE.update(font_size=VIDEO_STYLE['chart_font_pt'], legend_font_size=VIDEO_STYLE['chart_font_pt'])
    plots.configure_font()
    replay = Replay(source, cfg, geometry, events, cars, series, fps)
    out = Path(output).resolve() if output else source/'exports/four_panel_video'
    if out == source:
        raise ValueError('Use a dedicated output directory, not the raw recording directory')
    out.mkdir(parents=True, exist_ok=True)
    figures = out/'figures'; figures.mkdir(exist_ok=True)
    count = len(replay.times)
    keys = {0, count//4, count//2, 3*count//4, count-1}
    keys.update(int(np.argmin(abs(replay.times-event['t']))) for event in events)
    manifest = {'completed': False, 'preview_only': preview_only, 'source': str(source),
                'input_sha256': hashes, 'video_style': VIDEO_STYLE, 'chart_style': plots.STYLE,
                'title': TITLE, 'panel_titles': PANEL_TITLES, 'font': 'Times New Roman',
                'fps': fps, 'frames': count, 'width': replay.width, 'height': replay.height,
                'start_ros_time': replay.start, 'end_ros_time': replay.end,
                'playback_rate': 1, 'duration_seconds': count/fps,
                'future_curves_visible': False, 'speed_definition': 'norm of measured odometry velocity',
                'planned_trajectory_audit': replay.plan_audit,
                'formation_definition': 'same graph error and reference assignments as plot_recorded_figures.py',
                'kind': 'offline measured-odometry replay; not RViz screen capture; no ROS rerun',
                'code_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in (Path(__file__), Path(plots.__file__))}}
    manifest_path = out/('preview_manifest.json' if preview_only else 'video_manifest.json')
    manifest_path.write_text(json.dumps(manifest, indent=2))
    target = out/'formation_navigation_four_panels.mp4'
    try:
        if preview_only:
            for k in sorted(keys):
                if not cv2.imwrite(str(figures/f'preview_{k:05d}.png'), replay.draw(k)):
                    raise RuntimeError('Cannot save preview')
        else:
            # A failed/interrupted encode cannot replace a previous good video.
            with tempfile.TemporaryDirectory(prefix='.video_pending_', dir=out) as temp:
                pending = Path(temp)/target.name
                writer = cv2.VideoWriter(str(pending), cv2.VideoWriter_fourcc(*'avc1'), fps,
                                         (replay.width,replay.height))
                if not writer.isOpened():
                    raise RuntimeError('OpenCV H.264 encoder unavailable')
                try:
                    for k in range(count):
                        frame = replay.draw(k)
                        writer.write(frame)
                        if k in keys:
                            if not cv2.imwrite(str(figures/f'preview_{k:05d}.png'), frame):
                                raise RuntimeError('Cannot save preview')
                        if k % 250 == 0:
                            print(f'Rendered {k}/{count} frames', flush=True)
                finally:
                    writer.release()
                # Decode every frame, rather than trusting the container count.
                cap = cv2.VideoCapture(str(pending)); decoded = 0
                try:
                    while True:
                        ok, frame = cap.read()
                        if not ok:
                            break
                        if frame.shape[:2] != (replay.height,replay.width):
                            raise RuntimeError('Unexpected decoded frame size')
                        decoded += 1
                finally:
                    cap.release()
                if decoded != count:
                    raise RuntimeError(f'Incomplete video: {decoded}/{count} frames')
                manifest['decoded_frames'] = decoded
                pending.replace(target)
            manifest['video'] = target.name
            manifest['video_sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
        for name,digest in hashes.items():
            if hashlib.sha256((source/name).read_bytes()).hexdigest() != digest:
                raise RuntimeError(f'Recorded input changed: {name}')
        manifest['completed'] = True
    finally:
        manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f'VIDEO OUTPUT {out}', flush=True)
    return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='Recorded experiment directory')
    parser.add_argument('--output', type=Path, help='Default: <run>/exports/four_panel_video; repeat replaces derived outputs')
    parser.add_argument('--fps', type=int, help='Default: VIDEO_STYLE fps; playback remains 1x')
    parser.add_argument('--preview-only', action='store_true', help='Render keyframes only, without encoding video')
    args = parser.parse_args()
    render(args.directory, args.output, args.fps, args.preview_only)
