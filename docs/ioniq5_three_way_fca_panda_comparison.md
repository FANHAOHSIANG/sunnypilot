# Three-way Hyundai FCA/FCW/AEB investigation — no actuation changes

**Status: ANALYSIS ONLY — NEVER DEPLOY TO VEHICLE.**
Branch: `hkg-fca-aeb-preservation-lab`, forked from `hkg-enhanced-jetlink`.

## Findings from source files (2026-10-22)
### sunnypilot v0.9.7.1 (2024 release)
[interface.py](https://github.com/sunnypilot/sunnypilot/blob/v0.9.7.1/selfdrive/car/hyundai/interface.py), around lines 191–197:
- ECU disable is excluded when `CANFD_CAMERA_SCC`, `SP_ENHANCED_SCC` or a `CAMERA_SCC_CAR` platform condition applies.
- Remaining OP-long cases send UDS 0x28 communication control to 0x7d0/bus0 or HDA2 0x730/ECAN.
[carcontroller.py](https://github.com/sunnypilot/sunnypilot/blob/v0.9.7.1/selfdrive/car/hyundai/carcontroller.py), around lines 198–206:
- Periodic Tester Present has analogous exclusions involving `CANFD_CAMERA_SCC`, ESCC, and `CAMERA_SCC_CAR`.
[hyundaicanfd.py](https://github.com/sunnypilot/sunnypilot/blob/v0.9.7.1/selfdrive/car/hyundai/hyundaicanfd.py), around lines 175–194:
- `create_adrv_messages` constructs `ADRV_0x160` with `AEB_SETTING=1` (commented as an AEB disabled indicator). The absence of an icon on the user's vehicle is not proof of functioning AEB.

### carrot-wip
[interface.py](https://github.com/ajouatom/openpilot/blob/carrot-wip/opendbc_repo/opendbc/car/hyundai/interface.py), around lines 275–280:
- `disable_ecu` exclusion: `CANFD_CAMERA_SCC` or `bosch_passive`; HDA2 target 0x730.
[carcontroller.py](https://github.com/ajouatom/openpilot/blob/carrot-wip/opendbc_repo/opendbc/car/hyundai/carcontroller.py), around lines 532–538:
- Tester Present exclusion tests `CANFD_CAMERA_SCC` only, not `bosch_passive`: **the two guards differ**.
[hyundaicanfd.py](https://github.com/ajouatom/openpilot/blob/carrot-wip/opendbc_repo/opendbc/car/hyundai/hyundaicanfd.py), around lines 639–650:
- `create_fca_warning_light` has a `CAMERA_SCC` exclusion; otherwise emits `ADRV_0x160` with `AEB_SETTING=1`.

### hkg-enhanced-jetlink
[.gitmodules](https://github.com/FANHAOHSIANG/sunnypilot/blob/hkg-enhanced-jetlink/.gitmodules):
- Pinned submodule paths: `opendbc_repo` -> `TonyBinheWu/opendbc`; `panda` -> `sunnyhaibin/panda`.
- **Exact pinned gitlink SHAs not yet retrieved**; reading TonyBinheWu/opendbc default branch is NOT proof of the checked-out target code.
- Therefore no supported claim yet of exact Panda Safety changes between target and the two reference builds.

## Safety comparison required prior to code patches
1. Resolve each exact submodule SHA from target's git tree. Compare against 0.9.7.1's pinned Panda (old code) and carrot-wip's actual safety submodule.
2. For the Ioniq 5 log, inspect CarParams for `CANFD_HDA2`, `CANFD_CAMERA_SCC`, `CAMERA_SCC`, `SP_ENHANCED_SCC`, `BOSCH_RADAR`, `openpilotLongitudinalControl`, safetyParam.
3. Compare ECU disable and periodic Tester Present **as a pair**; single-function porting would be invalid.
4. Compare TX allowlists, longitudinal acceleration limits, gas/brake conflicts, stock ECU detection, checksum/counter and relay behavior of Panda Safety. No bypass of safety checks.
5. Compare `SCC_CONTROL` frame IDs/buses/timing and FCA/ADAS messages (especially `ADRV_0x160`), with offroad passive captures.
6. Log FCW and AEB separately. A previous FCW alert does not demonstrate emergency automatic braking.
7. Never suppress the FCA indicator solely for cosmetics, or attempt collision provocations. Only pursue bench/simulation validation first and qualified controlled closed-track validation before authorizing vehicle use.

**No actuation, ECU-disable, CAN TX, or Panda Safety code was changed.**

## Verified correction: ESCC flag coherence and CAN FD (2026-10-25)
Pinned source: [escc.py](https://github.com/TonyBinheWu/opendbc/blob/1c51731784729da5ebaf24b3c34106bd27aaf94b/opendbc/sunnypilot/car/hyundai/escc.py): `EnhancedSmartCruiseControl.enabled` returns `CP_SP.flags & HyundaiFlagsSP.ENHANCED_SCC`. In this version controller `self.ESCC.enabled` and interface `CP_SP.flags & ENHANCED_SCC` therefore read the same flag; previous suspicion of a discrepancy was not established.

Pinned [interface.py](https://github.com/TonyBinheWu/opendbc/blob/1c51731784729da5ebaf24b3c34106bd27aaf94b/opendbc/car/hyundai/interface.py) `_get_params_sp`: `ENHANCED_SCC` is set based on `ESCC_MSG=0x2AB` only inside `if not stock_cp.flags & HyundaiFlags.CANFD`. Thus old CAN-only ESCC preservation cannot simply be ported to CAN FD Ioniq 5.

Pinned [hyundai_canfd.h](https://github.com/TonyBinheWu/opendbc/blob/1c51731784729da5ebaf24b3c34106bd27aaf94b/opendbc/safety/modes/hyundai_canfd.h): SCC_CONTROL 0x1A0 is checked for longitudinal acceleration, and different steering configurations use different CAN buses; 0x160 appears in allowlists and relay checks. None of these demonstrate functional AEB.

**Disposition:** no ECU-disabling, warning suppression, or Panda transmit-rule changes are justified by current evidence. Gather real Ioniq 5 CarParams, passive bus observations, and inspect camera-SCC arbitration before writing an actuation patch. DO NOT DEPLOY.

## Actual user Ioniq 5 CarParams decoded (2026-10-25)
Source: user-reported on-device `HYUNDAI_IONIQ_5`, `openpilotLongitudinalControl=True`, `flags=134227976`, `safetyModel=hyundaiCanfd`, `safetyParam=1037`, `pcmCruise=False`, `radarUnavailable=False`.

According to pinned `opendbc/car/hyundai/values.py`:
- Flags set: `CANFD_CAMERA_SCC=8`, `EV=2048`, `CANFD=8192`, `CANFD_DYNAMIC_TORQUE=134217728`.
- `CANFD_LKA_STEER_MSG` is NOT set, so this is not the 0x730 ADAS/LKA pathway.
- Safety param 1037 = `EV_GAS=1` + `LONG=4` + `CAMERA_SCC=8` + `CANFD_DYNAMIC_TORQUE=1024`.
- Pinned `interface.py` skips `disable_ecu()` when `CANFD_CAMERA_SCC` is set. Pinned `carcontroller.py` skips longitudinal Tester Present in that case as well. Do not attribute this vehicle's FCA indicator to these specific ECU-disable routines without contrary evidence.
- **Priority new candidate cause:** pinned `carcontroller.py` calls `create_fca_warning_light()` on CAN FD non-LKA path whenever `openpilotLongitudinalControl` is true; pinned `hyundaicanfd.py` emits `ADRV_0x160` with `AEB_SETTING=1`, with source comment `show AEB disabled icon`. This is a credible *warning-light signaling hypothesis*, NOT proof of AEB functionality either way.
- Earlier carrot-wip source has `create_fca_warning_light(CP,...)` with a `CAMERA_SCC` guard. `CAMERA_SCC` and `CANFD_CAMERA_SCC` are distinct flags, so the exact user car path must be traced before attempting any patch.
- **Do not suppress or alter warning signaling to create a false impression that AEB is active.** Confirm OEM ECU status and AEB independently before any actuation/vehicle deployment.

## Actual route 0000004f segment 0 passive CAN inventory (2026-10-25)

User ran `tools/fca_research/compare_logged_can.py` against `/data/media/0/realdata/0000004f--858ff9d82b--0/rlog.zst`. The inventory returned **17,171 watched frames**.

- sendcan bus 0: `ADRV_0x160` 2,486 frames; `SCC_CONTROL (0x1A0)` 2,486 frames.
- can bus 0: `ADRV_0x160` 542; `SCC_CONTROL` 543.
- can bus 2: `ADRV_0x160` 3,047; `SCC_CONTROL` 3,046.
- can bus 128: `ADRV_0x160` 2,485; `SCC_CONTROL` 2,485. These are not evidence of an additional physical OEM bus/ECU without Panda source interpretation.
- can bus 192: 2 frames each for `ADRV_0x160` and `SCC_CONTROL`.
- sendcan `0x730` bus 0 4 / bus 1 5; sendcan `0x7D0` bus 0 5 / bus 1 6. Examples `02 3e 00...` and `03 22 f100...` are diagnostic session/query traffic, not evidence of a `28 83 01` ECU-disable command or `3e 80` keep-disabled message in this inventory.

Sample `sendcan` 0x160 payload ends `...02fffc0900...`, whereas one `can` bus-2 sample ends `...02fffc0100...`. These are different bytes, **not yet DBC-decoded AEB states**. Direction/bus and time should be matched before attributing to ECU behavior.

Pinned target `carcontroller.py` invokes `create_fca_warning_light` on CAN FD non-LKA longitudinal; `hyundaicanfd.py` sets `AEB_SETTING=1` and sends `ADRV_0x160`. Actual outgoing 0x160 in the user log supports that this transmit path is active.

Crucial: carrot-wip's `create_fca_warning_light(CP,...)` skips only `HyundaiFlags.CAMERA_SCC`; the user's flag is `CANFD_CAMERA_SCC`, which is **not identical**. Simply transplanting this condition would not avoid the output in this specific configuration. No OEM AEB claim is warranted.

**Next safe verification:** timestamp-aligned passive review of OEM FCA/DTC status and both RX/TX 0x160, then qualified bench/closed-course confirmation of AEB. Do not remove or suppress warning messages solely to clear the lamp. This document contains no control-plane edits.
