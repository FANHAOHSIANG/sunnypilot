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

## 2026-11-03: exact 2023 Ioniq 5 safetyParam=1037 gate

The user ran the pinned `opendbc.safety.tests.test_hyundai_canfd` suite on Mac mini Python 3.12: **3835 tests, OK, 276 skipped**. This confirms the pinned C safety test suite ran, but not physical FCA/AEB behavior.

Existing source includes `TestHyundaiCanfdDynamicTorqueLFALong` parameterized with EV_GAS + CAMERA_SCC + LONG + CANFD_DYNAMIC_TORQUE; this includes the numeric 1037 combination. However, the suite alone is not proof of full coverage of OEM collision avoidance.

A dedicated **non-actuating** smoke test was added in `tools/fca_research/test_ioniq5_safety_1037.py`. Run from `sunnypilot-lab/opendbc_repo` with the separate `../.venv-fca` activated:

```bash
PYTHONPATH="$PWD" python ../tools/fca_research/test_ioniq5_safety_1037.py
```

It checks exact safety flag composition and rejection of invalid acceleration, forged TCS and wrong-bus ADAS diagnostic frames. This test has **not yet been run** on the user's Mac and must not be called passing until output is received. Do not loosen assertions or Panda checks merely to make it pass. Failure should be investigated.

**Deployment status: BLOCKED.** No control logic or Panda firmware has been modified. FCA/AEB operation still requires OEM DTC and ECU state evidence, appropriate bench simulation and professional closed-course verification.

## User-run Mac mini verification: exact 1037 test PASS (2026-11-03)

Environment: separate `.venv-fca` in `~/CarrotMacHUD/fca-research/sunnypilot-lab`, pinned `opendbc_repo` 1c51731784729da5ebaf24b3c34106bd27aaf94b, pinned `panda` 4230530f5ec4c7f11971f868ae9930b3547346eb.

User ran:
```bash
cd ~/CarrotMacHUD/fca-research/sunnypilot-lab/opendbc_repo
PYTHONPATH="$PWD" python ../tools/fca_research/test_ioniq5_safety_1037.py
```

Observed: `Ran 4 tests in 0.513s; OK`.
- Exact EV_GAS | LONG | CAMERA_SCC | CANFD_DYNAMIC_TORQUE flags = 1037.
- Out-of-range longitudinal requests rejected by compiled C safety hook.
- Unwhitelisted TCS transmission rejected.
- Wrong-bus ADAS UDS transmission rejected.

Previously user ran upstream `opendbc.safety.tests.test_hyundai_canfd`: `Ran 3835 tests in 26.150s; OK (skipped=276)`. This is **not** 3835 tests of exact 1037, and the four new smoke tests are **not** proof of OEM AEB operation.

**Remaining blockers:** compare OEM ECU status/DTC under stock and Alpha, identify the source and meaning of 0x160 AEB_SETTING and OEM FCA availability states with variant-correct DBC, verify stock ECU conflicts and bus forwarding under actual 1037 in a broader test matrix, and perform independent closed-course AEB/FCW validation. Keep research branch non-deployable; no warning masking, safety hook relaxation, or ECU disable changes.

## 2026-11-03: 0x160 AEB_SETTING raw signal audit (no control changes)

The pinned carrot-wip and sunnypilot DBCs both define `ADRV_0x160.AEB_SETTING` at `24|2@1+`. User-provided route `0000004f` segment 0 CAN inventory included:
- openpilot `sendcan` bus 0 example `2d84000100000002fffc090000000000`: raw AEB_SETTING = **1**.
- `can` bus 2 example `41bab00300000002fffc010000000000`: raw AEB_SETTING = **3**.
These two frames are not time-aligned, and bus provenance / OEM enum meanings are unverified. They **cannot** establish AEB functionality or explain the yellow warning alone.

`tools/fca_research/aeb_setting_audit.py` now tallies 0x160 raw values by direction, bus, and second across archived RLOG segments, with no CAN output or controller modification. Its companion `test_aeb_setting_audit.py` tests the bitfield and RX/TX separation using synthetic examples. Both are committed, but the new tests and actual multi-segment audit are **not yet reported as run**.

