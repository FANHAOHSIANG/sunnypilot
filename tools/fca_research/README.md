# Ioniq 5 FCA/AEB offline test gate

**Development gate only; not an on-road or AEB-function approval.** This branch intentionally does not alter ECU disable behavior, CAN TX, SCC arbitration, FCA warning messages, or Panda limits.

## Windows PowerShell (offline/source-only)
From `C:\Users\aqwer\Desktop\sunnypilot`:

```powershell
git fetch origin hkg-fca-aeb-preservation-lab
git switch hkg-fca-aeb-preservation-lab
git submodule update --init panda opendbc_repo
python tools/fca_research/offline_preflight.py
python tools/fca_research/hyundai_safety_audit.py
```

The branch switch changes **only this Windows source checkout**; it does not install software to C4. Do not execute these commands inside a live C4 checkout. If this checkout has local modifications, stash/commit them first rather than force switching.

Source preflight verifies the specific target gitlink revisions and searches for crucial safety-related source conditions. It fails closed when versions or expected source tokens differ. Even PASS means **only source files verified**, NOT working FCA/AEB.

## Follow-on requirements (not yet satisfied)
1. Obtain actual Ioniq 5 CarParams (without secrets), CAN FD/HDA2/Camera SCC flags, safetyParam.
2. Passive capture: inspect OEM ECU DTC and SCC/FCA messages with Alpha Longitudinal on/off, no obstacle provocation.
3. Validate stock ECU arbitration and Panda RX/TX restrictions on bench/simulator.
4. Preserve acceleration checks, relay detection, warning signals and minimum-risk disengagement.
5. Confirm both FCW and AEB separately with qualified test facility closed-course procedure. No live deployment before this gate.

Do not use warning-lamp disappearance as a success signal.
