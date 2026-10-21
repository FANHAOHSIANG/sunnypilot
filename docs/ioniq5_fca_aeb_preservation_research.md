# Ioniq 5 CAN FD FCA/FCW preservation — research only

Status: **NOT APPROVED FOR VEHICLE DEPLOYMENT**. This branch starts from `hkg-enhanced-jetlink`. It must not be used as proof that factory AEB remains functional.

## Observed behavior
- User reports amber FCA warning with Alpha longitudinal in sunnypilot; warning clears with Alpha longitudinal off.
- User reports FCW alert while using carrot-wip and openpilot longitudinal.
- FCW activation is **not evidence that automated emergency braking (AEB) remains functional**.

## Confirmed source inspection
Source: [carrot-wip Hyundai interface](https://github.com/ajouatom/openpilot/blob/carrot-wip/opendbc_repo/opendbc/car/hyundai/interface.py)
- `init` calls `disable_ecu` while OP longitudinal is enabled, except if `CANFD_CAMERA_SCC` or `bosch_passive` is true.
- For CAN FD HDA2, the target is 0x730 on ECAN; otherwise initialization defaults to 0x7d0/bus 0.
- `bosch_passive` requires `BOSCH_RADAR` flag and `EnableRadarTracks > 0`.

Source: [carrot-wip Hyundai controller](https://github.com/ajouatom/openpilot/blob/carrot-wip/opendbc_repo/opendbc/car/hyundai/carcontroller.py)
- Every 100 frames, controller sends Tester Present to radar/ADAS ECU whenever OP longitudinal is active and `CANFD_CAMERA_SCC` is not set. **This condition does not include the `bosch_passive` exception**; investigate carefully before interpreting the init exception as preserving radar.
- Camera SCC branch uses `create_acc_control_scc2` and TCS forwarding; non-camera path uses `create_acc_control`.

Source: [carrot-wip CAN FD messages](https://github.com/ajouatom/openpilot/blob/carrot-wip/opendbc_repo/opendbc/car/hyundai/hyundaicanfd.py)
- `create_fca_warning_light` emits `ADRV_0x160` with `AEB_SETTING=1` (comment: 'show AEB disabled icon') if *not* `CAMERA_SCC`.
- This is instrument cluster signaling and must **never** be changed merely to hide a warning.

Target: [FANHAOHSIANG/sunnypilot .gitmodules](https://github.com/FANHAOHSIANG/sunnypilot/blob/hkg-enhanced-jetlink/.gitmodules)
- Target's vehicle code and Panda safety are submodules: `opendbc_repo` points to `TonyBinheWu/opendbc` and `panda` points to `sunnyhaibin/panda`. Exact pinned gitlinks/commits must be resolved before an accurate line-by-line target comparison.
- Existing Jetlink/model switching must remain unchanged.

## Blockers before any implementation
1. Resolve exact gitlink commit IDs of `opendbc_repo` and `panda` on `hkg-enhanced-jetlink`; inspect pinned code, not HEAD.
2. Identify actual vehicle flags from logged CarParams: CANFD, CANFD_HDA2, CANFD_CAMERA_SCC, CAMERA_SCC, BOSCH_RADAR, openpilotLongitudinalControl.
3. Compare SCC_CONTROL, ADRV_0x160, TCS, camera forwarding, UDS 0x28 / Tester Present and bus/addresses across pinned target and carrot-wip.
4. Compare Hyundai safety hooks and relay/stock ECU protections in pinned Panda; do not bypass Panda safety or disable stock ECU safety checks.
5. Check original warning and FCW events by **passive** log review, DTC readout and service documentation. Do not provoke collisions or brake tests on public roads.
6. Only consider separately reviewed limited patch after confirming compatible ECU and safety architecture. Require bench/simulation regression tests and qualified closed-course verification of both FCW and AEB.

No vehicle control code has been modified by this research note.