On Mac mini, in the isolated `sunnypilot-lab`:
```bash
cd ~/CarrotMacHUD/fca-research/sunnypilot-lab
git pull --ff-only
python3 tools/fca_research/test_aeb_setting_audit.py
```
For RLOG decoding use an environment with the pinned `openpilot.tools.lib.logreader` and its dependencies (e.g. the existing C4 environment, parked, analyzing local archived files only):
```bash
PYTHONPATH=/data/openpilot:/data/openpilot/opendbc_repo python3 /tmp/aeb_setting_audit.py \
  /data/media/0/realdata/0000004f--858ff9d82b--0/rlog.zst \
  --output /tmp/ioniq5_aeb_setting.json
```
Fetch the audited script from the branch to `/tmp/aeb_setting_audit.py` before this invocation. Do not deploy any experimental controls, suppress warnings, or induce collision warnings on public roads.

## 2026-11-05 new carrot route reported as AEB intervention

User supplied route `00000384--8d2511ca94`, segments 0 and 1, device Ioniq 5, branch `test/iphone-led-bridge-20261105`, commit `a825bccd` (2026-11-05 16:52:04). User reports that an AEB intervention occurred in this route. The public upload index links are not directly accessible from this environment; the two raw RLOGs have **not** been decoded here. Do not treat the route's `OK` upload status as AEB evidence.

Source inspection of `ajouatom/openpilot@a825bccd` `opendbc_repo/opendbc/car/hyundai/carstate.py` lines ~668-680: `stockAeb`/`stockFcw` are set inside a branch requiring `not openpilotLongitudinalControl or CAMERA_SCC`. A CAN FD car using `CANFD_CAMERA_SCC` with Alpha longitudinal and without the distinct `CAMERA_SCC` flag may **not populate** these fields. A missing `stockAeb` in RLOG is therefore NOT evidence AEB did not occur. Conversely a positive flag would be evidence of decoded stock AEB state, not independent proof of brake actuator force.

New read-only `tools/fca_research/route_aeb_event_inventory.py` can inventory `carState.stockAeb`, `stockFcw`, event names, and logged CarParams from locally present segments. Compare with the existing `aeb_setting_audit.py` on those same logs, then correlate time with user-observed intervention and OEM DTC/ECU data. No controller or Panda Safety modifications are authorized.

## 2026-11-05 carrot route 00000384 segment 0: decoded CAN evidence

The user uploaded a 12.8 MB zstd RLOG, with 53,898,928 bytes decompressed, containing 87,143 Cap'n Proto messages. Local read-only parsing validated the entire concatenated message stream and extracted CAN frames without a live vehicle connection. Embedded log metadata identifies `test/iphone-led-bridge-20261105`, commit `a825bccd`, and log rotation to `00000384--8d2511ca94--1` (indicating this file is segment 0).

Results for this **one 60-second segment**:
- `ADRV_0x160`: RX bus 2 **3001 frames**, all `AEB_SETTING=3`. The mirrored bus 128 frames should **not** be counted as a separate ECU. No `sendcan` 0x160 frames.
- `TCS (0x175)`: RX bus 0 **3001 frames**, all `FCA_EquipSta=1` and `FCA_AvlblSta=0`; TX bus 2 **2993 frames** with the same values. Bus 130 mirrors bus 0.
- `SCC_CONTROL (0x1A0)`: TX bus 0 **2993 frames**, RX bus 2 **3002 frames**. Bus 128 also mirrors received 0x1A0.
- The pinned carrot-wip DBC at `a825bccd` labels `AEB_SETTING=3` as `active assist`, `FCA_EquipSta=1` as equipped, and `FCA_AvlblSta=0` as available. These are **DBC label interpretations**, not independent OEM functional validation.
- Wheel speed `WHEEL_SPEEDS (0xA0)` was 0–7.64 km/h; ESP_STATUS brake pressure varied 0–213 raw units and the driver brake-pressed flag was sometimes true. These facts **cannot attribute braking to OEM AEB**. A low-speed brake pressure change may have several causes, including driver brake/auto-hold.
- No 0x730/0x7D0 frames were observed in the segment; this does not prove no ECU diagnostics in other segments.
- Carrot source `carstate.py` has a guard which may leave `stockAeb` unset in CANFD_CAMERA_SCC Alpha longitudinal mode. A missing stockAeb event is not negative evidence.

**New key comparison:** Earlier sunnypilot route 0000004f segment 0 had openpilot TX 0x160 with raw `AEB_SETTING=1`; this carrot segment has OEM RX 0x160 raw `3` and no TX 0x160. This supports a concrete difference in **warning/configuration signaling**, but does not prove the OEM brake actuator was enabled or triggered.

**Next:** obtain 00000384 segment 1 and timestamp of the driver's observed event, correlate speed, brake pressure, TCS and OEM status; read OEM FCA DTC/ECU health and professionally validate actual AEB. Do not remove 0x160 warnings, forge availability status, relax Panda Safety, or deploy unvalidated changes to public roads.

