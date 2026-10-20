#!/usr/bin/env bash
# Read-only C4 status; no keys, tokens, USB role changes or process restarts.
set -u
root="$(cd "$(dirname "$0")/../.." && pwd)"
git -C "$root" log -1 --format='software: %h %s'
for path in /dev/shm/sunnypilot-jetlink.json /dev/shm/sunnypilot-jetlink-model.json /dev/shm/sunnypilot-jetlink-fault \
            /dev/shm/jetlink-gadget /sys/class/usbpd/usbpd0/current_dr /sys/class/usbpd/usbpd0/current_pr \
            /sys/class/power_supply/usb/typec_mode /sys/class/power_supply/usb/typec_cc_orientation; do
  echo "$path"
  if [[ -r "$path" ]]; then cat "$path"; echo; else echo '(unavailable)'; fi
done
for path in /sys/class/udc/*/state /sys/class/udc/*/current_speed; do
  [[ -r "$path" ]] || continue
  echo "$path"
  cat "$path"
done
