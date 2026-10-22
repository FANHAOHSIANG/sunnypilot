"""Evaluate the real model selection helpers with saved picks and a fake store."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[5]

class Params:
  def __init__(self, enabled=True, cache=1):
    self.data = {"JetlinkEnabled": enabled, "ModelRunnerTypeCache": cache}
  def get(self, key):
    return self.data.get(key)
  def get_bool(self, key):
    return bool(self.data.get(key))
  def put(self, key, value, block=False):
    self.data[key] = value

def functions(path, names, scope):
  tree = ast.parse(path.read_text())
  body = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
  body += [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
  module = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
  exec(compile(module, str(path), "exec"), scope)
  return scope

class TestJetlinkRunner(unittest.TestCase):
  def setUp(self):
    self.saved = {"qcom": SimpleNamespace(internalName="C210M", displayName="C210M", runner=SimpleNamespace(raw=1)),
                  "chestnut": SimpleNamespace(internalName="saved big", displayName="saved big", runner=SimpleNamespace(raw=1))}
    self.scope = functions(ROOT / "openpilot/sunnypilot/models/helpers.py",
                           {"get_active_bundle", "get_active_model_runner"}, {
      "Params": Params,
      "custom": SimpleNamespace(ModelManagerSP=SimpleNamespace(Runner=SimpleNamespace(stock=2))),
      "get_active_source": lambda chestnut=None: "chestnut" if chestnut else "qcom",
      "get_selected_bundle": lambda params, source: self.saved.get(source),
    })

  def test_enabled_ignores_cached_tinygrad_and_preserves_both_saved_models(self):
    p = Params(enabled=True, cache=1)
    picks = self.saved.copy()
    self.assertIsNone(self.scope["get_active_bundle"](p))
    self.assertIsNone(self.scope["get_active_bundle"](p, chestnut=True))
    self.assertEqual(self.scope["get_active_model_runner"](p), 2)
    self.assertEqual(p.get("ModelRunnerTypeCache"), 2)
    self.assertEqual(self.saved, picks)

  def test_enabled_works_with_or_without_cache_or_custom_pick(self):
    for cache in (None, 1, 2):
      p = Params(enabled=True, cache=cache)
      self.assertEqual(self.scope["get_active_model_runner"](p), 2)
      self.saved.pop("qcom", None)
      self.assertEqual(self.scope["get_active_model_runner"](p, force_check=True), 2)

  def test_offroad_recheck_restores_saved_pick_after_disabling(self):
    p = Params(enabled=True, cache=1)
    self.assertEqual(self.scope["get_active_model_runner"](p), 2)
    p.data["JetlinkEnabled"] = False
    self.assertIs(self.scope["get_active_bundle"](p), self.saved["qcom"])
    self.assertEqual(self.scope["get_active_model_runner"](p, force_check=True), 1)
    self.assertEqual(p.get("ModelRunnerTypeCache"), 1)

  def test_disabled_preserves_existing_cache_semantics(self):
    p = Params(enabled=False, cache=1)
    self.assertEqual(self.scope["get_active_model_runner"](p), 1)
    self.assertIs(self.scope["get_active_bundle"](p, chestnut=True), self.saved["chestnut"])

  def test_ui_reports_builtin_fallback_while_retaining_saved_selection(self):
    p = Params(enabled=True)
    scope = functions(ROOT / "openpilot/selfdrive/ui/sunnypilot/model_info.py",
                      {"model_info", "carrying_model"}, {
      "ui_state": SimpleNamespace(params=p, chestnut_present=False),
      "active_source": lambda: "qcom",
      "default_model_name": lambda source: source + " default",
      "get_selected_bundle": lambda params, source: self.saved.get(source),
    })
    self.assertEqual(scope["model_info"](), ("qcom", "qcom default", "chestnut default"))
    self.assertEqual(scope["carrying_model"](), ("qcom", "qcom default", "qcom default"))
    p.data["JetlinkEnabled"] = False
    self.assertEqual(scope["model_info"]()[1], "C210M")

if __name__ == "__main__":
  unittest.main()
