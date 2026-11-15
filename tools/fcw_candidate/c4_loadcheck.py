#!/usr/bin/env python3
"""Install and load the FCW candidate in an isolated C4 directory.

NOT an openpilot branch installer. Does not replace /data/openpilot, launch
manager/card/pandad, write Params, open a Panda, or build/flash Panda firmware.
Only the pinned candidate's host-side, memory-only tests are run. No pip install.
"""
from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time

CANDIDATE_SHA = "0afd378aecc549834870422fd936c64054d36eaf"
BASE_SHA = "1c51731784729da5ebaf24b3c34106bd27aaf94b"
CANDIDATE_REPO = "https://github.com/FANHAOHSIANG/sunnypilot.git"
BASE_REPO = "https://github.com/TonyBinheWu/opendbc.git"
ROOT = Path("/data/fcw-loadcheck")
LIVE = Path("/data/openpilot")
ONROAD = Path("/data/params/d/IsOnroad")


class CheckFailure(RuntimeError):
    pass


def require_offroad(path: Path = ONROAD) -> None:
    try:
        value = path.read_bytes().strip()
    except OSError as exc:
        raise CheckFailure("無法確認 C4 非行車狀態；未啟動測試。" + str(exc)) from exc
    if value != b"0":
        raise CheckFailure("C4 尚在行車模式或狀態不明。請車輛熄火、C4 保持供電，回到非行車畫面再執行。")


def require_destination(path: Path, root: Path = ROOT, live: Path = LIVE) -> None:
    # A pre-existing symlink must not redirect a test installation to live code.
    if root.is_symlink() or root.resolve() != root.absolute():
        raise CheckFailure("測試根目錄不能是指向其他位置的連結")
    target, active = path.resolve(), live.resolve()
    if target == active or active in target.parents or target in active.parents:
        raise CheckFailure("拒絕寫入正在使用的 openpilot 目錄")
    if target == root.resolve() or root.resolve() not in target.parents:
        raise CheckFailure("測試工作目錄必須位於獨立根目錄之下")
    if path.exists():
        raise CheckFailure("拒絕重用先前結果目錄")


def test_count(text: str) -> tuple[int, int]:
    totals = re.findall(r"^Ran (\d+) tests? in ", text, re.M)
    success = re.findall(r"^OK(?: \(skipped=(\d+)\))?\s*$", text, re.M)
    if not totals or not success:
        raise CheckFailure("沒有完整 unittest 結果，不能當作通過")
    total = int(totals[-1])
    skipped = int(success[-1] or 0)
    if total <= 0 or skipped >= total:
        raise CheckFailure("沒有實際執行的測試，不能當作通過")
    return total, skipped


def validate_manifest(path: Path) -> dict:
    manifest = json.loads((path / "FCW_CANDIDATE.json").read_text())
    if manifest.get("baseline") != BASE_SHA or manifest.get("default_enabled") is not False:
        raise CheckFailure("來源基準或預設關閉條件不符")
    if manifest.get("hardware_activation") != "REFUSED_BY_PRODUCTION_SAFETY_BUILD":
        raise CheckFailure("正式韌體拒絕啟用的保護條件不符")
    changed = manifest.get("changed_files", {})
    if not changed:
        raise CheckFailure("候選沒有修改檔案清單")
    for relative, expected in changed.items():
        file = path / relative
        if not file.resolve().is_relative_to(path.resolve()):
            raise CheckFailure("不合法的候選檔案路徑")
        if hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            raise CheckFailure("候選檔案校驗不符: " + relative)
    return manifest


def live_identity() -> dict:
    result = {"resolved_path": str(LIVE.resolve()), "head": None}
    try:
        proc = subprocess.run(["git", "-C", str(LIVE), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10, check=False)
        if proc.returncode == 0:
            result["head"] = proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return result


def terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=3)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


