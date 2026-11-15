# FCW candidate integration status — 2026-11-16

## Not a vehicle-installable release
This update fixes actual defects in the paired controller/C safety candidate. It does not authorize public-road use, activate a vehicle mode, update live opendbc gitlinks, or flash Panda. Production refusal stays in place until complete integration and hardware evidence exist.

## Reproduced defects and fixes
1. `validate_bench_cp` previously ignored safety-only activation if the controller bit was absent. A regression reproduced failure, then the two-way pairing check passed all six pairing cases. Safety bits of other safety modes are not reinterpreted as Hyundai flags.
2. The C readiness predicate previously reported ready after valid source reception even if no host copy was authorized. Three failing regressions reproduced the problem. Readiness now additionally needs a recently authorized exact copy; discontinuity/reset revokes that history. Five transport regressions pass. Authorization is not proof of actual bus delivery.

## Executed in this session
- 15 controller helper methods: PASS.
- 6 bidirectional pairing methods: PASS (including a reproduced pre-fix failure).
- 20 native C policy methods: PASS with GCC and Clang (same methods, not counted twice).
- 5 native transport-readiness methods: PASS with GCC and Clang; 3 failed before the fix.
- 10 evidence/report gate methods: PASS.
- Total: 56 distinct local methods. These are NOT the complete modified opendbc safety suite.
- 16 unique archived RLOG files replayed through the native C policy: 48,110 source 0x160 frames, 0 CRC errors, 48,050 immediate-copy authorizations. This uses synthetic immediate-copy timing, not full controller/firmware/HIL replay. Raw driving logs are not published here.

## Full integration execution status
NOT RUN TO COMPLETION. The current execution container has Python 3.13 rather than the pinned 3.12 setup, no full opendbc checkout, and cannot download required packages. The GitHub branch had no workflow run at last check. Do not use old baseline results to mark modified software passing.

`validate_all.py` now runs the actual materialized parser/controller, all preserved safety tests under both normal and test-only candidate compilation, production-refusal tests, and full-libsafety integration cases. It records per-stage exit status, test counts, skipped counts and log hashes; an absent, zero-test, skipped targeted, failed or interrupted phase never yields a pass. Full integration tests added here are pending execution, not locally verified.

The build still lacks firmware/HIL validation and original Hyundai FCW function validation. The acceptance target is FCW coexistence; physical AEB braking is not claimed or required to be deliberately induced by the user.
