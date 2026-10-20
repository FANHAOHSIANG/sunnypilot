"""Exercise the actual model-manager default-seeding condition without hardware."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[5]
SOURCE = ROOT / "openpilot/sunnypilot/models/manager.py"

def predicate():
  tree = ast.parse(SOURCE.read_text())
  manager = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ModelManagerSP")
  main = next(n for n in manager.body if isinstance(n, ast.FunctionDef) and n.name == "main_thread")
  conditions = [n.test for n in ast.walk(main) if isinstance(n, ast.If)
                and "get_selected_bundle" in ast.unparse(n.test)
                and '"chestnut"' in ast.unparse(n.test).replace("'", '"')
                and '"qcom"' in ast.unparse(n.test).replace("'", '"')]
  if len(conditions) != 1:
    raise AssertionError("Cannot identify default-seeding condition")
  return compile(ast.Expression(conditions[0]), "<actual model-manager condition>", "eval")

class TestModelSelection(unittest.TestCase):
  def test_retained_chestnut_selection_never_reselects_tinygrad_for_jetlink(self):
    condition = predicate()
    for jetlink in (False, True):
      for small in (None, object()):
        for big in (None, object()):
          with self.subTest(jetlink=jetlink, small=small is not None, big=big is not None):
            params = SimpleNamespace(get_bool=lambda key: jetlink if key == "JetlinkEnabled" else None)
            value = eval(condition, {
              "self": SimpleNamespace(params=params),
              "get_selected_bundle": lambda p, source: small if source == "qcom" else big,
            })
            self.assertEqual(value, not jetlink and big is not None and small is None)

if __name__ == "__main__":
  unittest.main()
