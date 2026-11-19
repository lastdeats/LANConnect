"""Run isolated test scenarios with bounded execution and readable diagnostics."""
from pathlib import Path
import argparse
import json
import subprocess
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument('--tls', action='store_true')
parser.add_argument('--android-core', action='store_true')
parser.add_argument('--report-json', type=Path, help='Write machine-readable test summary')
parser.add_argument('--timeout', type=int, default=90, help='Maximum seconds per test process')
args = parser.parse_args()
if args.timeout < 1:
    parser.error('--timeout must be positive')
root = Path(__file__).resolve().parent
scripts = ['test_smoke.py', 'test_quota.py', 'test_remote.py', 'test_regressions.py',
           'test_live.py', 'test_network.py', 'test_history.py', 'test_integrity.py',
           'android/test_apk_contract.py', 'test_auto_receive_static.py']
if args.tls:
    scripts.append('test_tls.py')
if args.android_core:
    scripts.append('android/test_compatibility.py')
# Run the end-to-end server probe first: it exercises subprocess lifecycle and
# TLS startup in a clean environment, before other test suites create resources.
commands = [[sys.executable, str(root / 'test_packaged_server.py'), '--source',
             *(['--tls-source'] if args.tls else [])]]
commands.extend([sys.executable, str(root / name)] for name in scripts)
start = time.monotonic()
results = []
def write_report():
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(json.dumps({'passed': sum(x['status']=='passed' for x in results),
            'failed': sum(x['status'] in ('failed','error','timeout') for x in results), 'running': sum(x['status']=='running' for x in results), 'tests': results}, indent=2), encoding='utf-8')
for command in commands:
    name = Path(command[1]).name
    results.append({'name': name, 'status': 'running', 'seconds': 0})
    write_report()
    print(f'RUN {name} (timeout={args.timeout}s)', flush=True)
    t0 = time.monotonic()
    try:
        subprocess.run(command, cwd=root, check=True, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        results[-1] = {'name': name, 'status': 'timeout', 'seconds': round(time.monotonic()-t0, 2)}
        write_report()
        raise SystemExit(f'TIMEOUT {name} after {args.timeout}s')
    except OSError as exc:
        results[-1] = {'name': name, 'status': 'error', 'error': str(exc), 'seconds': round(time.monotonic()-t0, 2)}
        write_report()
        raise SystemExit(f'ERROR {name}: {exc}')
    except subprocess.CalledProcessError as exc:
        results[-1] = {'name': name, 'status': 'failed', 'exit_code': exc.returncode, 'seconds': round(time.monotonic()-t0, 2)}
        write_report()
        raise SystemExit(f'FAILED {name} (exit={exc.returncode})')
    results[-1] = {'name': name, 'status': 'passed', 'seconds': round(time.monotonic()-t0, 2)}
    print(f'OK {name} ({time.monotonic()-t0:.1f}s)', flush=True)
write_report()
print(f'PASS {len(commands)} scenarios in {time.monotonic()-start:.1f}s; no device certification implied.')
print('5 GiB physical transfer: run opt-in test_large_file.py --run --temp-root <volume>.')
