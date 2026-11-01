"""Optional test through the actual pinned Carrot parser and scene builder."""
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from openpilot.selfdrive.modeld.jetlink.hud_radar_trial import PassiveRadar, apply_trial
from openpilot.selfdrive.modeld.jetlink.tests.test_hud_radar_trial import cycle, VECTORS


@unittest.skipUnless(all(os.environ.get(k) for k in ('MAC_CHECKOUT', 'CARROT_HELPER_ROOT', 'HKG_CHECKOUT')),
                     'needs Mac adapter and pinned Carrot renderer checkout')
class RenderTests(unittest.TestCase):
  def test_raw_can_to_hud_json_to_actual_scene_left_right_and_primary(self):
    path = Path(os.environ['MAC_CHECKOUT']) / 'tests/test_hkg_pipeline.py'
    spec = importlib.util.spec_from_file_location('radar_pipeline_fixture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixture = module.PipelineTests()
    fixture.setUp()
    self.addCleanup(fixture.doCleanups)
    trial = Path(fixture.temp.name) / 'trial.json'
    decoder = PassiveRadar(1)
    for i in range(3):
      cycle(decoder, VECTORS[0], 9.9 + .05 * i)
    trial.write_text(json.dumps(decoder.snapshot(10., 'boot')))
    original = fixture.publisher.snapshot
    def with_trial(*args, **kwargs):
      record = original(*args, **kwargs)
      apply_trial(record, 10.01, 'boot', trial)
      return record
    with patch.object(fixture.publisher, 'snapshot', side_effect=with_trial):
      state = fixture.update()
      points = sorted(state.radar_points, key=lambda p: p.longitudinal_m)
      self.assertEqual(len(points), 2)
      self.assertEqual([p.lateral_m for p in points], [-3.5, 3.5])
      self.assertAlmostEqual(points[0].absolute_speed_kph, 64.8)
      self.assertAlmostEqual(points[1].absolute_speed_kph, 75.6)
      from cluster_scene import build_cluster_scene
      vehicles = {v.label: v for v in build_cluster_scene(state).vehicles}
      self.assertTrue(vehicles['L1'].primary)
      for point in points:
        self.assertIn(point.label, vehicles)
        self.assertFalse(vehicles[point.label].primary)
      trial.unlink()
      self.assertEqual(fixture.update().radar_points, ())

  def test_corner_zero_forward_distance_is_not_dropped_as_missing_position(self):
    # The installed Mac reader passes arbitrary fields inside liveTracks;
    # radarSource must survive so Carrot recognizes clipped side objects.
    path = Path(os.environ['MAC_CHECKOUT']) / 'tests/test_hkg_pipeline.py'
    spec = importlib.util.spec_from_file_location('corner_pipeline_fixture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixture = module.PipelineTests()
    fixture.setUp()
    self.addCleanup(fixture.doCleanups)
    trial = Path(fixture.temp.name) / 'trial.json'
    decoder = PassiveRadar(1)
    for i in range(3):
      cycle(decoder, VECTORS[-1], 9.9 + .05 * i)
    trial.write_text(json.dumps(decoder.snapshot(10., 'boot')))
    original = fixture.publisher.snapshot
    def with_trial(*args, **kwargs):
      record = original(*args, **kwargs)
      apply_trial(record, 10.01, 'boot', trial)
      return record
    with patch.object(fixture.publisher, 'snapshot', side_effect=with_trial):
      state = fixture.update()
    points = sorted(state.radar_points, key=lambda p: p.longitudinal_m)
    self.assertEqual(len(points), 2)
    self.assertEqual(points[0].longitudinal_m, 0.)
    self.assertAlmostEqual(points[0].lateral_m, 3.)
    self.assertEqual(points[0].source, 'cornerRadar')
    self.assertAlmostEqual(points[0].absolute_speed_kph, 75.6)


  def test_c4_metrics_and_tpms_reach_actual_carrot_state(self):
    path = Path(os.environ['MAC_CHECKOUT']) / 'tests/test_hkg_pipeline.py'
    spec = importlib.util.spec_from_file_location('metrics_pipeline_fixture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixture = module.PipelineTests()
    fixture.setUp()
    self.addCleanup(fixture.doCleanups)
    fixture.values['deviceState'].update(cpuUsagePercent=[10., 30.], cpuTempC=[55., 60.],
      memoryUsagePercent=40, freeSpacePercent=75.)
    fixture.values['carState']['tpms'] = dict(fl=39., fr=40., rl=40., rr=41.)
    state = fixture.update()
    self.assertEqual(state.cpu_usage_percent, 20.)
    self.assertEqual(state.cpu_temp_c, 60.)
    self.assertEqual(state.memory_used_percent, 40.)
    self.assertEqual(state.disk_used_percent, 25.)
    self.assertEqual(state.tpms.fl, 39.)
    self.assertEqual(state.tpms.rr, 41.)


if __name__ == '__main__':
  unittest.main()