class Runner:
    def __init__(self, directory: Path, report: dict):
        self.directory = directory
        self.report = report
        self.logs = directory / "logs"
        self.logs.mkdir()
        self.env = dict(os.environ)
        for key in ("PYTHONPATH", "PYTHONHOME", "FCW_EXPECT_BENCH", "CC", "CFLAGS", "LDFLAGS"):
            self.env.pop(key, None)
        self.env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1",
                         "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"})

    def step(self, name: str, args: list[str], cwd: Path, *, tests: bool = False,
             timeout: int = 180, extra_env: dict | None = None) -> None:
        require_offroad()
        print("執行：", name, flush=True)
        log = self.logs / (name + ".log")
        item = {"name": name, "status": "RUNNING", "log": str(log.relative_to(self.directory))}
        self.report["stages"].append(item)
        env = dict(self.env)
        env.update(extra_env or {})
        proc = None
        started = time.monotonic()
        try:
            with log.open("w") as out:
                proc = subprocess.Popen(args, cwd=cwd, env=env, stdout=out,
                                        stderr=subprocess.STDOUT, start_new_session=True)
                while proc.poll() is None:
                    require_offroad()
                    if time.monotonic() - started > timeout:
                        raise CheckFailure(name + " 執行逾時")
                    time.sleep(0.5)
                item["exit_code"] = proc.returncode
            text = log.read_text(errors="replace")
            if proc.returncode:
                print("\n".join(text.splitlines()[-35:]), flush=True)
                raise CheckFailure(name + " 失敗，詳見 " + str(log))
            if tests:
                total, skipped = test_count(text)
                item.update({"tests": total, "skipped": skipped})
            item["status"] = "PASS"
        except BaseException:
            item["status"] = "FAIL"
            if proc is not None:
                terminate(proc)
            raise
        finally:
            item["seconds"] = round(time.monotonic() - started, 2)
            if log.exists():
                item["log_sha256"] = hashlib.sha256(log.read_bytes()).hexdigest()

    def fetch(self, name: str, repository: str, sha: str, path: Path) -> None:
        self.step(name + "_init", ["git", "init", str(path)], path.parent)
        self.step(name + "_fetch", ["git", "-C", str(path), "-c", "core.hooksPath=/dev/null",
                                   "fetch", "--depth", "1", repository, sha], path.parent, timeout=300)
        self.step(name + "_checkout", ["git", "-C", str(path), "-c", "core.hooksPath=/dev/null",
                                      "checkout", "--detach", sha], path.parent, timeout=180)
        actual = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
        if actual != sha:
            raise CheckFailure("固定來源版本不符: " + name)


IMPORT_CHECK = r'''
import ast, importlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
for relative in ("opendbc/car/hyundai/carcontroller.py", "opendbc/car/hyundai/carstate.py",
                 "opendbc/car/hyundai/fca_proxy.py", "opendbc/can/parser.py"):
    ast.parse((root / relative).read_text(), filename=relative)
for name in ("opendbc.car.hyundai.carcontroller", "opendbc.car.hyundai.carstate",
             "opendbc.car.hyundai.interface", "opendbc.car.hyundai.fca_proxy", "opendbc.can.parser"):
    module = importlib.import_module(name)
    location = pathlib.Path(module.__file__).resolve()
    if not location.is_relative_to(root):
        raise RuntimeError("誤載入隔離目錄之外的模組: " + str(location))
    print(name, "=>", location)
from opendbc.can import CANParser, CANPacker
parser = CANParser("hyundai_canfd_generated", [("ADRV_0x160", 50)], 2)
packer = CANPacker("hyundai_canfd_generated")
frame = packer.make_can_msg("ADRV_0x160", 2, {"AEB_SETTING": 3})
parser.update([(2_000_000_000, [frame])])
assert parser.message_states[0x160].last_valid_raw == frame[1]
print("實際 parser/packer 初始化及記憶體封包解析通過；沒有發送到 CAN")
'''


