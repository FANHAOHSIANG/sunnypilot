"""Only the reviewed Cinque v2 graph is admitted by this experimental port."""
import json
from pathlib import Path

from jetlink.spec import ModelSpec

DEFAULT_SPEC = ModelSpec.from_dict(json.loads(Path(__file__).with_name('cinque_v2.json').read_text()))


def validate_model_spec(spec):
  if spec.to_dict() != DEFAULT_SPEC.to_dict():
    raise ValueError('This sunnypilot port requires the exact Cinque v2 queued model; RL Nav and other exports are not supported')
  return spec


def parse_outputs(parser, spec, result):
  validate_model_spec(spec)
  return parser.parse_outputs({name: result[None, at] for name, at in spec.output_slices.items()})
