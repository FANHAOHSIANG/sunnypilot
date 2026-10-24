# Ioniq 5 FCA/AEB readiness and acceptance gates

## Current facts
- CarParams reported: HYUNDAI_IONIQ_5; CANFD_CAMERA_SCC + CANFD + EV + CANFD_DYNAMIC_TORQUE; longitudinal=True; hyundaiCanfd safetyParam=1037.
- Pinned target uses `opendbc_repo` 1c51731784729da5ebaf24b3c34106bd27aaf94b and `panda` 4230530f5ec4c7f11971f868ae9930b3547346eb.
- Camera SCC exclusion already exists for both ECU disable and periodic Tester Present, therefore copying the carrot-wip exclusion does not restore OEM AEB.
- Non-LKA longitudinal path calls `create_fca_warning_light`, where `AEB_SETTING=0x1` is commented as an AEB-disabled warning. Keep it unchanged until OEM safety status is independently established.
- Safety rules for SCC_CONTROL 0x1A0 and relevant stock-ECU conflict/relay safeguards must remain unchanged.

## Offline gates (Windows)
```powershell
cd $HOME\Desktop\sunnypilot
git fetch origin hkg-fca-aeb-preservation-lab
git switch hkg-fca-aeb-preservation-lab
git pull --ff-only
git submodule update --init panda opendbc_repo
python tools/fca_research/offline_preflight.py
python tools/fca_research/test_ioniq5_static_regression.py
```
A static PASS is *not* evidence of working FCA or AEB.

## Explicit gates before car test
- Passive bus capture comparing stock/Alpha modes on same car without obstacles or induced warning events; inspect provenance of ADRV_0x160 and SCC_CONTROL and event timing.
- Read OEM ECU health/DTC and OEM-configured FCA status; verify actual hardware and camera/radar arbitration.
- Simulated CAN and Panda Safety suites, including stock ECU conflicts, brake override, acceleration and checksum/counter failures.
- Independent safety review; controlled test facility to test FCW and AEB separately.
- No road deployment until full evidence exists. Do not hide dashboard warnings, bypass Panda rules, or disable original crash avoidance controls.

This branch is **source-validation ready only, not vehicle-test ready**.
