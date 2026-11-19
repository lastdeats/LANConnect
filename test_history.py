"""History beyond the state window, stable cursors and authorization via real HTTP."""
import base64, json, urllib.parse, os, socket, sqlite3, subprocess, sys, tempfile, time, urllib.request, urllib.error
from pathlib import Path
src=Path(__file__).parent
with tempfile.TemporaryDirectory() as td:
 root=Path(td)
 for name in ('server.py','live.py'):(root/name).write_bytes((src/name).read_bytes())
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 proc=subprocess.Popen([sys.executable,str(root/'server.py')],env=dict(os.environ,LAN_HOST='127.0.0.1',LAN_PORT=str(port)),stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
 def req(path,token=None,obj=None):
  with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=json.dumps(obj).encode() if obj is not None else None,headers={'Authorization':'Bearer '+token} if token else {}),timeout=5) as res:return json.loads(res.read())
 def denied(code,*args,**kw):
  try:req(*args,**kw)
  except urllib.error.HTTPError as error:assert error.code==code,(error.code,code);return
  raise AssertionError('request unexpectedly accepted')
 try:
  for _ in range(150):
   if proc.poll() is not None:
    raise RuntimeError('server exited during startup: '+proc.stderr.read().decode(errors='replace')[-2000:])
   try:
    with socket.create_connection(('127.0.0.1',port),.2):
     break
   except OSError:
    time.sleep(.05)
  else:
   raise RuntimeError('server did not become ready within 7.5 seconds')
  a,b,c=[req('/register',obj={'name':name,'password':'password123'})['token'] for name in ('Alice','Bob','Carol')]
  users={u['name']:u['id'] for u in req('/state',a)['users']};alice,bob,carol=[users[n] for n in ('Alice','Bob','Carol')]
  req('/group',a,{'name':'Private'});gid=req('/state',a)['groups'][0]['id'];req('/invite',a,{'gid':gid,'uid':bob})
  with sqlite3.connect(root/'server_data/lan.db') as db:
   db.executemany('INSERT INTO messages(sender,target,body,created) VALUES(?,?,?,?)',[(alice,bob,'direct '+str(i),i) for i in range(650)])
   db.executemany('INSERT INTO messages(sender,gid,body,created) VALUES(?,?,?,?)',[(alice,gid,'group '+str(i),i) for i in range(180)])
   db.executemany('INSERT INTO files(sender,target,filename,stored,size,created,expires) VALUES(?,?,?,?,?,?,?)',[(alice,bob,'expired '+str(i),str(i),1,i,0) for i in range(350)])
   db.execute('INSERT INTO files(sender,target,filename,stored,size,created,expires) VALUES(?,?,?,?,?,?,?)',(alice,carol,'private','private',0,1,0))
  denied(401,'/history?target='+str(bob))
  denied(403,'/history?gid='+str(gid),c)
  denied(400,'/history?target='+str(bob)+'&gid='+str(gid),a)
  for tail in ('&limit=0','&limit=101','&before=invalid'):
   denied(400,'/history?target='+str(bob)+tail,a)
  overflow=base64.urlsafe_b64encode(json.dumps([0,2**90,'message']).encode()).decode()
  denied(400,'/history?target='+str(bob)+'&before='+urllib.parse.quote(overflow),a)
  limited=req('/state?message_limit=5&file_limit=3',a)
  assert len(limited['messages'])==5 and len(limited['files'])==3
  denied(400,'/state?message_limit=501',a)
  denied(400,'/state?file_limit=0',a)
  page=req('/history?target='+str(bob)+'&limit=73',a)
  assert len(page['messages'])+len(page['files'])==73
  # New arrivals must not change the older-page boundary or cause duplication.
  req('/message',b,{'target':alice,'body':'arrived during paging'})
  messages=[];files=[];last=None
  while True:
   keys=sorted([(m['created'],m['id'],'message') for m in page['messages']]+[(f['created'],f['id'],'file') for f in page['files']],reverse=True)
   if last is not None:assert not keys or keys[0]<last,'history pages overlap or have mixed chronology'
   if keys:last=keys[-1]
   messages.extend(page['messages']);files.extend(page['files'])
   if not page['has_more']:break
   page=req('/history?target='+str(bob)+'&limit=73&before='+urllib.parse.quote(page['before']),a)
  assert len(messages)==650 and len({m['id'] for m in messages})==650
  assert {m['body'] for m in messages}=={'direct '+str(i) for i in range(650)}
  assert len(files)==350 and len({f['id'] for f in files})==350
  assert all('stored' not in f for f in files)
  assert req('/history?target='+str(bob),a)['messages'][-1]['body']=='arrived during paging'
  group=req('/history?gid='+str(gid),b);assert all(m['gid']==gid for m in group['messages'])
  old=files[-1]['id'];assert req('/file/info?id='+str(old),b)['id']==old
  denied(403,'/file/info?id='+str(old),c)
  denied(404,'/file/info?id=999999',a)
  denied(410,'/download?id='+str(old),b)
  print('PASS: 650 direct messages, 350 file records, 180 group messages; stable pages, no gaps/duplicates, authorization and expired metadata')
 finally:proc.terminate();proc.wait(5)
