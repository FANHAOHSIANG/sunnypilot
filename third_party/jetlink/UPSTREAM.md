# Jetlink vendored runtime

Source: https://github.com/zoompilot/jetlink
Revision: `194ff6dc71cd27282378fff155e94daa7770152b` (0.3.0a1).
The v2 runtime is retained. `transport/base.py` adds a per-connection
`wire_protocol` selection hook; its default remains v2. See LICENSE.
`jetlink/protocol_v3.py` is an unmodified copy of `jetlink/protocol.py` from
zoompilot/jetlink `24e8917673829cbe1d146735956e9038cc93f227`.
PR #534 extends `protocol.py`, `client.py`, `spec.py` and `transport/base.py`
with strict per-stream v2/v3 selection, HELLO link metadata, queued/stateful
model contracts and validated full-output replies. `transport/ffs.py` reports
reader scheduling failures to the waiting consumer. The vendored server and
default protocol remain v2; these changes do not publish a new Jetson release.
`compat.py` retains the earlier pinned-model adapter for regression coverage;
the production daemon now uses the extended `JetlinkClient`.
Carrot's adapter, device-role policy, process placement and deployment tools are
outside this directory. Updating the protocol requires testing both peers.

Registry catalog.py and lfs.py updated unchanged from zoompilot/jetlink
`d24328d849b0e5154b55f149dc18c5dfe8bd79cc` for V3 and precompiled export resolution.

FunctionFS AIO port: `transport/ffs.py`, `transport/aio.py`, and the AIO/FFS
tests are based on zoompilot/jetlink `b1d704188758536e573108162d0f84c316b427d9`,
the gitlink pinned by zoompilot/zoompilot develop
`632b0b0bebd608cb4846c00cec8c9522eac514f2`.
Local adaptations retain the existing strict v2/v3 framing in base._frame,
use local USB descriptor constants, report reader startup failures, reject
zero-progress submits, and expose incomplete AIO cleanup to the daemon.
The vendor server/client protocol defaults have not been upgraded wholesale.
Tests use the upstream FakeAio explicitly for FIFO simulation; test_aio also
executes Linux AIO against real pipes. Test-only LEAVE sends use PING because
the retained v2 enum does not contain LEAVE; this does not add a wire command.
