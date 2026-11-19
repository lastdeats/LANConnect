import base64
import json, sqlite3, hashlib, secrets, os, time, threading, urllib.parse, shutil, sys
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from live import handle_live
ROOT=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent; DATA=Path(os.getenv('LAN_DATA_DIR',str(ROOT/'server_data'))); DATA.mkdir(parents=True,exist_ok=True); (DATA/'files').mkdir(exist_ok=True)
DB=DATA/'lan.db'; LOCK=threading.RLock(); MAX_FILE=5*1024**3; CHUNK=1024**2; RETENTION=10*86400; MIN_FREE=int(os.getenv('LAN_MIN_FREE_GIB','30'))*1024**3; UPLOADS=DATA/'uploads'; UPLOADS.mkdir(exist_ok=True)
# Maximum server-side storage for completed and unfinished files.
# Change via LAN_STORAGE_GIB environment variable (default 200 GiB).
STORAGE_LIMIT=int(os.getenv('LAN_STORAGE_GIB','200'))*1024**3
if STORAGE_LIMIT < MAX_FILE: raise RuntimeError('LAN_STORAGE_GIB must be at least 5')


def db():
 c=sqlite3.connect(DB,timeout=30); c.row_factory=sqlite3.Row; return c
with db() as c:
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT UNIQUE, salt TEXT, hash TEXT, token TEXT UNIQUE); CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY, name TEXT, owner INTEGER); CREATE TABLE IF NOT EXISTS members(gid INTEGER, uid INTEGER, UNIQUE(gid,uid)); CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, sender INTEGER, target INTEGER, gid INTEGER, body TEXT, created INTEGER); CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, sender INTEGER, target INTEGER, gid INTEGER, filename TEXT, stored TEXT, size INTEGER, created INTEGER, downloaded INTEGER DEFAULT 0);''')

with db() as c:
 c.execute('CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY, sender INTEGER, target INTEGER, gid INTEGER, filename TEXT, size INTEGER, offset INTEGER DEFAULT 0, created INTEGER, updated INTEGER)')
 cols=[r[1] for r in c.execute('PRAGMA table_info(files)')]
 if 'expires' not in cols: c.execute('ALTER TABLE files ADD COLUMN expires INTEGER')
 if 'expired' not in cols: c.execute('ALTER TABLE files ADD COLUMN expired INTEGER DEFAULT 0')
 if 'sha256' not in cols: c.execute('ALTER TABLE files ADD COLUMN sha256 TEXT')
 c.execute('UPDATE files SET expires=created+? WHERE expires IS NULL',(RETENTION,))
 c.executescript('CREATE INDEX IF NOT EXISTS message_direct_idx ON messages(sender,target,id); CREATE INDEX IF NOT EXISTS message_group_idx ON messages(gid,id); CREATE INDEX IF NOT EXISTS file_direct_idx ON files(sender,target,id); CREATE INDEX IF NOT EXISTS file_group_idx ON files(gid,id);')

with db() as c:
 c.execute("CREATE TABLE IF NOT EXISTS remote_sessions(id TEXT PRIMARY KEY, requester INTEGER NOT NULL, owner INTEGER NOT NULL, status TEXT NOT NULL, created INTEGER NOT NULL, updated INTEGER NOT NULL)")

def remote_cleanup():
 with db() as c:
  c.execute("UPDATE remote_sessions SET status='expired',updated=? WHERE status IN ('pending','approved') AND created<?",(int(time.time()),int(time.time())-3600))

def storage_usage(c):
 completed=c.execute('SELECT COALESCE(SUM(size),0) FROM files WHERE expired=0',()).fetchone()[0]
 pending=c.execute('SELECT COALESCE(SUM(size),0) FROM uploads').fetchone()[0]
 return completed+pending

def cleanup():
 now=int(time.time())
 with LOCK,db() as c:
  for r in c.execute('SELECT id,stored FROM files WHERE expired=0 AND expires<=?',(now,)).fetchall():
   (DATA/'files'/r['stored']).unlink(missing_ok=True)
   c.execute('UPDATE files SET expired=1 WHERE id=?',(r['id'],))
  for r in c.execute('SELECT id FROM uploads WHERE updated<?',(now-86400,)).fetchall():
   (UPLOADS/r['id']).unlink(missing_ok=True)
   c.execute('DELETE FROM uploads WHERE id=?',(r['id'],))
def janitor():
 while True:
  try: cleanup(); remote_cleanup()
  except Exception as e: print('CLEANUP ERROR',repr(e))
  time.sleep(300)

def auth(h):
 token=h.headers.get('Authorization','').removeprefix('Bearer ').strip()
 with db() as c: return c.execute('SELECT id,name FROM users WHERE token=?',(token,)).fetchone()
def rows(c,sql,args=()): return [dict(r) for r in c.execute(sql,args).fetchall()]
class BoundedHTTPServer(ThreadingHTTPServer):
 # Bound per-connection chunk buffers and worker threads on small server laptops.
 def __init__(self,*args,**kwargs):
  self.slots=threading.BoundedSemaphore(128)
  super().__init__(*args,**kwargs)
 def process_request(self,request,address):
  if not self.slots.acquire(blocking=False):
   self.shutdown_request(request);return
  try: super().process_request(request,address)
  except Exception:
   self.slots.release();raise
 def process_request_thread(self,request,address):
  try: super().process_request_thread(request,address)
  finally: self.slots.release()

def user_exists(uid):
 with db() as c: return bool(c.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone())

class Handler(BaseHTTPRequestHandler):
 protocol_version="HTTP/1.0"
 def setup(self):
  super().setup(); self.connection.settimeout(30)
 def log_message(self,*args): pass
 def send(self,status=200,obj=None,binary=None):
  raw=binary if binary is not None else json.dumps(obj or {},ensure_ascii=False).encode()
  self.send_response(status); self.send_header('Content-Type','application/octet-stream' if binary is not None else 'application/json; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
 def read(self,limit=MAX_FILE):
  n=int(self.headers.get('Content-Length','0'))
  if n<0 or n>limit: raise ValueError('Слишком большой запрос')
  raw=self.rfile.read(n)
  if len(raw)!=n: raise ValueError('Неполный запрос')
  return raw
 def do_POST(self):
  try:
   path=urllib.parse.urlparse(self.path).path
   if path=='/register':
    p=json.loads(self.read(10000)); name=p['name'].strip(); password=p['password']
    if not 2<=len(name)<=32 or len(password)<8: raise ValueError('Имя: 2–32 символа; пароль: от 8 символов')
    salt=secrets.token_hex(16); hashed=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),200000).hex(); token=secrets.token_urlsafe(32)
    with LOCK,db() as c: c.execute('INSERT INTO users(name,salt,hash,token) VALUES(?,?,?,?)',(name,salt,hashed,token))
    return self.send(obj={'token':token,'name':name})
   if path=='/login':
    p=json.loads(self.read(10000))
    with LOCK,db() as c:
     u=c.execute('SELECT * FROM users WHERE name=?',(p['name'],)).fetchone()
     if not u or not secrets.compare_digest(hashlib.pbkdf2_hmac('sha256',p['password'].encode(),bytes.fromhex(u['salt']),200000).hex(),u['hash']): return self.send(401,{'error':'Неверный логин или пароль'})
     token=secrets.token_urlsafe(32); c.execute('UPDATE users SET token=? WHERE id=?',(token,u['id']))
    return self.send(obj={'token':token,'name':u['name']})
   u=auth(self)
   if not u: return self.send(401,{'error':'Требуется вход'})
   if handle_live(self,u['id'],path,urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query),True,user_exists): return
   if path=='/upload/start':
    p=json.loads(self.read(20000)); size=int(p['size']); target=int(p.get('target') or 0); gid=int(p.get('gid') or 0)
    name=Path(str(p.get('name') or 'file').replace('\\','/')).name[:200]
    if not 0<=size<=MAX_FILE: raise ValueError('Максимальный размер одного файла 5 ГБ')
    if bool(target)==bool(gid): raise ValueError('Укажите одного получателя или группу')
    with LOCK,db() as c:
     if target and not c.execute('SELECT 1 FROM users WHERE id=?',(target,)).fetchone(): raise ValueError('Получатель не найден')
     if gid and not c.execute('SELECT 1 FROM members WHERE gid=? AND uid=?',(gid,u['id'])).fetchone(): return self.send(403,{'error':'Нет доступа'})
     if storage_usage(c)+size>STORAGE_LIMIT:
      return self.send(507,{'error':'Превышен лимит файлового хранилища сервера'})
     reserved=c.execute('SELECT COALESCE(SUM(size-offset),0) FROM uploads').fetchone()[0]
     if shutil.disk_usage(DATA).free < size+reserved+MIN_FREE: return self.send(507,{'error':'На сервере недостаточно места с резервом свободного места'})
     ident=secrets.token_hex(20); now=int(time.time())
     c.execute('INSERT INTO uploads(id,sender,target,gid,filename,size,offset,created,updated) VALUES(?,?,?,?,?,?,0,?,?)',(ident,u['id'],target or None,gid or None,name,size,now,now))
     (UPLOADS/ident).touch()
    return self.send(obj={'upload_id':ident,'offset':0,'chunk_size':CHUNK})
   if path=='/upload/chunk':
    q=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query); ident=q.get('id',[''])[0]; offset=int(q.get('offset',['-1'])[0]); n=int(self.headers.get('Content-Length','0'))
    if not 0<n<=CHUNK: raise ValueError('Неверный размер части')
    with LOCK,db() as c:
     r=c.execute('SELECT * FROM uploads WHERE id=? AND sender=?',(ident,u['id'])).fetchone()
     if not r: return self.send(404,{'error':'Загрузка не найдена'})
     if offset!=r['offset'] or offset+n>r['size']: return self.send(409,{'error':'Неверная позиция загрузки'})
     if shutil.disk_usage(DATA).free < n+MIN_FREE: return self.send(507,{'error':'Исчерпан свободный резерв диска'})
     data=self.rfile.read(n)
     if len(data)!=n: raise ValueError('Неполная часть')
     with open(UPLOADS/ident,'r+b') as f: f.seek(offset);f.write(data);f.flush()
     c.execute('UPDATE uploads SET offset=?,updated=? WHERE id=?',(offset+n,int(time.time()),ident))
    return self.send(obj={'offset':offset+n})
   if path=='/upload/cancel':
    p=json.loads(self.read(20000)); ident=str(p['upload_id'])
    with LOCK,db() as c:
     r=c.execute('SELECT id FROM uploads WHERE id=? AND sender=?',(ident,u['id'])).fetchone()
     if not r:return self.send(404,{'error':'Загрузка не найдена'})
     (UPLOADS/ident).unlink(missing_ok=True)
     c.execute('DELETE FROM uploads WHERE id=?',(ident,))
    return self.send(obj={'ok':True})
   if path=='/upload/status':
    p=json.loads(self.read(20000));ident=str(p['upload_id'])
    with db() as c:
     r=c.execute('SELECT offset,size,filename FROM uploads WHERE id=? AND sender=?',(ident,u['id'])).fetchone()
    if not r:return self.send(404,{'error':'Загрузка не найдена'})
    return self.send(obj=dict(r))
   if path=='/upload/finish':
    p=json.loads(self.read(20000)); ident=str(p['upload_id'])
    with LOCK,db() as c:
     r=c.execute('SELECT * FROM uploads WHERE id=? AND sender=?',(ident,u['id'])).fetchone()
     if not r: return self.send(404,{'error':'Загрузка не найдена'})
     if r['offset']!=r['size']: return self.send(409,{'error':'Файл загружен не полностью'})
     source=UPLOADS/ident
     if not source.exists() or source.stat().st_size!=r['size']: raise ValueError('Ошибка размера файла')
     expected=p.get('sha256');digest=hashlib.sha256()
     with source.open('rb') as f:
      for part in iter(lambda:f.read(CHUNK),b''):digest.update(part)
     checksum=digest.hexdigest()
     if expected and not secrets.compare_digest(checksum,str(expected)):return self.send(409,{'error':'Контрольная сумма не совпала'})
     stored=secrets.token_hex(20); source.replace(DATA/'files'/stored);now=int(time.time())
     c.execute('INSERT INTO files(sender,target,gid,filename,stored,size,created,expires,sha256) VALUES(?,?,?,?,?,?,?,?,?)',(r['sender'],r['target'],r['gid'],r['filename'],stored,r['size'],now,now+RETENTION,checksum))
     c.execute('DELETE FROM uploads WHERE id=?',(ident,))
    return self.send(obj={'ok':True,'expires':now+RETENTION,'sha256':checksum})
   if path in ('/file','/file_DISABLED'):
    return self.send(410,{'error':'Старый способ отправки отключён. Обновите клиент до 0.2'})
   p=json.loads(self.read(20000))
   with LOCK,db() as c:
    if path=='/remote/request':
     owner=int(p.get('owner') or 0)
     if owner==u['id'] or not c.execute('SELECT 1 FROM users WHERE id=?',(owner,)).fetchone(): raise ValueError('Неверный получатель запроса')
     sid=secrets.token_urlsafe(24); now=int(time.time())
     c.execute('INSERT INTO remote_sessions VALUES(?,?,?,?,?,?)',(sid,u['id'],owner,'pending',now,now))
     return self.send(obj={'session_id':sid,'status':'pending'})
    if path=='/remote/decision':
     sid=str(p.get('session_id') or ''); approve=p.get('approve')
     if not isinstance(approve,bool): raise ValueError('Нужно подтверждение владельца')
     session=c.execute('SELECT * FROM remote_sessions WHERE id=?',(sid,)).fetchone()
     if not session: return self.send(404,{'error':'Сеанс не найден'})
     if session['owner']!=u['id']: return self.send(403,{'error':'Только владелец может подтвердить доступ'})
     if session['status']!='pending' or time.time()-session['created']>120: return self.send(409,{'error':'Запрос больше не активен'})
     status='approved' if approve else 'denied'
     c.execute('UPDATE remote_sessions SET status=?,updated=? WHERE id=?',(status,int(time.time()),sid))
     return self.send(obj={'status':status})
    if path=='/remote/end':
     sid=str(p.get('session_id') or '')
     session=c.execute('SELECT * FROM remote_sessions WHERE id=?',(sid,)).fetchone()
     if not session: return self.send(404,{'error':'Сеанс не найден'})
     if u['id'] not in (session['owner'],session['requester']): return self.send(403,{'error':'Нет доступа'})
     c.execute("UPDATE remote_sessions SET status='ended',updated=? WHERE id=? AND status IN ('pending','approved')",(int(time.time()),sid))
     return self.send(obj={'status':'ended'})
    if path=='/message':
     target=int(p.get('target') or 0); gid=int(p.get('gid') or 0); body=str(p.get('body','')).strip()[:10000]
     if not body or bool(target)==bool(gid): raise ValueError('Неверное сообщение')
     if gid and not c.execute('SELECT 1 FROM members WHERE gid=? AND uid=?',(gid,u['id'])).fetchone(): return self.send(403,{'error':'Нет доступа'})
     if target and not c.execute('SELECT 1 FROM users WHERE id=?',(target,)).fetchone(): raise ValueError('Нет получателя')
     c.execute('INSERT INTO messages(sender,target,gid,body,created) VALUES(?,?,?,?,?)',(u['id'],target or None,gid or None,body,int(time.time())))
    elif path=='/group':
     name=str(p['name']).strip()[:80]
     if not name: raise ValueError('Название пустое')
     cur=c.execute('INSERT INTO groups(name,owner) VALUES(?,?)',(name,u['id'])); c.execute('INSERT INTO members(gid,uid) VALUES(?,?)',(cur.lastrowid,u['id']))
    elif path=='/invite':
     gid=int(p['gid']); uid=int(p['uid'])
     if not c.execute('SELECT 1 FROM groups WHERE id=? AND owner=?',(gid,u['id'])).fetchone(): return self.send(403,{'error':'Только создатель группы'})
     if not c.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone(): raise ValueError('Пользователь не найден')
     c.execute('INSERT OR IGNORE INTO members(gid,uid) VALUES(?,?)',(gid,uid))
    else: return self.send(404,{'error':'Неизвестный адрес'})
   return self.send(obj={'ok':True})
  except sqlite3.IntegrityError: self.send(409,{'error':'Имя занято'})
  except (ValueError,KeyError,TypeError,json.JSONDecodeError) as e: self.send(400,{'error':str(e)})
  except Exception as e: print('ERROR',repr(e)); self.send(500,{'error':'Ошибка сервера'})
 def do_GET(self):
  try:
   u=auth(self)
   if not u: return self.send(401,{'error':'Требуется вход'})
   path=urllib.parse.urlparse(self.path).path; q=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
   if handle_live(self,u['id'],path,q,False,user_exists): return
   with db() as c:
    if path=='/remote/sessions':
     sessions=rows(c,"SELECT id,requester,owner,status,created,updated FROM remote_sessions WHERE requester=? OR owner=? ORDER BY created DESC LIMIT 100",(u['id'],u['id']))
     return self.send(obj={'sessions':sessions})
    if path=='/history':
     target=int(q.get('target',['0'])[0]);gid=int(q.get('gid',['0'])[0]);limit=int(q.get('limit',['50'])[0])
     if bool(target)==bool(gid) or not 1<=limit<=100: raise ValueError('Выберите один чат; размер страницы от 1 до 100')
     if gid and not c.execute('SELECT 1 FROM members WHERE gid=? AND uid=?',(gid,u['id'])).fetchone():return self.send(403,{'error':'Нет доступа к группе'})
     if target and not user_exists(target):raise ValueError('Пользователь не найден')
     cursor=q.get('before',[''])[0];boundary=None
     if cursor:
      if len(cursor)>200:raise ValueError('Неверный указатель страницы')
      try:
       boundary=json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
       if not isinstance(boundary,list) or len(boundary)!=3 or type(boundary[0]) is not int or type(boundary[1]) is not int or boundary[2] not in ('message','file') or not -(2**63)<=boundary[0]<2**63 or not 0<boundary[1]<2**63:raise ValueError()
      except Exception:raise ValueError('Неверный указатель страницы')
     scope='r.gid=?' if gid else '(r.gid IS NULL AND ((r.sender=? AND r.target=?) OR (r.sender=? AND r.target=?)))'
     args=[gid] if gid else [u['id'],target,target,u['id']]
     common='r.id,r.sender,r.target,r.gid,r.created,s.name sender_name'
     message_sql="SELECT "+common+",'message' kind,r.body,NULL filename,NULL size,NULL downloaded,NULL expired,NULL expires,NULL sha256 FROM messages r JOIN users s ON r.sender=s.id WHERE "+scope
     file_sql="SELECT "+common+",'file' kind,NULL body,r.filename,r.size,r.downloaded,r.expired,r.expires,r.sha256 FROM files r JOIN users s ON r.sender=s.id WHERE "+scope
     sql='SELECT * FROM ('+message_sql+' UNION ALL '+file_sql+')'
     params=args+args
     if boundary:sql+=' WHERE (created,id,kind)<(?,?,?)';params+=boundary
     sql+=' ORDER BY created DESC,id DESC,kind DESC LIMIT ?';params.append(limit+1)
     page=rows(c,sql,params);more=len(page)>limit;page=page[:limit]
     next_cursor=base64.urlsafe_b64encode(json.dumps([page[-1]['created'],page[-1]['id'],page[-1]['kind']]).encode()).decode() if more else ''
     messages=[];files=[]
     for item in page[::-1]:
      kind=item.pop('kind')
      if kind=='message':
       for key in ('filename','size','downloaded','expired','expires','sha256'):item.pop(key)
       messages.append(item)
      else:item.pop('body');files.append(item)
     return self.send(obj={'messages':messages,'files':files,'before':next_cursor,'has_more':more})
    if path=='/file/info':
     fid=int(q.get('id',['0'])[0])
     f=c.execute('SELECT f.id,f.sender,f.target,f.gid,f.filename,f.size,f.created,f.downloaded,f.expired,f.expires,f.sha256,s.name sender_name FROM files f JOIN users s ON f.sender=s.id WHERE f.id=?',(fid,)).fetchone()
     if not f:return self.send(404,{'error':'Файл не найден'})
     if not (f['sender']==u['id'] or f['target']==u['id'] or (f['gid'] and c.execute('SELECT 1 FROM members WHERE gid=? AND uid=?',(f['gid'],u['id'])).fetchone())):return self.send(403,{'error':'Нет доступа'})
     return self.send(obj=dict(f))
    if path=='/storage':
     used=storage_usage(c)
     return self.send(obj={'used_bytes':used,'limit_bytes':STORAGE_LIMIT,'free_disk_bytes':shutil.disk_usage(DATA).free,'retention_days':10,'max_file_bytes':MAX_FILE})
    if path=='/state':
     message_limit=int(q.get('message_limit',['500'])[0]);file_limit=int(q.get('file_limit',['300'])[0])
     if not 1<=message_limit<=500 or not 1<=file_limit<=300:raise ValueError('Неверный размер списка состояния')
     users=rows(c,'SELECT id,name FROM users ORDER BY name'); groups=rows(c,'SELECT g.id,g.name,g.owner FROM groups g JOIN members m ON g.id=m.gid WHERE m.uid=?',(u['id'],))
     messages=rows(c,'''SELECT m.*,s.name sender_name FROM messages m JOIN users s ON m.sender=s.id WHERE m.sender=? OR m.target=? OR m.gid IN (SELECT gid FROM members WHERE uid=?) ORDER BY m.id DESC LIMIT ?''',(u['id'],u['id'],u['id'],message_limit))
     files=rows(c,'''SELECT f.id,f.sender,f.target,f.gid,f.filename,f.size,f.created,f.downloaded,f.expired,f.expires,f.sha256,s.name sender_name FROM files f JOIN users s ON f.sender=s.id WHERE f.sender=? OR f.target=? OR f.gid IN (SELECT gid FROM members WHERE uid=?) ORDER BY f.id DESC LIMIT ?''',(u['id'],u['id'],u['id'],file_limit))
     return self.send(obj={'me':u['id'],'users':users,'groups':groups,'messages':messages[::-1],'files':files})
    if path=='/download':
     fid=int(q.get('id',[0])[0]); f=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
     if not f: return self.send(404,{'error':'Файл не найден'})
     allowed=f['sender']==u['id'] or f['target']==u['id'] or (f['gid'] and c.execute('SELECT 1 FROM members WHERE gid=? AND uid=?',(f['gid'],u['id'])).fetchone())
     if not allowed: return self.send(403,{'error':'Нет доступа'})
     if f['expired'] or f['expires']<=int(time.time()): return self.send(410,{'error':'Срок хранения файла истёк'})
     file_path=DATA/'files'/f['stored']
     if not file_path.exists(): return self.send(404,{'error':'Файл отсутствует на сервере'})
     start=int(q.get('offset',['0'])[0]); size=file_path.stat().st_size
     if start<0 or start>size: raise ValueError('Неверная позиция скачивания')
     self.send_response(200);self.send_header('Content-Type','application/octet-stream');self.send_header('Content-Length',str(size-start));self.end_headers()
     with file_path.open('rb') as stream:
      stream.seek(start)
      while True:
       part=stream.read(CHUNK)
       if not part: break
       self.wfile.write(part)
     if f['target']==u['id']: c.execute('UPDATE files SET downloaded=1 WHERE id=?',(fid,));c.commit()
     return
   return self.send(404,{'error':'Неизвестный адрес'})
  except (ValueError,KeyError,TypeError) as e: self.send(400,{'error':str(e)})
  except Exception as e: print('ERROR',repr(e)); self.send(500,{'error':'Ошибка сервера'})
if __name__=='__main__':
 host=os.getenv('LAN_HOST','0.0.0.0');port=int(os.getenv('LAN_PORT','8080'))
 print(f'LAN Connect server: {host}:{port}; files: {DATA}',flush=True)
 cleanup();threading.Thread(target=janitor,daemon=True).start()
 server=BoundedHTTPServer((host,port),Handler)
 cert=os.getenv('LAN_TLS_CERT');key=os.getenv('LAN_TLS_KEY')
 if bool(cert)!=bool(key): raise RuntimeError('Укажите LAN_TLS_CERT и LAN_TLS_KEY вместе')
 if cert:
  import ssl
  context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
  context.load_cert_chain(cert,key);server.socket=context.wrap_socket(server.socket,server_side=True,do_handshake_on_connect=False)
  print('HTTPS enabled',flush=True)
 else: print('HTTP: тестовый режим без шифрования',flush=True)
 server.serve_forever()
