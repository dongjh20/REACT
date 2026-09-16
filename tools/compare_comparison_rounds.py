#!/usr/bin/env python3
"""Compare archived rounds with a fixed 0..16 s, 0.02 s measurement window."""
import argparse
import json
from pathlib import Path
import numpy as np
import render_tvfr_comparison as render
from evaluate_transition_tuning import evaluate


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--replay', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    old = render.RESULTS/'20260914_145854_166506'
    reference = render.RESULTS/'20260914_210201_219110'
    rounds = [
        ('original_unmatched_environment', old, reference),
        ('matched_environment_before_axis_tuning', old, render.RESULTS/'20260916_163215_772733'),
        ('matched_environment_after_axis_tuning', old, render.RESULTS/'20260916_170430_434793'),
        ('historical_replay_matched_environment_and_triggers', args.replay, reference),
    ]
    runs = {str(p): evaluate(p) for _, a, b in rounds for p in (a, b)}
    summaries = []
    for name, a, b in rounds:
        transitions = []
        for index, event in enumerate((0, 3)):
            pair = [render.load_clip(p, event, np.array([0., 1.])) for p in (a, b)]
            try:
                environment = render.verify_matched_environment(pair)
            except ValueError as error:
                environment = dict(exact_match=False, reason=str(error))
            left, right = [runs[str(p)]['transitions'][index] for p in (a, b)]
            e0, e1 = [r['total_excess_lateral_travel_m'] for r in (left, right)]
            transitions.append(dict(event_index=event, environment=environment,
                triggers=render.trigger_audit(pair), without=left, with_tvfr=right,
                excess_reduction_percent=100*(e0-e1)/e0 if e0 else None,
                absolute_excess_difference_m=e0-e1))
        summaries.append(dict(name=name, without=str(a), with_tvfr=str(b), transitions=transitions))
    result = dict(window_seconds=[0.,16.], sample_seconds=.02, rounds=summaries,
                  note='Individual trials, not statistical significance; equal rules do not imply identical switch states.')
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'rounds.json').write_text(json.dumps(result, indent=2)+'\n')
    text = ['# Comparison rounds', '',
            'Fixed 0–16 s after each actual switch; recorded world coordinates, no shifts or filtering.', '',
            '| Round | Transition | Excess travel: Without / With [m] | Reduction | Peak abs vy: Without / With [m/s] | Min vx: Without / With [m/s] |',
            '|---|---|---:|---:|---:|---:|']
    for row in summaries:
        for t in row['transitions']:
            left, right = t['without'], t['with_tvfr']
            def values(key): return f'{left[key]:.4f} / {right[key]:.4f}'
            name = '3→2' if t['event_index']==0 else '1→3'
            text.append(f"| {row['name']} | {name} | {values('total_excess_lateral_travel_m')} | {t['excess_reduction_percent']:.2f}% | {values('peak_abs_vy_mps')} | {values('minimum_vx_mps')} |")
    text += ['', 'See rounds.json for per-WMR values, gate equality, switch-state differences, and source directories.',
             'Excess lateral travel is total absolute lateral travel minus net lateral displacement, summed over seven WMRs.',
             'Single trials do not establish repeatability or isolate TVFR from all other historical algorithm changes.']
    (args.output/'rounds.md').write_text('\n'.join(text)+'\n')
    print('\n'.join(text))


if __name__ == '__main__':
    main()
