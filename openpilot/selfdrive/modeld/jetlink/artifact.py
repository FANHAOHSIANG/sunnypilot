"""The out-of-band pickle ABIs used across HKG and sunnypilot 0.11.3."""
import io
import inspect
import tempfile
from pathlib import Path


def stream_abi():
  from tinygrad_repo.examples.openpilot.helpers import dump_pickle
  return 'out_of_band' in inspect.signature(dump_pickle).parameters


def load_prepared(data):
  from tinygrad_repo.examples.openpilot.helpers import load_pickle
  if stream_abi():
    return load_pickle(io.BytesIO(data), out_of_band=True)
  directory = '/dev/shm' if Path('/dev/shm').is_dir() else None
  with tempfile.NamedTemporaryFile(prefix='jetlink-warp-', dir=directory) as artifact:
    artifact.write(data)
    artifact.flush()
    return load_pickle(artifact.name, out_of_band=True)


def save_prepared(value, path):
  from tinygrad_repo.examples.openpilot.helpers import dump_pickle
  if stream_abi():
    with path.open('wb') as output:
      dump_pickle(value, output, out_of_band=True)
  else:
    dump_pickle(value, path)
