"""Regression: a received OEM frame alone does not establish an operating proxy."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_policy import TestNativePolicy, packet

# Reuse the same native C build, but do not count its inherited tests twice.
class TestTransportReadiness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TestNativePolicy.setUpClass()
        cls.lib = TestNativePolicy.lib
    @classmethod
    def tearDownClass(cls):
        TestNativePolicy.tearDownClass()
    def setUp(self):
        self.fixture = TestNativePolicy(methodName='test_same_frame_only_once')
        self.fixture.setUp()
    def test_receiving_without_host_copy_is_not_fcw_ready(self):
        _, t = self.fixture.acquire()
        self.assertFalse(self.lib.fcw_ready(t))
    def test_successful_exact_copy_then_ready(self):
        p,t = self.fixture.acquire()
        self.assertTrue(self.fixture.call('authorize',p,t))
        self.assertTrue(self.lib.fcw_ready(t))
    def test_source_continues_but_host_stalls_not_ready(self):
        p,t = self.fixture.acquire()
        self.assertTrue(self.fixture.call('authorize',p,t))
        for k in range(1,7):
            self.assertTrue(self.fixture.call('observe',packet(2+k),t+k*20000))
        self.assertTrue(self.lib.healthy(t+120000))
        self.assertFalse(self.lib.fcw_ready(t+120000))
    def test_refused_copy_cannot_establish_readiness(self):
        p,t = self.fixture.acquire()
        q=bytearray(p);q[9]^=1
        self.assertFalse(self.fixture.call('authorize',bytes(q),t))
        self.assertFalse(self.lib.fcw_ready(t))
    def test_reset_revokes_prior_successful_copy(self):
        p,t = self.fixture.acquire()
        self.assertTrue(self.fixture.call('authorize',p,t))
        self.lib.reset(1)
        self.assertFalse(self.lib.fcw_ready(t))

if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(TestTransportReadiness)
    r=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not r.wasSuccessful())
