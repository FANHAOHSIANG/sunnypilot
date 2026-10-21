"""Model joins require fresh, stationary and disengaged vehicle state."""


def may_join(now, messages, valid, received):
  required = ('carState', 'selfdriveState', 'carControl', 'carControlSP')
  if not all(valid.get(k, False) and 0 <= now - received.get(k, -1e6) < .25 for k in required):
    return False
  cs, sd, cc, cc_sp = (messages[k] for k in required)
  return bool(cs.standstill and abs(cs.vEgo) < .01 and not sd.enabled and not sd.active
              and not cc.enabled and not cc.latActive and not cc.longActive
              and not cc_sp.mads.enabled)
