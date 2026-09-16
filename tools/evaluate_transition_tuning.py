#!/usr/bin/env python3
"""Read-only evaluation of recorded TVFR candidates, at fixed sampling/windows.

Prints JSON; optional --output saves a separate audit (never modifies recordings).
Finite-difference acceleration is a sampled estimate, not a continuous bound.
"""
import argparse
import json
from pathlib import Path
import numpy as np
import render_tvfr_comparison as render


def evaluate(directory):
    directory = Path(directory).resolve()
    report = json.loads((directory/'report.json').read_text())
    try:
        accepted = render.verify_completed_run(directory)['passed']
    except ValueError:
        accepted = False
    result = dict(run=str(directory), runtime_passed=accepted,
                  original_recorder_passed=report['passed'], transitions=[])
    result['runtime_checks'] = {key:report[key] for key in (
        'finished','parameter_chain_passed','mesh_footprint','processes_started','processes_finished_cleanly','errors')}
    for index in (0, 3):
        clip = render.load_clip(directory, index, np.linspace(0., 16., 801))
        values = render.metrics(clip)
        acceleration = np.diff(clip['samples'][:, :, 3:5], axis=1)/.02
        values.update(event_index=index, duration=clip['cfg']['experiment']['stages'][index].get('transition_duration', 0.),
                      lateral_duration=clip['cfg']['experiment']['stages'][index].get('lateral_transition_duration',clip['cfg']['experiment']['stages'][index].get('transition_duration',0.)),
                      lateral_acceleration_rms_mps2=float(np.sqrt(np.mean(acceleration[:, :, 1]**2))),
                      planar_acceleration_rms_mps2=float(np.sqrt(np.mean(np.sum(acceleration**2, axis=2)))),
                      peak_abs_yaw_deg=max(row['peak_abs_yaw_deg'] for row in values['per_wmr']),
                      total_reverse_distance_m=sum(row['reverse_distance_m'] for row in values['per_wmr']))
        result['transitions'].append(values)
    result['two_column_alignment'] = report['two_column_alignment']
    result['final_formation'] = report['final_formation']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directories', nargs='+', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    data = dict(window_seconds=[0, 16], sample_seconds=.02,
                acceleration_method='finite difference of interpolated recorded velocities',
                note='Individual runs; no statistical significance or single-variable causal claim.',
                runs=[evaluate(p) for p in args.directories])
    text = json.dumps(data, indent=2)+'\n'
    if args.output:
        if any(args.output.resolve().is_relative_to(p.resolve()) for p in args.directories):
            parser.error('Save the audit outside input recording directories')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text)


if __name__ == '__main__':
    main()
