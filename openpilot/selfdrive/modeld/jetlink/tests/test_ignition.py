import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

path = Path(__file__).resolve().parents[1] / "ignition.py"
spec = importlib.util.spec_from_file_location("jetlink_ignition", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class Params:
  offroad = True
  def get_bool(self, key):
    assert key == "IsOffroad"
    return self.offroad

class Messages:
  def __init__(self):
    self.alive = self.valid = True
    self.logMonoTime = {s: 100_000_000_000 for s in module.SERVICES}
    self.data = {
      "deviceState": SimpleNamespace(started=False),
      "pandaStates": [SimpleNamespace(ignitionLine=False, ignitionCan=False)],
    }
  def update(self, timeout):
    assert timeout == 0
  def all_alive(self, services):
    return self.alive
  def all_valid(self, services):
    return self.valid
  def __getitem__(self, key):
    return self.data[key]

class TestIgnition(unittest.TestCase):
  def setUp(self):
    self.params, self.sm = Params(), Messages()
  def allowed(self):
    return module.ignition_off(self.params, self.sm, now=100.5)
  def test_fresh_ignition_off(self):
    self.assertTrue(self.allowed())
  def test_offroad_override_cannot_hide_either_ignition(self):
    for attr in ("ignitionLine", "ignitionCan"):
      with self.subTest(attr=attr):
        setattr(self.sm.data["pandaStates"][0], attr, True)
        self.assertFalse(self.allowed())
        setattr(self.sm.data["pandaStates"][0], attr, False)
  def test_device_started(self):
    self.sm.data["deviceState"].started = True
    self.assertFalse(self.allowed())
  def test_onroad_param(self):
    self.params.offroad = False
    self.assertFalse(self.allowed())
  def test_no_pandas(self):
    self.sm.data["pandaStates"] = []
    self.assertFalse(self.allowed())
  def test_any_panda_ignition_blocks(self):
    self.sm.data["pandaStates"].append(SimpleNamespace(ignitionLine=True, ignitionCan=False))
    self.assertFalse(self.allowed())
  def test_missing_stale_future_messages(self):
    for service in module.SERVICES:
      for timestamp in (0, 98_000_000_000, 102_000_000_000):
        with self.subTest(service=service, timestamp=timestamp):
          self.sm.logMonoTime[service] = timestamp
          self.assertFalse(self.allowed())
      self.sm.logMonoTime[service] = 100_000_000_000
  def test_dead_or_invalid(self):
    for attr in ("alive", "valid"):
      setattr(self.sm, attr, False)
      self.assertFalse(self.allowed())
      setattr(self.sm, attr, True)
  def test_no_removed_parameter_in_daemon(self):
    source = (path.parent / "daemon.py").read_text()
    self.assertNotIn("IsOnroad", source)
    self.assertIn("offroad = lambda: ignition_off(params, ignition_sm)", source)

if __name__ == "__main__":
  unittest.main()
