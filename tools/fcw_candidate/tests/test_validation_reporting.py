"""Do not label missing/zero/skipped/failing full test runs successful."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from validate_all import summarize_unittest, completed_status, STAGES
class ValidationReporting(unittest.TestCase):
    def test_normal_summary(self):
        x=summarize_unittest('Ran 4 tests in 0.010s\n\nOK\n',0,4)
        self.assertEqual(x['executed_non_skipped'],4)
    def test_zero_tests_rejected(self):
        with self.assertRaises(ValueError):summarize_unittest('Ran 0 tests in 0.000s\nOK\n',0)
    def test_nonzero_exit_rejected(self):
        with self.assertRaises(ValueError):summarize_unittest('Ran 4 tests in 1.0s\nOK\n',1)
    def test_no_summary_rejected(self):
        with self.assertRaises(ValueError):summarize_unittest('import failed',0)
    def test_all_skipped_rejected(self):
        with self.assertRaises(ValueError):summarize_unittest('Ran 4 tests in 0.0s\nOK (skipped=4)\n',0,1,True)
    def test_targeted_skips_rejected(self):
        with self.assertRaises(ValueError):summarize_unittest('Ran 4 tests in 0.0s\nOK (skipped=1)\n',0,1)
    def test_full_suite_skips_counted_separately(self):
        x=summarize_unittest('Ran 3835 tests in 26.150s\nOK (skipped=276)\n',0,3000,True)
        self.assertEqual(x['executed_non_skipped'],3559)
    def test_duplicate_summary_rejected(self):
        with self.assertRaises(ValueError):summarize_unittest('Ran 4 tests in 0.0s\nOK\nRan 5 tests in 0.0s\nOK\n',0)
    def test_incomplete_manifest_not_pass(self):
        self.assertTrue(completed_status({}).startswith('BLOCKED'))
        stages={name:{'status':'PASS'} for name in STAGES};stages['full-safety-bench']['status']='NOT_RUN'
        self.assertTrue(completed_status(stages).startswith('BLOCKED'))
    def test_success_still_not_road_approval(self):
        stages={name:{'status':'PASS'} for name in STAGES}
        self.assertEqual(completed_status(stages),'SOFTWARE_TESTS_PASSED_HARDWARE_UNVERIFIED')
if __name__=='__main__':unittest.main(verbosity=2)
