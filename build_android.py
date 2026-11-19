"""Build and inspect an APK using installed SDK/JDK. No network downloads.
Signing verifies identity, not feature completeness; hardware tests remain required.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile
from apk_contract import inspect_apk


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--sdk',default=os.getenv('ANDROID_SDK_ROOT') or os.getenv('ANDROID_HOME'))
    parser.add_argument('--tools',default='35.0.0');parser.add_argument('--platform',default='android-35')
    parser.add_argument('--keystore',type=Path);parser.add_argument('--key-alias')
    parser.add_argument('--signing',choices=['development','release'],default='development')
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    if not args.sdk:parser.error('Android SDK не найден: укажите --sdk или ANDROID_SDK_ROOT')
    sdk=Path(args.sdk).resolve();tools=sdk/'build-tools'/args.tools;android=sdk/'platforms'/args.platform/'android.jar'
    exe='.exe' if os.name=='nt' else ''
    paths={n:tools/(n+exe) for n in ('aapt2','zipalign')}
    jars={n:tools/'lib'/(n+'.jar') for n in ('d8','apksigner')}
    java=shutil.which('java');javac=shutil.which('javac');keytool=shutil.which('keytool')
    missing=[str(p) for p in [android,*paths.values(),*jars.values()] if not p.is_file()]
    if not java or not keytool:missing.append('JDK 17+: java/keytool в PATH')
    if missing:raise RuntimeError('Отсутствуют инструменты:\n'+'\n'.join(missing))
    compiler=[javac] if javac else [java,'-m','jdk.compiler/com.sun.tools.javac.Main']
    subprocess.run([*compiler,'-version'],check=True,stdout=subprocess.DEVNULL)
    signing_env=dict(os.environ)
    if args.signing=='release':
        if args.keystore is None:raise RuntimeError('Release signing requires an existing persistent keystore')
        key=args.keystore.expanduser().resolve()
        if not key.is_file():raise RuntimeError('Release signing requires an existing persistent keystore')
        alias=args.key_alias or os.getenv('LAN_ANDROID_KEY_ALIAS')
        if not alias:raise RuntimeError('Release key alias is missing')
        if not os.getenv('LAN_ANDROID_STORE_PASSWORD'):raise RuntimeError('LAN_ANDROID_STORE_PASSWORD is missing')
        signing_env['LAN_ANDROID_KEY_PASSWORD']=os.getenv('LAN_ANDROID_KEY_PASSWORD') or os.environ['LAN_ANDROID_STORE_PASSWORD']
        key=args.keystore.expanduser().resolve()
    else:
        key=(args.keystore or Path.home()/'.lanconnect/dev-signing.jks').expanduser().resolve()
        alias=args.key_alias or 'lanconnect-dev'
        signing_env.update(LAN_ANDROID_STORE_PASSWORD='android',LAN_ANDROID_KEY_PASSWORD='android')
    root=Path(__file__).resolve().parent;output=(args.out or root/'build').resolve();output.mkdir(parents=True,exist_ok=True)
    def run(*cmd,capture=False,env=None):
        result=subprocess.run([str(x) for x in cmd],cwd=root,check=True,env=env,
                              stdout=subprocess.PIPE if capture else None,encoding='utf-8' if capture else None)
        return result.stdout if capture else None
    # Staging protects any earlier verified APK from an incomplete rebuild.
    with tempfile.TemporaryDirectory(prefix='.apk-work-',dir=output) as td:
        build=Path(td)
        for n in ('classes','generated','dex'):(build/n).mkdir()
        run(paths['aapt2'],'compile','--dir',root/'res','-o',build/'resources.zip')
        run(paths['aapt2'],'link','-I',android,'--manifest',root/'AndroidManifest.xml','--java',build/'generated',
            '--auto-add-overlay','-o',build/'unsigned.apk',build/'resources.zip')
        sources=sorted((root/'src').rglob('*.java'))+sorted((build/'generated').rglob('*.java'))
        run(*compiler,'-encoding','UTF-8','-source','8','-target','8','-bootclasspath',android,'-d',build/'classes',*sources)
        with zipfile.ZipFile(build/'classes.jar','w',zipfile.ZIP_DEFLATED) as archive:
            for path in (build/'classes').rglob('*.class'):archive.write(path,path.relative_to(build/'classes').as_posix())
        run(java,'-cp',jars['d8'],'com.android.tools.r8.D8','--min-api','26','--lib',android,'--output',build/'dex',build/'classes.jar')
        with zipfile.ZipFile(build/'unsigned.apk','a',zipfile.ZIP_DEFLATED) as archive:
            dex=list((build/'dex').glob('*.dex'))
            if not dex:raise RuntimeError('D8 produced no DEX files')
            for path in dex:archive.write(path,path.name)
        run(paths['zipalign'],'-f','4',build/'unsigned.apk',build/'aligned.apk')
        if not key.exists():
            if args.signing!='development':raise RuntimeError('Release keystore is no longer available')
            key.parent.mkdir(parents=True,exist_ok=True)
            run(keytool,'-genkeypair','-keystore',key,'-storepass','android','-keypass','android','-alias',alias,
                '-dname','CN=LAN Connect Development,O=TAMBOV','-keyalg','RSA','-keysize','2048','-validity','3650')
            if os.name!='nt':key.chmod(0o600)
        signed=build/'signed.apk'
        run(java,'-jar',jars['apksigner'],'sign','--ks',key,'--ks-key-alias',alias,'--ks-pass','env:LAN_ANDROID_STORE_PASSWORD',
            '--key-pass','env:LAN_ANDROID_KEY_PASSWORD','--v1-signing-enabled','true','--v2-signing-enabled','true',
            '--v4-signing-enabled','false','--out',signed,build/'aligned.apk',env=signing_env)
        signature=run(java,'-jar',jars['apksigner'],'verify','--verbose','--print-certs',signed,capture=True)
        import re
        digest=re.search(r'Signer #1 certificate SHA-256 digest: ([0-9a-fA-F]{64})',signature)
        if not digest:raise RuntimeError('Signer certificate fingerprint not reported')
        badging=run(paths['aapt2'],'dump','badging',signed,capture=True)
        report=inspect_apk(signed,badging)
        report.update(signing=args.signing,signer_sha256=digest[1].lower(),bytes=signed.stat().st_size,
                      sha256=hashlib.sha256(signed.read_bytes()).hexdigest(),source_commit=os.getenv('GITHUB_SHA'),device_tests_complete=False)
        name='LANConnect.apk' if args.signing=='release' else 'LANConnect-dev.apk'
        os.replace(signed,output/name)
        temp=output/'build-report.json.new';temp.write_text(json.dumps(report,indent=2),encoding='utf-8');os.replace(temp,output/'build-report.json')
        print('APK compiled, signed and structurally checked:',output/name)
        print('Device tests are still required. Signing is not a readiness certificate.')


if __name__=='__main__':
    try:main()
    except Exception as error:print('BUILD FAILED:',error,file=sys.stderr);sys.exit(1)
