#!/usr/bin/env python3
"""Run preserved full safety suites against a real, isolated patched opendbc tree.

No vehicle/SSH/Params/flashing operations. Missing dependencies, zero tests,
skips in targeted integration tests, failures, or incomplete stages never
become a passing report. Software evidence is not road-release approval.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from prepare_candidate import BASE_SHA

HERE = Path(__file__).resolve().parent
STAGES = ('controller','pairing','native','transport','materialize','syntax',
          'can-parser','runtime-integration','full-safety-production',
          'production-refusal','full-safety-bench','paired-libsafety')


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def summarize_unittest(text: str, returncode: int, minimum: int = 1, allow_skips: bool = False) -> dict:
    matches = re.findall(r'^Ran (\d+) tests? in ([0-9.]+)s\s*$', text, re.M)
    ok = re.findall(r'^OK(?: \(skipped=(\d+)\))?\s*$', text, re.M)
    if returncode != 0 or len(matches) != 1 or len(ok) != 1:
        raise ValueError('Unittest did not produce exactly one successful summary')
    run = int(matches[0][0]); skipped = int(ok[0] or '0')
    if skipped < 0 or skipped > run or run - skipped < minimum:
        raise ValueError('No adequate executed-test evidence')
    if skipped and not allow_skips:
        raise ValueError('Targeted integration tests must not be skipped')
    if re.search(r'^FAILED\b|^ERROR:|^FAIL:', text, re.M):
        raise ValueError('Failure text appeared in otherwise successful output')
    return {'reported':run,'skipped':skipped,'executed_non_skipped':run-skipped,'seconds':float(matches[0][1])}


def completed_status(stages: dict) -> str:
    return ('SOFTWARE_TESTS_PASSED_HARDWARE_UNVERIFIED'
            if set(stages) == set(STAGES) and all(x.get('status') == 'PASS' for x in stages.values())
            else 'BLOCKED_INCOMPLETE_OR_FAILED')


class Validation:
    def __init__(self, results: Path):
        self.results = results
        self.report = {
            'format':'fcw_complete_validation_v1','started_utc':utc_now(),
            'baseline_opendbc':BASE_SHA,'python':sys.version,
            'vehicle_installable':False,'road_validated':False,
            'status':'BLOCKED_INCOMPLETE_OR_FAILED',
            'stages':{name:{'status':'NOT_RUN'} for name in STAGES},
            'not_performed':['actual Panda firmware/HIL','vehicle FCW verification','physical AEB verification'],
        }
        self.save()
    def save(self):
        target=self.results/'VALIDATION_CURRENT.json';temp=target.with_suffix('.tmp')
        temp.write_text(json.dumps(self.report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        temp.replace(target)
    def run(self,name,command,cwd,env=None,minimum=None,allow_skips=False):
        record=self.report['stages'][name]
        record.update(status='RUNNING',command=command,started_utc=utc_now());self.save()
        log=self.results/(name+'.log')
        try:
            with log.open('w',encoding='utf-8') as stream:
                process=subprocess.Popen(command,cwd=cwd,env=env,stdout=stream,stderr=subprocess.STDOUT,text=True)
                try:
                    returncode=process.wait(timeout=1800)
                except BaseException:
                    process.kill();process.wait();raise
            text=log.read_text(encoding='utf-8',errors='replace')
            record['returncode']=returncode
            record['log_sha256']=hashlib.sha256(log.read_bytes()).hexdigest()
            if returncode:
                raise RuntimeError(f'{name}: exit {returncode}; see {log}')
            if minimum is not None:
                record['tests']=summarize_unittest(text,returncode,minimum,allow_skips)
            record.update(status='PASS',finished_utc=utc_now())
            print(name+': PASS',record.get('tests',''),flush=True)
        except BaseException as exc:
            record.update(status='FAILED',error=str(exc),finished_utc=utc_now());raise
        finally:
            self.save()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',type=Path,required=True)
    ap.add_argument('--results',type=Path,required=True,help='New results directory; existing directories are refused')
    args=ap.parse_args();source=args.source.expanduser().resolve();results=args.results.expanduser().resolve()
    if results.exists():
        ap.error('Use a NEW results directory; never mix old PASS logs with a new run')
    results.mkdir(parents=True);v=Validation(results)
    try:
        if sys.version_info[:2] != (3,12):
            raise RuntimeError('Use the isolated Python 3.12 environment for this pinned baseline')
        head=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True,stderr=subprocess.STDOUT).strip()
        if head != BASE_SHA:
            raise RuntimeError(f'Expected exact opendbc {BASE_SHA}, got {head}')
        for stage,file,minimum in (
            ('controller','test_controller_proxy.py',15),('pairing','test_pairing_regression.py',6),
            ('native','test_native_policy.py',20),('transport','test_transport_readiness.py',5)):
            v.run(stage,[sys.executable,str(HERE/'tests'/file)],HERE,minimum=minimum)
        with tempfile.TemporaryDirectory(prefix='fcw-full-') as temp:
            root=Path(temp)/'opendbc'
            v.run('materialize',[sys.executable,str(HERE/'prepare_candidate.py'),'--source',str(source),'--output',str(root)],HERE)
            env=os.environ.copy();env['PYTHONPATH']=str(root)
            v.run('syntax',[sys.executable,'-m','compileall','-q','opendbc'],root,env)
            v.run('can-parser',[sys.executable,'-m','unittest','discover','-s','opendbc/can/tests','-t','.'],root,env,minimum=1,allow_skips=True)
            v.run('runtime-integration',[sys.executable,str(HERE/'tests/test_runtime_integration.py')],root,env,minimum=5)
            full=[sys.executable,'-m','unittest','discover','-s','opendbc/safety/tests','-t','.']
            v.run('full-safety-production',full,root,env,minimum=3000,allow_skips=True)
            v.run('production-refusal',[sys.executable,str(HERE/'tests/test_full_libsafety_candidate.py')],root,dict(env,FCW_EXPECT_BENCH='0'),minimum=3)
            # TEST ONLY: never add this definition to the vehicle firmware builder.
            translation_unit=root/'opendbc/safety/tests/libsafety/safety.c'
            translation_unit.write_text('#define HYUNDAI_FCW_BENCH 1\n'+translation_unit.read_text())
            v.run('full-safety-bench',full,root,env,minimum=3000,allow_skips=True)
            v.run('paired-libsafety',[sys.executable,str(HERE/'tests/test_full_libsafety_candidate.py')],root,dict(env,FCW_EXPECT_BENCH='1'),minimum=19)
            for name in ('FCW_CANDIDATE.patch','FCW_CANDIDATE.json'):
                (results/name).write_bytes((root/name).read_bytes())
            # No vehicle image or installer is produced even when host tests pass.
        v.report['status']=completed_status(v.report['stages']);v.report['finished_utc']=utc_now();v.save()
        print(v.report['status']);return 0
    except BaseException as exc:
        v.report['status']='BLOCKED_INCOMPLETE_OR_FAILED';v.report['error']=f'{type(exc).__name__}: {exc}'
        v.report['finished_utc']=utc_now();v.save();print('BLOCKED:',exc,file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
