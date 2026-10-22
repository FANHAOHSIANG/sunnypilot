"""Transport failure boundaries and the actual reversible root memory policy."""
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openpilot.selfdrive.modeld.jetlink import daemon


class USBRuntimeTests(unittest.TestCase):
  def test_incomplete_aio_cleanup_blocks_reopening(self):
    t = daemon.SafeTransport.__new__(daemon.SafeTransport)
    t.cleanup_incomplete = True
    with patch.object(daemon.FfsTransport, 'close'):
      with self.assertRaisesRegex(daemon.UnsafeCleanupError, 'AIO cleanup'):
        t.close()

  def test_telemetry_is_a_snapshot_and_tcp_needs_no_aio_fields(self):
    values = {'submit_ms': 3.0}
    client = SimpleNamespace(t=SimpleNamespace(last_send=values))
    snapshot = daemon.transport_stat(client, 'last_send')
    values['submit_ms'] = 99.0
    self.assertEqual(snapshot, {'submit_ms': 3.0})
    self.assertEqual(daemon.transport_stat(client, 'send_totals'), {})

  def test_root_vm_apply_repeat_and_restore_keep_original_values(self):
    script = Path(__file__).resolve().parents[5] / 'tools/jetlink/ios-root.sh'
    initial = {'vm.dirty_bytes': 1000000, 'vm.dirty_background_bytes': 500000,
               'vm.extra_free_kbytes': 1000, 'net.core.wmem_max': 212992,
               'net.core.rmem_max': 212992}
    wanted = {'vm.dirty_bytes': 16777216, 'vm.dirty_background_bytes': 8388608,
              'vm.extra_free_kbytes': 32768, 'net.core.wmem_max': 4194304,
              'net.core.rmem_max': 4194304}
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      record = root / 'saved'
      for key, value in initial.items():
        path = root / key.replace('.', '/')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(value) + '\n')
      env = dict(os.environ, JETLINK_PROC_SYS=str(root), JETLINK_SYSCTL_PREV=str(record))
      def run(action):
        subprocess.run(['bash', str(script), 'vm', action], env=env, check=True,
                       capture_output=True, timeout=5)
      run('apply')
      backup = record.read_text()
      run('apply')
      self.assertEqual(record.read_text(), backup)
      for key, value in wanted.items():
        self.assertEqual(int((root / key.replace('.', '/')).read_text()), value)
      run('restore')
      self.assertFalse(record.exists())
      for key, value in initial.items():
        self.assertEqual(int((root / key.replace('.', '/')).read_text()), value)
      run('restore')

  def test_root_vm_saves_ratio_when_dirty_bytes_is_zero(self):
    script = Path(__file__).resolve().parents[5] / 'tools/jetlink/ios-root.sh'
    initial = {'vm.dirty_bytes': 0, 'vm.dirty_background_bytes': 0,
               'vm.dirty_ratio': 20, 'vm.dirty_background_ratio': 10,
               'vm.extra_free_kbytes': 0, 'net.core.wmem_max': 212992, 'net.core.rmem_max': 212992}
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      for key, value in initial.items():
        path = root / key.replace('.', '/')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(value) + '\n')
      env = dict(os.environ, JETLINK_PROC_SYS=str(root), JETLINK_SYSCTL_PREV=str(root / 'saved'))
      subprocess.run(['bash', str(script), 'vm', 'apply'], env=env, check=True, capture_output=True, timeout=5)
      saved = (root / 'saved').read_text()
      self.assertIn('vm.dirty_ratio=20\n', saved)
      self.assertIn('vm.dirty_background_ratio=10\n', saved)
      self.assertNotIn('vm.dirty_bytes=', saved)
