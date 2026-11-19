"""Optional real 5 GiB loopback transfer; writes 5 GiB to the selected test volume.
python test_large_file.py --run --temp-root /path/with/at/least/7GiB/free
Does not qualify Windows, Android, throughput over LAN or multi-user load.
"""
import argparse, hashlib, json, os, socket, subprocess, sys, tempfile, time
from pathlib import Path
import urllib.request


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',action='store_true');p.add_argument('--temp-root',type=Path)
    args=p.parse_args()
    if not args.run:print('SKIP: supply --run and a test volume with sufficient space');return
    src=Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(dir=args.temp_root) as td:
        root=Path(td)
        for name in ('server.py','live.py'):(root/name).write_bytes((src/name).read_bytes())
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        proc=subprocess.Popen([sys.executable,str(root/'server.py')],env=dict(os.environ,LAN_HOST='127.0.0.1',LAN_PORT=str(port),LAN_MIN_FREE_GIB='1'),stdout=subprocess.DEVNULL)
        def ready():
            for _ in range(100):
                try:socket.create_connection(('127.0.0.1',port),.1).close();return
                except OSError:time.sleep(.03)
            raise RuntimeError('server failed to start')
        def req(path,token=None,obj=None,raw=None):
            data=raw if raw is not None else json.dumps(obj).encode() if obj is not None else None
            with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=data,headers={'Authorization':'Bearer '+token} if token else {}),timeout=180) as res:return json.loads(res.read())
        try:
            ready()
            a,b=[req('/register',obj={'name':name,'password':'password123'})['token'] for name in ('Alice','Bob')]
            bob=next(x['id'] for x in req('/state',a)['users'] if x['name']=='Bob')
            size=5*1024**3;block=bytes(range(256))*4096
            sid=req('/upload/start',a,{'target':bob,'name':'real-5GiB.bin','size':size})['upload_id']
            expected=hashlib.sha256();started=time.monotonic()
            for index in range(5120):
                if index==128:
                    proc.terminate();proc.wait(5)
                    proc=subprocess.Popen([sys.executable,str(root/'server.py')],env=dict(os.environ,LAN_HOST='127.0.0.1',LAN_PORT=str(port),LAN_MIN_FREE_GIB='1'),stdout=subprocess.DEVNULL)
                    ready();assert req('/upload/status',a,{'upload_id':sid})['offset']==128*len(block)
                expected.update(block)
                result=req('/upload/chunk?id='+sid+'&offset='+str(index*len(block)),a,raw=block)
                assert result['offset']==(index+1)*len(block)
                if index%512==511:print('Uploaded',round((index+1)/1024,1),'GiB',flush=True)
            req('/upload/finish',a,{'upload_id':sid,'sha256':expected.hexdigest()})
            file=req('/state',b)['files'][0];assert file['size']==size and file['sha256']==expected.hexdigest()
            digest=hashlib.sha256();total=0
            request=urllib.request.Request(f'http://127.0.0.1:{port}/download?id='+str(file['id']),headers={'Authorization':'Bearer '+b})
            with urllib.request.urlopen(request,timeout=180) as response:
                while True:
                    part=response.read(1024**2)
                    if not part:break
                    digest.update(part);total+=len(part)
            assert total==size and digest.hexdigest()==expected.hexdigest()
            mem=Path('/proc')/str(proc.pid)/'status'
            peak=next((s for s in mem.read_text().splitlines() if s.startswith('VmHWM:')),'not measured') if mem.exists() else 'not measured'
            print('PASS: real 5 GiB upload + server restart/resume + full download, SHA-256',expected.hexdigest(),flush=True)
            print('Elapsed seconds:',round(time.monotonic()-started,2),'server peak RSS (after restart):',peak,flush=True)
        finally:proc.terminate();proc.wait(5)


if __name__=='__main__':main()
