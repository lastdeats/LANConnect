"""Real TLS server: trusted CA success, untrusted CA and hostname mismatch fail."""
import json, os, socket, ssl, subprocess, sys, tempfile, time
from pathlib import Path
import urllib.request, urllib.error
from setup_tls import generate

src=Path(__file__).parent
with tempfile.TemporaryDirectory() as td:
    root=Path(td)
    generate(root/'tls',['127.0.0.1'],[])
    for name in ('server.py','live.py'):(root/name).write_bytes((src/name).read_bytes())
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    proc=subprocess.Popen([sys.executable,str(root/'server.py')],env=dict(os.environ,LAN_HOST='127.0.0.1',LAN_PORT=str(port),LAN_TLS_CERT=str(root/'tls/server-cert.pem'),LAN_TLS_KEY=str(root/'tls/server-key.pem')),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:socket.create_connection(('127.0.0.1',port),.1).close();break
            except OSError:time.sleep(.03)
        context=ssl.create_default_context(cafile=str(root/'tls/lan-ca.crt'))
        context.verify_flags |= ssl.VERIFY_X509_STRICT
        req=urllib.request.Request(f'https://127.0.0.1:{port}/register',data=json.dumps({'name':'TLS User','password':'password123'}).encode())
        with urllib.request.urlopen(req,context=context,timeout=5) as res:token=json.loads(res.read())['token']
        previous=os.environ.get('LAN_CA_CERT')
        os.environ['LAN_CA_CERT']=str(root/'tls/lan-ca.crt')
        from network import urlopen
        try:
            with urlopen(urllib.request.Request(f'https://127.0.0.1:{port}/state',headers={'Authorization':'Bearer '+token})) as res:assert json.loads(res.read())['me']
        finally:
            if previous is None:os.environ.pop('LAN_CA_CERT',None)
            else:os.environ['LAN_CA_CERT']=previous
        for url,ctx in [(f'https://127.0.0.1:{port}/state',ssl.create_default_context()),(f'https://localhost:{port}/state',context)]:
            try:urllib.request.urlopen(url,context=ctx,timeout=5);raise AssertionError('TLS check bypassed')
            except urllib.error.URLError as error:assert isinstance(error.reason,ssl.SSLCertVerificationError),str(error)
        print('PASS: HTTPS registration, CA validation, hostname validation')
    finally:proc.terminate();proc.wait(5)
