"""HKG's pinned tinygrad stream-based out-of-band pickle ABI."""
import io


def load_prepared(data):
  from tinygrad_repo.examples.openpilot.helpers import load_pickle
  return load_pickle(io.BytesIO(data), out_of_band=True)


def save_prepared(value, path):
  from tinygrad_repo.examples.openpilot.helpers import dump_pickle
  with path.open('wb') as output:
    dump_pickle(value, output, out_of_band=True)
