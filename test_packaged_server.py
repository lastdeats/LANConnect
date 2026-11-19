"""Smoke test a selected server command: source Python or built EXE.
Never touches production data: explicit temporary LAN_DATA_DIR and loopback bind.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import ssl
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error


def probe(command,tls_command=None):
    with tempfile.TemporaryDirectory(prefix='lanconnect-probe-') as td:
        data=Path(td)/'data'
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env=dict(os.environ,LAN_HOST='127.0.0.1',LAN_PORT=str(port),LAN_DATA_DIR=str(data),LAN_MIN_FREE_GIB='1')
        # An existing caller's TLS settings must not leak into an isolated HTTP probe.
        for key in ('LAN_TLS_CERT','LAN_TLS_KEY','LAN_STORAGE_GIB'):env.pop(key,None)
        proc=None;scheme='http'
        log_path=Path(td)/'server.log'
        def start():
            nonlocal proc
            with log_path.open('ab') as log:
                proc=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT)
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                if proc.poll() is not None:
                    raise RuntimeError(f'Server exited during startup (exit={proc.returncode}): '+log_path.read_text(errors='replace')[-3000:])
                try:
                    # A listening TCP socket is not proof that HTTP/TLS is ready.
                    request=urllib.request.Request(f'{scheme}://127.0.0.1:{port}/state')
                    with opener.open(request,timeout=1):return
                except urllib.error.HTTPError as error:
                    # An auth error proves the HTTP handler is accepting requests.
                    if error.code in (401,403):return
                except (OSError,TimeoutError):pass
                time.sleep(.1)
            raise RuntimeError('Server HTTP startup timed out: '+log_path.read_text(errors='replace')[-3000:])
        def stop():
            if proc is not None and proc.poll() is None:
                if os.name=='nt':
                    subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
                else:proc.terminate()
                try:proc.wait(10)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(10)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def req(path,token=None,obj=None,raw=None,binary=False):
            body=raw if raw is not None else json.dumps(obj).encode() if obj is not None else None
            request=urllib.request.Request(f'{scheme}://127.0.0.1:{port}'+path,data=body,headers={'Authorization':'Bearer '+token} if token else {})
            with opener.open(request,timeout=20) as response:
                content=response.read();return content if binary else json.loads(content)
        try:
            print('STAGE: initial HTTP server startup', flush=True)
            start()
            print('STAGE: registration and messaging', flush=True)
            a,b,c=[req('/register',obj={'name':name,'password':'probe-password-123'})['token'] for name in ('Alice','Bob','Carol')]
            users={u['name']:u['id'] for u in req('/state',a)['users']}
            req('/message',a,{'target':users['Bob'],'body':'Packaged server check — Тамбов'})
            assert req('/state',b)['messages'][0]['body'].endswith('Тамбов')
            block=b'LAN Connect'*70000;content=block+b'final'
            print('STAGE: resumable file upload', flush=True)
            upload=req('/upload/start',a,{'target':users['Bob'],'name':'probe.bin','size':len(content)})['upload_id']
            req('/upload/chunk?id='+upload+'&offset=0',a,raw=block)
            print('STAGE: restart and resume', flush=True)
            stop();start()
            assert req('/upload/status',a,{'upload_id':upload})['offset']==len(block)
            req('/upload/chunk?id='+upload+'&offset='+str(len(block)),a,raw=b'final')
            checksum=hashlib.sha256(content).hexdigest()
            req('/upload/finish',a,{'upload_id':upload,'sha256':checksum})
            f=req('/state',b)['files'][0]
            assert f['sha256']==checksum
            assert req('/download?id='+str(f['id']),b,binary=True)==content
            assert req('/file/info?id='+str(f['id']),b)['size']==len(content)
            try:req('/download?id='+str(f['id']),c,binary=True);raise AssertionError('Download ACL bypass')
            except urllib.error.HTTPError as error:assert error.code==403
            print('STAGE: audio relay', flush=True)
            session=req('/live/start',a,{'kind':'audio','owner':users['Bob']})['id']
            req('/live/decision',b,{'id':session,'approve':True})
            req('/live/audio?id='+session+'&sequence=0',a,raw=b'\x00\x01'*1600)
            assert req('/live/audio?id='+session,b,binary=True)==b'\x00\x01'*1600
            req('/live/end',b,{'id':session})
            if tls_command:
                print('STAGE: TLS certificate generation and restart', flush=True)
                stop();certs=Path(td)/'tls'
                subprocess.run([*tls_command,'--out',str(certs),'--ip','127.0.0.1'],env=env,check=True,timeout=90,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                env.update(LAN_TLS_CERT=str(certs/'server-cert.pem'),LAN_TLS_KEY=str(certs/'server-key.pem'))
                scheme='https';context=ssl.create_default_context(cafile=str(certs/'lan-ca.crt'))
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context))
                start();print('STAGE: TLS verification', flush=True);assert req('/state',a)['me']==users['Alice']
                untrusted=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                try:untrusted.open(f'https://127.0.0.1:{port}/state',timeout=20);raise AssertionError('Untrusted TLS accepted')
                except urllib.error.URLError as error:assert isinstance(error.reason,ssl.SSLCertVerificationError)
            return {'ok':True,'registration_chat':True,'restart_resume_file_sha256':True,
                    'download_acl':True,'https_with_generated_ca':bool(tls_command),'audio_relay_synthetic_bytes':True,'hardware_tested':False}
        finally:stop()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--exe',type=Path);parser.add_argument('--source',action='store_true');parser.add_argument('--report',type=Path);parser.add_argument('--tls-tool',type=Path);parser.add_argument('--tls-source',action='store_true')
    args=parser.parse_args()
    if bool(args.exe)==bool(args.source):parser.error('Select exactly --exe or --source')
    command=[str(args.exe.resolve())] if args.exe else [sys.executable,str(Path(__file__).parent/'server.py')]
    if args.tls_tool and args.tls_source:parser.error('Choose --tls-tool or --tls-source')
    tls_command=[str(args.tls_tool.resolve())] if args.tls_tool else [sys.executable,str(Path(__file__).parent/'setup_tls.py')] if args.tls_source else None
    result=probe(command,tls_command)
    if args.report:args.report.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print('PASS: isolated server probe:',json.dumps(result))


if __name__=='__main__':main()
