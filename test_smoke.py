"""Local loopback smoke test of registration, messaging, upload/download and expiry."""
import os,sys,tempfile,subprocess,time,json,urllib.request,urllib.error,socket,hashlib,sqlite3
from pathlib import Path
src=Path(__file__).resolve().parent
with tempfile.TemporaryDirectory() as td:
 (Path(td)/'live.py').write_bytes((src/'live.py').read_bytes())
 server=Path(td)/'server.py';server.write_bytes((src/'server.py').read_bytes())
 sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
 env=dict(os.environ,LAN_HOST='127.0.0.1',LAN_PORT=str(port),LAN_MIN_FREE_GIB='1')
 p=subprocess.Popen([sys.executable,str(server)],cwd=td,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 def req(path,obj=None,token=None,raw=None):
  data=raw if raw is not None else (json.dumps(obj).encode() if obj is not None else None)
  headers={'Authorization':'Bearer '+token} if token else {}
  with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}{path}',data=data,headers=headers),timeout=10) as r:
   b=r.read();return b if path.startswith('/download') else json.loads(b)
 try:
  for _ in range(50):
   try: socket.create_connection(('127.0.0.1',port),.1).close();break
   except OSError:time.sleep(.1)
  a=req('/register',{'name':'Alice','password':'password123'})['token']
  b=req('/register',{'name':'Bob','password':'password123'})['token']
  state=req('/state',token=a);bob=next(u['id'] for u in state['users'] if u['name']=='Bob')
  req('/message',{'target':bob,'body':'hello'},a)
  assert any(m['body']=='hello' for m in req('/state',token=b)['messages'])
  content=b'LAN Connect smoke test file'*500
  up=req('/upload/start',{'target':bob,'size':len(content),'name':'smoke.bin'},a)
  req('/upload/chunk?id='+up['upload_id']+'&offset=0',token=a,raw=content)
  completed=req('/upload/finish',{'upload_id':up['upload_id'],'sha256':hashlib.sha256(content).hexdigest()},a)
  assert completed['sha256']==hashlib.sha256(content).hexdigest()
  fid=next(f['id'] for f in req('/state',token=b)['files'] if f['filename']=='smoke.bin')
  assert req('/file/info?id='+str(fid),token=b)['sha256']==hashlib.sha256(content).hexdigest()
  assert req('/history?target='+str(bob),token=a)['files'][0]['sha256']==hashlib.sha256(content).hexdigest()
  assert req('/download?id='+str(fid),token=b)==content
  assert req('/download?id='+str(fid)+'&offset=10',token=b)==content[10:]
  with sqlite3.connect(Path(td)/'server_data'/'lan.db') as db:db.execute('UPDATE files SET expires=0 WHERE id=?',(fid,))
  # Expired downloads are rejected even before janitor deletes file.
  try:req('/download?id='+str(fid),token=b);raise AssertionError('Expired file accessible')
  except urllib.error.HTTPError as e:assert e.code==410
  print('PASS: 2 accounts, message, chunk upload, checksum, full and resumed download, expiry enforcement')
 finally:
  if p.poll() is None:
   p.terminate()
   try:
    p.wait(timeout=10)
   except subprocess.TimeoutExpired:
    p.kill()
    p.wait(timeout=10)

  if p.stderr:
   p.stderr.close()

  # Wait for Windows to release SQLite database handles.
  time.sleep(2)