### Additional code-path correlation (a825bccd)

Carrot `carcontroller.py` lines ~599-606 sends `create_acc_control_scc2()` **and** `create_tcs_messages()` when `CP.flags & HyundaiFlags.CAMERA_SCC` is true. Carrot `create_fca_warning_light(CP,...)` returns without TX when `CAMERA_SCC` is set. The simultaneous observed TX `SCC_CONTROL` bus 0 and TX `TCS` bus 2 at ~50 Hz, with no TX `ADRV_0x160`, is therefore **strongly consistent with the carrot CAMERA_SCC-specific control path**. This contrasts with the previously reported sunnypilot CarParams `CANFD_CAMERA_SCC` but not `CAMERA_SCC`. Exact carrot CarParams for this route should be recovered before asserting the flag as proven.

Do not copy carrot's SCC2/TCS frame rewrites to sunnypilot without variant-specific DBC, stock ECU arbitration, and full safety testing.

## Correction and verification: 00000384 segment **1**, not 0 (2026-11-06)

The user uploaded `00000384--8d2511ca94--1--rlog.zst`, SHA-256 `0d9cc06f2c9ef8b21eee1149067711e52670e68a1fba0f45eebfe8a8337049`. It is byte-for-byte identical to the earlier upload titled `rlog.zst 10`. **The earlier analysis erroneously called that upload segment 0; all earlier counts attributed to segment 0 belong to segment 1.** Segment 0 is not yet verified from a distinct file.

A read-only pure-Python Cap'n Proto message framing/list parser (without external packages) independently decoded the uploaded 53,898,928-byte decompressed stream, 87,143 messages, and obtained:
- `can` bus 2 `ADRV_0x160`: **3,001**, all raw `AEB_SETTING=3` (DBC `VAL_ 352` labels 3 as `active assist`); no `sendcan` `0x160`.
- `can` bus 0 `TCS (0x175)`: **3,001**, all `FCA_EquipSta=1`, `FCA_AvlblSta=0` (DBC labels equipped and available). `sendcan` bus 2 TCS: **2,993**, same values. Bus 128/130 echoes should not be double counted as independent OEM messages.
- `can` bus 2 `SCC_CONTROL (0x1A0)`: **3,002**; `sendcan` bus 0 `SCC_CONTROL`: **2,993**.
- `can` bus 0 `WHEEL_SPEEDS (0xA0)`: observed average wheel speed **0–7.64 km/h** in the segment.
- `can` bus 0 `ESP_STATUS (0x60)`: `BRAKE_PRESSURE` raw **0–213**. Distinct braking-related intervals at approximately 13–16 seconds (peak raw 155), 21–25 seconds (peak 213), and 26 seconds (raw 151). Some intervals include driver-brake state asserted, so brake pressure is **not** proof of OEM AEB actuation.
- No `0x730` or `0x7D0` frames in this segment.

**Status:** OEM FCA status bytes and lack of TX `0x160` are concrete, but do not establish physical AEB intervention. User reports AEB triggered on this route; this is not yet independently confirmed. The strongest next evidence is the *distinct* segment 0 RLOG, event timestamp, and OEM ECU/DTC plus brake actuation evidence.

Do not copy or deploy `create_tcs_messages`, `create_acc_control_scc2`, or altered Panda Safety to public-road use without a qualified safety review and closed-course testing.

### Direct re-decode of earlier sunnypilot route for side-by-side (2026-11-06)

