"""Parse a Mac Jetlink N16 frame with HCOP's own parser for offline evaluation.

The caller supplies a negotiated client from the pinned Carrot Jetlink package.
This adapter neither owns a USB gadget nor publishes driving commands.
"""
import numpy as np

from openpilot.selfdrive.modeld.parse_model_outputs import Parser


class N16Shadow:
  def __init__(self, client):
    from jetlink import output_projection as projection
    if not projection.eligible(client.spec):
      raise ValueError('Expected the pinned N16 model and exact tensor layout')
    self.client = client
    self.spec = client.spec
    self.parser = Parser()

  def infer(self, warped, packed, frame_id, *, reset, deadline):
    if self.client.spec is not self.spec:
      raise ValueError('Client changed model; create a new N16 session')
    raw = self.client.infer(warped, packed, frame_id=frame_id, reset=reset, deadline=deadline)
    return self.parse(raw)

  def parse(self, raw):
    from jetlink import output_projection as projection
    if self.client.spec is not self.spec or not isinstance(raw, np.ndarray) or raw.dtype != np.float32:
      raise ValueError('Unexpected N16 output type or session')
    full = raw.shape == (self.spec.output_nelem,)
    compact = raw.shape == (projection.COUNT,) and self.client.output_projection_available and self.client.project_output
    if not (full or compact) or not np.isfinite(raw).all():
      raise ValueError('Unexpected N16 output layout or non-finite values')
    # HCOP's parser writes normalized values into its arrays; never mutate the
    # transport buffer or fabricate the graph-owned state omitted by projection.
    driving = {name: raw[None, span].copy() for name, span in self.spec.output_slices.items()
               if name not in projection.OMITTED}
    return self.parser.parse_outputs(driving)
