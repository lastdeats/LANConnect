"""Native Windows build with import/GUI/server smoke probes, not hardware acceptance."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parent


def environment():
    reasons=[]
    if os.name!='nt':reasons.append('Windows build host required (PyInstaller does not cross-build EXE)')
    if sys.version_info[:2]!=(3,12):reasons.append('Python 3.12 required')
    for module in ('PyInstaller','PIL','tkinter','cryptography'):
        if importlib.util.find_spec(module) is None:reasons.append('Missing '+module)
    return {'ok':not reasons,'platform':sys.platform,'python':platform.python_version(),'reasons':reasons}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    check=environment()
    if args.check:print(json.dumps(check,indent=2));return 0 if check['ok'] else 1
    if not check['ok']:raise RuntimeError('; '.join(check['reasons']))
    def run(*command,timeout=None):subprocess.run([str(x) for x in command],cwd=ROOT,check=True,timeout=timeout)
    run(sys.executable,'run_tests.py',timeout=300)
    # Build into private staging: a failed attempt leaves any prior distribution intact.
    with tempfile.TemporaryDirectory(prefix='lanconnect-win-build-') as td:
        staging=Path(td);dist=staging/'dist'
        for name,source,mode in [('LANConnect','client.py','--windowed'),('LANConnect-Server','server.py','--console'),('LANConnect-TLS','setup_tls.py','--console')]:
            run(sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onefile',mode,'--name',name,
                '--distpath',dist,'--workpath',staging/'work', '--specpath',staging,ROOT/source,timeout=1200)
        gui_report=staging/'client-probe.json'
        run(dist/'LANConnect.exe','--self-test',gui_report,timeout=90)
        client=json.loads(gui_report.read_text(encoding='utf-8'))
        if not client.get('ok'):raise RuntimeError('Packaged GUI client probe failed')
        server_report=staging/'server-probe.json'
        run(sys.executable,'test_packaged_server.py','--exe',dist/'LANConnect-Server.exe','--tls-tool',dist/'LANConnect-TLS.exe','--report',server_report,timeout=180)
        server=json.loads(server_report.read_text(encoding='utf-8'))
        report={'platform':sys.platform,'python':platform.python_version(),'source_commit':os.getenv('GITHUB_SHA'),
                'client_probe':client,'server_probe':server,'device_tests_complete':False,'files':{}}
        for path in dist.glob('*.exe'):
            report['files'][path.name]={'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        for name in ('README_RU.txt','README_REMOTE.txt','TLS_RU.md'):shutil.copy2(ROOT/name,dist/name)
        (dist/'build-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        output=ROOT/'dist';output.mkdir(exist_ok=True)
        # No broad directory copies: data folders, signing keys and certificates are excluded.
        for path in dist.iterdir():
            temp=output/(path.name+'.new');shutil.copy2(path,temp);os.replace(temp,output/path.name)
    print('Windows binaries built and smoke-tested. Device acceptance is still required:',ROOT/'dist')
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:print('BUILD FAILED:',error,file=sys.stderr);raise SystemExit(1)
