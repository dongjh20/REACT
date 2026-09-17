"""Numerical and recorded-input checks for the standalone web comparison."""
import unittest
import copy
import json
from pathlib import Path
from unittest.mock import patch
import numpy as np
import render_tvfr_comparison as render


class ComparisonTests(unittest.TestCase):
    def test_supplemental_acceptance_does_not_hide_physical_or_parameter_failures(self):
        from verify_historical_replay import validate
        source = render.RESULTS/'20260916_181033_397685'
        self.assertTrue(validate(source)['passed'])
        self.assertFalse(validate(source)['original_recorder_passed'])
        original = Path.read_text
        for file, mutate in (
            ('report.json', lambda r: r['mesh_footprint'].update(passed=False)),
            ('report.json', lambda r: r.update(errors=['CBM-TAPF failed'])),
            ('independent_parameter_readback.json', lambda r: r['parameters'].pop('/drone_1_pcl_render_node')),
            ('independent_parameter_readback.json', lambda r: r['parameters']['/drone_1_pcl_render_node'].update(sensing_horizon=1.)),
        ):
            altered = json.loads(original(source/file))
            mutate(altered)
            def read(path, *args, **kwargs):
                return json.dumps(altered) if path == source/file else original(path,*args,**kwargs)
            with patch.object(Path, 'read_text', read), self.assertRaises(ValueError):
                validate(source)

    def test_equal_geometry_does_not_excuse_different_trigger_gates(self):
        times = np.array([0., 1.])
        latest = render.load_clip(render.RESULTS/render.DEFAULTS['current'], 0, times)
        same = copy.deepcopy(latest)
        audit = render.trigger_audit((same, latest), require_match=True)
        self.assertTrue(audit['exact_rule_match'])
        self.assertEqual(audit['mean_position_difference_m'], [0., 0.])
        same['cfg']['experiment']['stages'][0].pop('all_wmr_past_x', None)
        with self.assertRaisesRegex(ValueError, 'trigger rules differ'):
            render.trigger_audit((same, latest), require_match=True)

    def test_failed_runtime_recording_cannot_be_published(self):
        self.assertTrue(render.verify_completed_run(render.RESULTS/render.DEFAULTS['current'])['passed'])
        with self.assertRaisesRegex(ValueError, 'full runtime validation failed'):
            render.verify_completed_run(render.RESULTS/'20260916_170717_980168')

    def test_matching_environment_and_rejection_of_shifted_archive(self):
        times = np.array([0., 1.])
        old = render.load_clip(render.RESULTS/render.DEFAULTS['before_32'], 0, times)
        latest = render.load_clip(render.RESULTS/render.DEFAULTS['current'], 0, times)
        self.assertTrue(render.verify_matched_environment((old, latest))['exact_match'])
        shifted = render.load_clip(render.RESULTS/'20260916_163215_772733', 0, times)
        with self.assertRaisesRegex(ValueError, 'obstacle geometry'):
            render.verify_matched_environment((old, shifted))
        changed = copy.deepcopy(latest)
        changed['cfg']['experiment']['route'][-1][0] += .01
        with self.assertRaisesRegex(ValueError, 'experiment.route'):
            render.verify_matched_environment((old, changed))

    def test_six_legends_do_not_cover_recorded_curves_or_bars(self):
        times = np.linspace(-2., 16., 901)
        pairs = [(render.load_clip(render.RESULTS/render.DEFAULTS['before_32'], i, times),
                  render.load_clip(render.RESULTS/render.DEFAULTS['current'], i, times)) for i in (0, 3)]
        checked = []
        def audit(fig, output, name):
            try:
                if name != 'tvfr_transient_comparison':
                    return
                fig.canvas.draw()
                fig.canvas.draw()
                self.assertEqual(len(fig.axes), 6)
                for ax in fig.axes[:4]:
                    self.assertEqual(ax.get_xlabel(), 'Time from transition [s]')
                for ax in fig.axes[:2]:
                    annotations = [t for t in ax.texts if t.get_text() == 'Transition start']
                    self.assertEqual(len(annotations), 1)
                    note = annotations[0]
                    self.assertEqual(note.xy[0], 0.)
                    self.assertEqual(note.xy[1], note.get_position()[1])
                    box = note.get_window_extent(fig.canvas.get_renderer())
                    self.assertFalse(box.overlaps(ax.get_legend().get_window_extent()))
                    # Check the text separately: its arrow intentionally meets
                    # the vertical event line, but the label must clear data.
                    from matplotlib.text import Text
                    text_box = Text.get_window_extent(note, fig.canvas.get_renderer())
                    for line in ax.lines:
                        self.assertFalse(line.get_path().transformed(
                            line.get_transform()).intersects_bbox(text_box, filled=False))
                for ax in fig.axes:
                    legend = ax.get_legend()
                    self.assertIsNotNone(legend)
                    self.assertEqual([t.get_text() for t in legend.get_texts()], list(render.LABELS))
                    box = legend.get_window_extent(fig.canvas.get_renderer())
                    for line in ax.lines:
                        self.assertFalse(line.get_path().transformed(line.get_transform()).intersects_bbox(box, filled=False))
                    for bar in ax.patches:
                        self.assertFalse(bar.get_window_extent().overlaps(box))
                    checked.append(True)
            finally:
                render.plots.plt.close(fig)
        with render.plots.plt.rc_context(), patch.dict(render.plots.STYLE,
                font_size=render.STYLE['font_size'], legend_font_size=render.STYLE['legend_font_size']):
            render.plots.configure_font()
            with patch.object(render, 'save_figure', audit):
                render.render_figures(pairs, None)
        self.assertEqual(len(checked), 6)

    def test_lateral_excess_distinguishes_motion_from_oscillation(self):
        a = np.zeros((1, 3, 7))
        a[0, :, 1] = [0., 1., 2.]
        clip = dict(times=np.array([0., 1., 2.]), samples=a)
        self.assertEqual(render.metrics(clip)['total_excess_lateral_travel_m'], 0.)
        a[0, :, 1] = [0., 1., 0.]
        self.assertEqual(render.metrics(clip)['total_excess_lateral_travel_m'], 2.)

    def test_reference_gradients_match_finite_differences(self):
        rng = np.random.default_rng(12)
        p, r = rng.normal(size=(7, 2)), rng.normal(size=(7, 2))
        def cost(x):
            e = x-x.mean(axis=0)-r+r.mean(axis=0)
            return np.sum(e*e)/len(x)
        gradient = 2*(p-p.mean(axis=0)-r+r.mean(axis=0))/len(p)
        for i in range(7):
            for j in range(2):
                plus, minus = p.copy(), p.copy()
                plus[i, j] += 1e-6
                minus[i, j] -= 1e-6
                self.assertAlmostEqual((cost(plus)-cost(minus))/2e-6, gradient[i, j], places=7)
        self.assertAlmostEqual(cost(p), cost(p+[14., -3.]), places=12)

    def test_fixed_route_anchor_gradients(self):
        rng = np.random.default_rng(20)
        p = rng.normal(size=(7, 2))
        yc = 4.
        for i in range(7):
            plus, minus = p.copy(), p.copy()
            plus[i, 1] += 1e-6
            minus[i, 1] -= 1e-6
            fd = ((plus[:, 1].mean()-yc)**2-(minus[:, 1].mean()-yc)**2)/2e-6
            self.assertAlmostEqual(fd, 2*(p[:, 1].mean()-yc)/7, places=7)

    def test_recorded_event_alignment_and_data_coverage(self):
        times = np.array([-2., 0., 16.])
        clip = render.load_clip(render.RESULTS/render.DEFAULTS['current'], 3, times)
        self.assertEqual(clip['samples'].shape, (7, 3, 7))
        for i, a in enumerate(clip['cars']):
            self.assertAlmostEqual(clip['samples'][i, 1, 0], np.interp(clip['event_time'], a[:, 0], a[:, 1]))
        with self.assertRaises(ValueError):
            render.load_clip(render.RESULTS/render.DEFAULTS['current'], 3, np.array([-1e6, 1e6]))

    def test_without_tvfr_labels_require_disabled_reference(self):
        for index in (0, 3):
            baseline = render.load_clip(render.RESULTS/render.DEFAULTS['before_32'], index, np.array([0., 1.]))
            self.assertFalse(render.verify_tvfr_label(baseline, False)['enabled'])
            current = render.load_clip(render.RESULTS/render.DEFAULTS['current'], index, np.array([0., 1.]))
            self.assertTrue(render.verify_tvfr_label(current, True)['enabled'])
            with self.assertRaises(ValueError):
                render.verify_tvfr_label(current, False)
        previous_wrong_selection = render.load_clip(render.RESULTS/'20260914_171816_922224', 0, np.array([0., 1.]))
        with self.assertRaises(ValueError):
            render.verify_tvfr_label(previous_wrong_selection, False)


if __name__ == '__main__':
    unittest.main()
