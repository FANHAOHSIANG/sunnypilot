"""Runtime primitives adapted from zoompilot/jetlink b1d7041 warp.py (MIT).

Copyright (c) 2026-, Zeph Leggett. See third_party/jetlink/LICENSE.
The HKG warp has four inputs and its own serialized TinyJit ABI; keep that
graph, but use the upstream coherent output allocation and captured reset.
"""

COHERENT_WRITEBACK = (1 << 31) | (3 << 26)


def make_coherent(out, device):
  device.synchronize()
  mem = device._gpu_alloc(out.nbytes, flags=COHERENT_WRITEBACK)
  if mem.meta[0].flags & COHERENT_WRITEBACK != COHERENT_WRITEBACK:
    device._gpu_free(mem)
    raise RuntimeError('GPU driver refused coherent write-back memory')
  out.deallocate()
  out.allocate(opaque=mem)


def prepare_small_reset(small):
  from tinygrad import Tensor, TinyJit
  queues = tuple(t for name, t in small.input_queues.items()
                 if name.endswith('_q') or name in getattr(small, 'state_pairs', {}))
  arrays = tuple(small.npy.values())

  @TinyJit
  def clear():
    Tensor.realize(*(q.assign(0) for q in queues))

  if queues:
    for _ in range(3):
      clear()

  def reset():
    if queues:
      clear()
    small.prev_desire.fill(0)
    for array in arrays:
      array.fill(0)

  return reset
