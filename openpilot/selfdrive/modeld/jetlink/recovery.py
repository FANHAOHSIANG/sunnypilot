"""Recovery bounds adapted from zoompilot/jetlink d24328d (MIT).

The IPC adapter never masks nonfinite outputs, transport errors or safety
faults. Holds have both a count bound and a maximum source age.
"""
from collections import deque

HOLD_FRAME = .046
PROVING_FRAMES = 20
SETTLING_FRAMES = 3
MAX_OUTPUT_AGE = .2
DROP_LIMIT = .0075


class Recovery:
  def __init__(self):
    self.failures = 0
    self.joined_at = None
    self.frames = self.consecutive = 0
    self.holds = deque()

  def joined(self, now):
    self.joined_at = now
    self.frames = self.consecutive = 0
    self.holds.clear()

  @property
  def proving(self):
    return self.joined_at is not None and self.frames < PROVING_FRAMES

  def note(self, held, now, source_at):
    self.frames += 1
    if now - source_at > MAX_OUTPUT_AGE:
      raise TimeoutError('Jetlink output too old')
    self.consecutive = self.consecutive + 1 if held else 0
    if not held:
      return
    self.holds.append(now)
    while self.holds and now - self.holds[0] > 10.:
      self.holds.popleft()
    if (SETTLING_FRAMES < self.frames <= PROVING_FRAMES or
        self.consecutive >= 5 or len(self.holds) > 20):
      raise TimeoutError('Jetlink fell behind')

  def failed(self, now):
    stable = self.joined_at is not None and now - self.joined_at > 60.
    self.failures = 1 if stable else self.failures + 1
    self.joined_at = None
    return 1. if self.failures <= 3 else min(5. * 2 ** min(self.failures - 4, 4), 60.)