def main() -> int:
    if sys.platform != "linux" or platform.machine() not in ("aarch64", "arm64") or not LIVE.is_dir():
        print("這支程式要在 C4 SSH 執行，不是在 Mac 終端機直接執行。", file=sys.stderr)
        return 2
    if not (3, 11) <= sys.version_info[:2] < (3, 13):
        print("需要 C4 現有 Python 3.11/3.12；不會自動修改 Python。", file=sys.stderr)
        return 2
    if not shutil.which("git"):
        print("C4 找不到 git；未變更任何系統檔案。", file=sys.stderr)
        return 2
    try:
        require_offroad()
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        directory = ROOT / (stamp + "-" + str(os.getpid()))
        require_destination(directory)
        if shutil.disk_usage("/data").free < 1_500_000_000:
            raise CheckFailure("/data 可用空間不足 1.5 GB；不清除任何既有資料")
        directory.mkdir(parents=True)
    except (OSError, CheckFailure) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        os.nice(10)
    except OSError:
        pass
    report = {"kind": "C4_ISOLATED_CANDIDATE_LOADCHECK", "candidate_sha": CANDIDATE_SHA,
              "opendbc_sha": BASE_SHA, "python": sys.version, "machine": platform.machine(),
              "status": "INCOMPLETE", "installed_to": None, "live_before": live_identity(),
              "active_system_replaced": False, "panda_firmware_flashed": False,
              "vehicle_mode_enabled": False, "manager_boot_tested": False,
              "fcw_function_verified": False, "stages": []}
    runner = Runner(directory, report)
    success = False
    try:
        # Build only in a temporary workspace. The production activation refusal
        # in the pinned builder and candidate remains unchanged.
        with tempfile.TemporaryDirectory(prefix="fcw-loadcheck-", dir="/tmp") as temp:
            temp = Path(temp)
            runner.fetch("candidate", CANDIDATE_REPO, CANDIDATE_SHA, temp / "candidate")
            runner.fetch("opendbc", BASE_REPO, BASE_SHA, temp / "source")
            pkg = temp / "candidate/tools/fcw_candidate"
            runner.step("materialize", [sys.executable, str(pkg / "prepare_candidate.py"),
                        "--source", str(temp / "source"), "--output", str(temp / "staged")], temp)
            validate_manifest(temp / "staged")
            installed = directory / "opendbc"
            shutil.move(str(temp / "staged"), installed)
            copied_tests = directory / "test-tools"
            shutil.copytree(pkg, copied_tests)
        report["installed_to"] = str(installed)
        runner.env["PYTHONPATH"] = str(installed)
        for sub in ("tmp", "cache"):
            (directory / sub).mkdir()
        runner.env.update({"TMPDIR": str(directory / "tmp"), "XDG_CACHE_HOME": str(directory / "cache")})
        script = directory / "import_check.py"
        script.write_text(IMPORT_CHECK)
        runner.step("actual_import_and_parser", [sys.executable, str(script), str(installed)], installed)
        runner.step("controller_helper", [sys.executable, str(copied_tests / "tests/test_controller_proxy.py")], installed, tests=True)
        runner.step("actual_controller_memory_schedule", [sys.executable, str(copied_tests / "tests/test_runtime_integration.py")], installed, tests=True)
        if not shutil.which("cc"):
            report["stages"].append({"name": "native_c_and_libsafety", "status": "NOT_RUN", "reason": "cc unavailable"})
            raise CheckFailure("Python 載入已測試，但 C4 缺 cc 編譯器；不自動安裝套件")
        runner.step("native_c_policy", [sys.executable, str(copied_tests / "tests/test_native_policy.py")], installed, tests=True)
        runner.step("production_activation_lock", [sys.executable, str(copied_tests / "tests/test_full_libsafety_candidate.py")],
                    installed, tests=True, extra_env={"FCW_EXPECT_BENCH": "0"})
        require_offroad()
        success = True
        report["status"] = "PASS_LOADCHECK_ONLY"
    except (Exception, KeyboardInterrupt) as exc:
        report["status"] = "FAILED_OR_INTERRUPTED"
        report["error"] = type(exc).__name__ + ": " + str(exc)
        print("\n檢查停止：", str(exc), flush=True)
    finally:
        report["live_after"] = live_identity()
        report["live_identity_unchanged"] = report["live_before"] == report["live_after"]
        if not report["live_identity_unchanged"]:
            success = False
            report["status"] = "LIVE_VERSION_CHANGED_DURING_CHECK"
        report_path = directory / "REPORT.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        bundle = directory / "REPORT.tar.gz"
        with tarfile.open(bundle, "w:gz") as archive:
            archive.add(report_path, arcname="REPORT.json")
            archive.add(runner.logs, arcname="logs")
        print("\n結果：", report["status"])
        print("報告：", report_path)
        print("回傳檔案：", bundle)
        print("現用 openpilot 版本未變：", report["live_identity_unchanged"])
        print("未啟動 manager/card/pandad；未刷 Panda；未啟用車上新模式。")
        print("這不是主畫面開機/行車測試，PASS 不代表 FCW/AEB 已恢復。")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
