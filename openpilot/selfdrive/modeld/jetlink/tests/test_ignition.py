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
    self.assertIn("setup_allowed = lambda: connection_allowed(params, ignition_sm, vehicle_sm)", source)




class ConnectionMessages(Messages):
  def __init__(self):
    super().__init__()
    self.data = {
      "carState": SimpleNamespace(vEgo=25., standstill=False, gearShifter="drive"),
      "selfdriveState": SimpleNamespace(enabled=True, active=True),
      "carControl": SimpleNamespace(enabled=True, latActive=True, longActive=True),
      "carControlSP": SimpleNamespace(mads=SimpleNamespace(enabled=True)),
    }
    self.logMonoTime = dict.fromkeys(module.CONNECTION_SERVICES, 100_000_000_000)


class TestBackgroundConnection(unittest.TestCase):
  def setUp(self):
    self.params = Params()
    self.params.offroad = False
    self.hardware = Messages()
    self.hardware.data["deviceState"].started = True
    self.hardware.data["pandaStates"][0].ignitionLine = True
    self.vehicle = ConnectionMessages()

  def allowed(self):
    return module.connection_allowed(self.params, self.hardware, self.vehicle, now=100.1)

  def test_moving_and_engaged_may_connect_without_changing_params_or_ignition(self):
    self.assertTrue(self.allowed())
    self.assertFalse(self.params.offroad)
    self.assertTrue(self.hardware.data["pandaStates"][0].ignitionLine)
    self.assertTrue(self.vehicle.data["carControl"].enabled)

  def test_background_connection_does_not_allow_engaged_model_handover(self):
    gate_spec = importlib.util.spec_from_file_location("jetlink_gate", path.parent / "gate.py")
    gate = importlib.util.module_from_spec(gate_spec)
    gate_spec.loader.exec_module(gate)
    self.assertTrue(self.allowed())
    valid = dict.fromkeys(self.vehicle.data, True)
    received = dict.fromkeys(self.vehicle.data, 100.)
    self.assertFalse(gate.may_join(100.1, self.vehicle.data, valid, received))
    self.vehicle.data["selfdriveState"].enabled = False
    self.vehicle.data["selfdriveState"].active = False
    cc = self.vehicle.data["carControl"]
    cc.enabled = cc.latActive = cc.longActive = False
    self.vehicle.data["carControlSP"].mads.enabled = False
    self.assertTrue(gate.may_join(100.1, self.vehicle.data, valid, received))

  def test_true_ignition_off_still_works_without_onroad_messages(self):
    self.params.offroad = True
    self.hardware.data["deviceState"].started = False
    self.hardware.data["pandaStates"][0].ignitionLine = False
    self.vehicle.alive = False
    self.assertTrue(self.allowed())

  def test_offroad_override_does_not_replace_fresh_onroad_data(self):
    self.params.offroad = True
    self.vehicle.alive = False
    self.assertFalse(self.allowed())

  def test_every_stale_future_missing_or_invalid_onroad_service_blocks(self):
    for key in module.CONNECTION_SERVICES:
      for stamp in (0, 99_000_000_000, 101_000_000_000):
        self.vehicle.logMonoTime[key] = stamp
        self.assertFalse(self.allowed())
      self.vehicle.logMonoTime[key] = 100_000_000_000
    for field in ("alive", "valid"):
      setattr(self.vehicle, field, False)
      self.assertFalse(self.allowed())
      setattr(self.vehicle, field, True)

  def test_invalid_or_stale_hardware_and_empty_panda_list_block(self):
    for field in ("alive", "valid"):
      setattr(self.hardware, field, False)
      self.assertFalse(self.allowed())
      setattr(self.hardware, field, True)
    for key in module.SERVICES:
      self.hardware.logMonoTime[key] = 0
      self.assertFalse(self.allowed())
      self.hardware.logMonoTime[key] = 100_000_000_000
    self.hardware.data["pandaStates"] = []
    self.assertFalse(self.allowed())

  def test_nonfinite_speed_blocks_connection(self):
    for value in (float("nan"), float("inf")):
      self.vehicle.data["carState"].vEgo = value
      self.assertFalse(self.allowed())

if __name__ == "__main__":
  unittest.main()
