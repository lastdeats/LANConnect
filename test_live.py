"""Unit limits plus real HTTP permission/transport checks; no physical devices."""
from live import LiveSessions, LiveError
from pathlib import Path
import time, tempfile, socket, os, subprocess, sys, json, urllib.request, urllib.error

def rejected(code, fn, *args):
    try:fn(*args)
    except LiveError as error:assert error.status == code;return
    raise AssertionError('accepted forbidden operation')

now=[0.0]
live=LiveSessions(clock=lambda:now[0])
s=live.start(1,2,'audio');sid=s['id']
rejected(409,live.audio_push,sid,1,0,b'\0\0')
rejected(403,live.decision,sid,3,True)
rejected(403,live.decision,sid,1,True)
live.decision(sid,2,True)
for i in range(100):live.audio_push(sid,1,i,bytes([i])*3200)
assert len(live.sessions[sid]['queues'][2])==8
assert live.audio_pull(sid,1)==b''
assert live.audio_pull(sid,2)==bytes([92])*3200
rejected(409,live.audio_push,sid,1,99,b'\0\0')
rejected(400,live.audio_push,sid,1,100,b'x')
now[0]=31
rejected(410,live.audio_pull,sid,2)
assert live.list(1)==[]
sid=live.start(1,2,'screen','view')['id'];live.decision(sid,2,True)
rejected(403,live.command_push,sid,1,{'action':'key','key':'Return'})
live.end(sid,1)
sid=live.start(1,2,'screen','control')['id'];live.decision(sid,2,True)
for _ in range(64):live.command_push(sid,1,{'action':'click','x':.2,'y':.3})
rejected(429,live.command_push,sid,1,{'action':'key','key':'Return'})
assert len(live.command_pull(sid,2)['commands'])==64
assert live.command_pull(sid,2)['commands']==[]
rejected(400,live.command_push,sid,1,{'action':'click','x':float('nan'),'y':0})
rejected(400,live.command_push,sid,1,{'action':'exec','command':'arbitrary'})
live.frame_push(sid,2,b'\xff\xd8'+b'x'*100)
live.end(sid,2)
rejected(410,live.command_pull,sid,2)
assert not live.sessions
for i in range(16):live.start(i*2+10,i*2+11,'audio')
rejected(429,live.start,100,101,'screen')
now[0]+=61
assert live.list(10)==[]
print('PASS: bounded queues, replay rejection, expiry, validation, memory removal, session cap')

src=Path(__file__).parent
with tempfile.TemporaryDirectory() as td:
    for name in ('server.py','live.py'):(Path(td)/name).write_bytes((src/name).read_bytes())
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    p=subprocess.Popen([sys.executable,str(Path(td)/'server.py')],env=dict(os.environ,LAN_HOST='127.0.0.1',LAN_PORT=str(port)),stdout=subprocess.DEVNULL)
    def request(path,token=None,obj=None,raw=None,binary=False):
        data=raw if raw is not None else json.dumps(obj).encode() if obj is not None else None
        req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=data,headers={'Authorization':'Bearer '+token} if token else {})
        with urllib.request.urlopen(req,timeout=5) as response:
            body=response.read();return body if binary else json.loads(body)
    def denied(status,*args,**kw):
        try:request(*args,**kw)
        except urllib.error.HTTPError as e:assert e.code==status,(e.code,status);return
        raise AssertionError('unexpected acceptance')
    try:
        for _ in range(100):
            try:socket.create_connection(('127.0.0.1',port),.1).close();break
            except OSError:time.sleep(.03)
        a,b,c=[request('/register',obj={'name':name,'password':'password123'})['token'] for name in ('Alice','Bob','Carol')]
        users=request('/state',a)['users'];bob=next(u['id'] for u in users if u['name']=='Bob')
        denied(401,'/live/sessions')
        sid=request('/live/start',a,{'owner':bob,'kind':'audio'})['id']
        denied(409,'/live/audio?id='+sid+'&sequence=0',a,raw=b'\0\0')
        assert not request('/live/sessions',c)['sessions']
        denied(403,'/live/decision',c,{'id':sid,'approve':True})
        request('/live/decision',b,{'id':sid,'approve':True})
        denied(403,'/live/audio?id='+sid,c,binary=True)
        pcm=b'\x01\x02'*1600
        request('/live/audio?id='+sid+'&sequence=0',a,raw=pcm)
        assert request('/live/audio?id='+sid,b,binary=True)==pcm
        assert request('/live/audio?id='+sid,a,binary=True)==b''
        request('/live/end',b,{'id':sid})
        denied(410,'/live/audio?id='+sid+'&sequence=1',a,raw=pcm)
        sid=request('/live/start',a,{'owner':bob,'kind':'screen','mode':'view'})['id']
        request('/live/decision',b,{'id':sid,'approve':True})
        denied(403,'/live/command',a,{'id':sid,'command':{'action':'key','key':'Return'}})
        request('/live/end',b,{'id':sid})
        sid=request('/live/start',a,{'owner':bob,'kind':'screen','mode':'control'})['id']
        denied(409,'/live/frame?id='+sid,b,raw=b'\xff\xd8xx')
        request('/live/decision',b,{'id':sid,'approve':True})
        denied(403,'/live/frame?id='+sid,a,raw=b'\xff\xd8xx')
        request('/live/frame?id='+sid,b,raw=b'\xff\xd8xx')
        assert request('/live/frame?id='+sid,a,binary=True)==b'\xff\xd8xx'
        denied(403,'/live/frame?id='+sid,c,binary=True)
        request('/live/command',a,{'id':sid,'command':{'action':'text','text':'Тамбов'}})
        commands=request('/live/commands?id='+sid,b)['commands']
        assert commands==[{'action':'text','text':'Тамбов'}]
        assert not request('/live/commands?id='+sid,b)['commands']
        request('/live/end',b,{'id':sid})
        denied(410,'/live/commands?id='+sid,b)
        denied(410,'/live/frame?id='+sid,a,binary=True)
        print('PASS: HTTP consent, two-way transport roles, isolated PCM, screen/control permissions, revocation')
    finally:p.terminate();p.wait(5)