Independently re-decoded the uploaded `99411088c9cfd064_0000004f--858ff9d82b--0--rlog.zst` (101,306 Cap'n Proto messages) with the same read-only CAN extraction used above:

| CAN observation | sunnypilot 0000004f seg 0 | carrot 00000384 seg 1 |
| --- | --- | --- |
| RX 0x160 bus 2, raw AEB_SETTING | 3,047 frames, all **3** | 3,001 frames, all **3** |
| TX 0x160 bus 0, raw AEB_SETTING | 2,486 frames, all **1** | **0** frames |
| RX TCS bus 0, FCA_EquipSta / FCA_AvlblSta | 3,049 frames, all **1 / 0** | 3,001 frames, all **1 / 0** |
| TX SCC_CONTROL bus 0 | 2,486 frames | 2,993 frames |
| Mean wheel speed range | 0–0.039 km/h (nearly parked) | 0–7.64 km/h |
| ESP_STATUS BRAKE_PRESSURE | 0 throughout | 0–213 raw |

The two segments differ in driving state and are not a matched on-road A/B test. In particular, **both** show received 0x160 raw=3 and FCA availability 0; the strongest differentiator is sunnypilot's **additional** outgoing raw=1 0x160 frame. No physical AEB function can be inferred from either segment. DBC enum labels are not independently verified against Hyundai service documentation.

## 2026-11-06 user clarification: FCW, **not** AEB actuation

**Authoritative firsthand correction:** For carrot-wip route `00000384--8d2511ca94`, the driver observed **only an audible forward-collision warning and a red forward-collision visual warning**. The driver **did not report automatic emergency braking**. Earlier descriptions of this route as an "AEB intervention" were incorrect; relabel it as a **reported FCW event** throughout future conclusions.

What is supported:
- The user's firsthand FCW observation is consistent with a functioning warning path in the tested carrot-wip setup.
- Segment 1 contains OEM 0x160 raw AEB_SETTING=3 and TCS FCA_AvlblSta=0, with no outgoing 0x160; sunnypilot comparison shows outgoing raw=1 0x160. These are configuration/communication observations.
- No recorded or firsthand evidence currently demonstrates **physical AEB actuation**. Brake pressure changes cannot be attributed to AEB, especially with driver braking/low-speed operation.
- The available RLOG may not include the exact FCW moment. Correlate the user's event time with logs before attributing it to stock FCW versus openpilot FCW.

**Deployment gate remains blocked** for claims of OEM AEB preservation. Do not deliberately induce an FCW or AEB event on public roads. Do not suppress warning messages, forge OEM availability, or weaken Panda Safety.

## 2026-11-06: Distinct 00000384 segment 0 recovered and analyzed

User uploaded a new `rlog.zst 12` file, SHA-256 `c823be18d733f09b60b71114513d9068eab3b01cd02d95e76fe640ee93fe1b72`, **distinct** from the prior segment 1 SHA-256 `0d9cc06f2c9ef8b21eee1149067711e52670e68f6a1fba0f45eebfe8a8337049`. Zstandard integrity passed; decompressed 54,007,792 bytes, 86,697 Cap'n Proto messages. CAN monotonic time range **50.922–112.192 seconds**, immediately followed by segment 1 CAN time range **112.203–172.053 seconds**. This establishes that the new file is the preceding segment **0**. Both are from `test/iphone-led-bridge-20261105`, commit `a825bccd`.

Segment 0 independent passive CAN decode:
- OEM `ADRV_0x160` RX bus 2: **3,065** frames, all raw `AEB_SETTING=3`. During a short startup/bridge period, **349** additional frames appeared on bus 0 with raw=3; avoid counting mirrored/forwarded traffic as distinct OEM ECUs. **No TX 0x160**.
- OEM `TCS (0x175)` RX bus 0: **3,066** frames, all `FCA_EquipSta=1`, `FCA_AvlblSta=0`. There were **349** additional RX bus 2 frames with the same values during bus transition; `sendcan` bus 2 TCS: **2,658** frames, with these values.
- `SCC_CONTROL (0x1A0)` RX bus 2: **3,064** frames; TX bus 0: **2,659** frames.
- Wheel speeds from RX bus 0 `WHEEL_SPEEDS (0xA0)`: max mean wheel speed **13.875 km/h**; `ESP_STATUS (0x60)` raw brake pressure max **114**, including ~22–23 seconds with speed near zero. No positive brake-pedal sample in those specific two seconds, but this is **not** evidence of AEB: automatic hold, normal SCC and other causes remain possible.
- No `ADRV_0x161` frames seen in this segment's watched CAN traffic. This does **not** prove the source of the driver's reported audible/red FCW alert.
- Segment 1 remains as previously documented: RX 0x160 bus 2 = 3,001 raw=3; RX TCS bus 0 = 3,001 equip=1, available=0; TX 0x160 = 0; TX SCC_CONTROL bus 0 = 2,993; max wheel speed 7.64 km/h.

**Combined result:** Across both segments, RX bus 2 0x160 raw=3 totals **6,066** frames, RX bus 0 TCS equip=1/available=0 totals **6,067**, TX 0x160 totals **zero**. Carrot-wip's FCW warning is **user-observed only**; neither source of the red UI warning nor physical OEM AEB actuation has been independently proven. The sunnypilot 0000004f comparison remains an unmatched parked-state segment. No deployment authorization for experimental AEB/longitudinal coexistence.

## 2026-11-06: vehicle-mode provenance gate added (no actuation)

Added `tools/fca_research/vehicle_fca_provenance.py` plus synthetic tests. It reads archived RLOGs and reports:
- actual **CarParams.flags** (if a `carParams` event is recorded), and independently the **safetyConfigs.safetyParam**; the `CAMERA_SCC` vehicle flag must not be inferred solely from the similarly named Panda safety flag;
- whether Alpha longitudinal was enabled in the recorded CarParams;
- raw RX/TX `ADRV_0x160.AEB_SETTING`, `TCS.FCA_EquipSta/FCA_AvlblSta`, and SCC_CONTROL counts by bus;
- optional `stockFcw`/`stockAeb` and openpilot FCW alert events, with caveat that CAN FD Alpha can leave stock warning fields unpopulated.

The central unresolved fork is whether carrot's observed no-TX-0x160 behavior is explained by the **vehicle flag** `HyundaiFlags.CAMERA_SCC` and a different SCC2/TCS control path, versus another controller condition. Neither source code nor the Panda safetyParam=1037 proves which vehicle flags were active in the carrot route.

This is a **read-only diagnostic gate**, not an FCA/AEB control fix. No live ECU/Panda changes. Unit tests are committed but have not yet been verified running on the user's Mac.

## 2026-11-06: decisive CarParams and cross-fork flag alias discovery

The user **successfully ran** standalone_carparams.py on actual carrot-wip route `00000384--8d2511ca94--1`, with **one** decoded CarParams event, **zero** errors:
```json
{"carFingerprint":"HYUNDAI_IONIQ_5","flags":402663433,"openpilotLongitudinalControl":true,"safetyConfigs":[{"model":"hyundaiCanfd","param":6173}],"logMonoTime":152388625295}
```

**Critical source-verified difference:** carrot-wip `ajouatom/openpilot@a825bccd` `opendbc_repo/opendbc/car/hyundai/values.py` defines `CAMERA_SCC = CANFD_CAMERA_SCC` (both bit 3). Its `create_fca_warning_light(CP,...)` returns without transmitting `ADRV_0x160` if `CP.flags & HyundaiFlags.CAMERA_SCC`. This **exactly explains** why the real carrot route (flags include bit 3) has zero TX 0x160.

In pinned sunnypilot `TonyBinheWu/opendbc@1c51731784729da5ebaf24b3c34106bd27aaf94b`, `CANFD_CAMERA_SCC = 2**3` while `CAMERA_SCC = 2**15` (separate flags). Its `create_fca_warning_light()` does not receive CP and always transmits 0x160 raw=1 every other frame when invoked. Previous sunnypilot CarParams flags=134227976 include bit 3 but not bit 15. This explains the observed **software-side TX divergence**, not necessarily physical AEB behavior.

Carrot raw flags `402663433 = 0x18002809` bits `[0,3,11,13,27,28]`, per carrot source:
- bit 0 `CANFD_HDA2`
- bit 3 `CANFD_CAMERA_SCC` and alias `CAMERA_SCC`
- bit 11 `EV`
- bit 13 `CANFD`
- bit 27 `CANFD_CLUSTER_DIRECT_TX`
- bit 28 `CANFD_SPAS_BLINKERS`.

Sunnypilot raw flags `134227976 = 0x08002808` bits `[3,11,13,27]`, per sunnypilot source:
- bit 3 `CANFD_CAMERA_SCC`
- bit 11 `EV`
- bit 13 `CANFD`
- bit 27 `CANFD_DYNAMIC_TORQUE`.

**Do not compare raw flag bit numbers across forks as equivalent meanings:** bit 27 is a *different feature* in each fork.

Carrot Panda safetyParam `6173 = 0x181D` bits `[0,2,3,4,11,12]`: EV_GAS, LONG, CAMERA_SCC, CANFD_LKA_STEERING, CANFD_CLUSTER_DIRECT_TX, CANFD_SPAS_BLINKERS. Sunnypilot `1037 = 0x040D` bits `[0,2,3,10]`: EV_GAS, LONG, CAMERA_SCC, CANFD_DYNAMIC_TORQUE. These are substantially different Panda configurations. The carrot `CANFD_HDA2` bit and safety `CANFD_LKA_STEERING` additionally indicate an HDA2-style steering pathway, which must not be transplanted into the sunnypilot configuration without vehicle-specific review.

**Status:** root cause of **outgoing AEB_SETTING=1 difference** is now source-backed and matched to actual CarParams, but OEM AEB braking is still **unverified**. No deployment, warning suppression, or Panda safety modification is approved. An offline-only code-path assertion test is appropriate as next step.
