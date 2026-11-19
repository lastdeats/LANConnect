from network import urlopen
from file_integrity import verify_download, IntegrityError
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import urllib.request,urllib.parse,urllib.error,json,threading,os,hashlib,time,shutil
from pathlib import Path
class App:
 def __init__(self):
  self.root=tk.Tk();self.root.title('LAN Connect • TAMBOV');self.root.geometry('950x620');self.token='';self.me=None;self.state={};self.selected=None;self.busy=False;self.transfer_busy=False;self.auto_download=tk.BooleanVar(value=True);self.auto_running=False;self.auto_seen=set();self.download_dir=Path.home()/'Downloads'/'LAN Connect'
  self.live=None;self.root.protocol('WM_DELETE_WINDOW',self.close);self.apply_theme();self.login_ui();self.root.mainloop()
 def close(self):
  if self.live:self.live.close()
  self.root.destroy()
 def apply_theme(self):
  bg='#091526'; panel='#10243b'; fg='#d9edf9'; cyan='#38cce8'
  self.root.configure(bg=bg)
  style=ttk.Style(self.root);style.theme_use('clam')
  style.configure('.',background=bg,foreground=fg,fieldbackground=panel)
  style.configure('TButton',background=panel,foreground=cyan,padding=6)
  style.map('TButton',background=[('active','#174464')])
  self.root.option_add('*Listbox.background',panel);self.root.option_add('*Listbox.foreground',fg)
  self.root.option_add('*Text.background',panel);self.root.option_add('*Text.foreground',fg)
  self.root.option_add('*Text.insertBackground',cyan)
 def request(self,path,payload=None,file=None):
  url=self.url.get().rstrip('/')+path; headers={}
  if self.token: headers['Authorization']='Bearer '+self.token
  if file is not None: data=file;headers['Content-Type']='application/octet-stream'
  elif payload is not None: data=json.dumps(payload,ensure_ascii=False).encode();headers['Content-Type']='application/json'
  else:data=None
  try:
   with urlopen(urllib.request.Request(url,data=data,headers=headers),timeout=30) as r:
    raw=r.read();return raw if path.startswith('/download') else json.loads(raw)
  except urllib.error.HTTPError as e:
   try: msg=json.loads(e.read()).get('error',str(e))
   except:msg=str(e)
   raise RuntimeError(msg)
 def async_task(self,fn,done=None):
  def worker():
   try: res=fn();self.root.after(0,lambda:done(res) if done else None)
   except Exception as e: self.root.after(0,lambda err=str(e):messagebox.showerror('Ошибка',err))
  threading.Thread(target=worker,daemon=True).start()
 def login_ui(self):
  self.frame=ttk.Frame(self.root,padding=35);self.frame.pack(expand=True)
  ttk.Label(self.frame,text='LAN Connect • TAMBOV',font=('Segoe UI',23,'bold')).pack(pady=12)
  self.url=tk.StringVar(value='http://127.0.0.1:8080');self.name=tk.StringVar();self.password=tk.StringVar()
  for title,var,mask in [('Адрес сервера',self.url,False),('Имя пользователя',self.name,False),('Пароль (от 8 символов)',self.password,True)]:
   ttk.Label(self.frame,text=title).pack(anchor='w');ttk.Entry(self.frame,textvariable=var,width=45,show='*' if mask else '').pack(pady=(0,12))
  ttk.Button(self.frame,text='Войти',command=lambda:self.sign('login')).pack(fill='x',pady=4)
  ttk.Button(self.frame,text='Зарегистрироваться',command=lambda:self.sign('register')).pack(fill='x')
 def sign(self,mode):
  self.async_task(lambda:self.request('/'+mode,{'name':self.name.get(),'password':self.password.get()}),self.logged)
 def logged(self,res):
  self.token=res['token'];self.frame.destroy();self.ui();self.refresh()
 def ui(self):
  bar=ttk.Frame(self.root,padding=8);bar.pack(fill='x')
  ttk.Label(bar,text='LAN Connect • TAMBOV',font=('Segoe UI',15,'bold')).pack(side='left');ttk.Button(bar,text='Обновить',command=self.refresh).pack(side='right')
  ttk.Checkbutton(bar,text='Автополучение личных файлов',variable=self.auto_download).pack(side='right',padx=8)
  ttk.Button(bar,text='Создать группу',command=self.create_group).pack(side='right',padx=4)
  ttk.Button(bar,text='Добавить в группу',command=self.invite).pack(side='right',padx=4)
  pane=ttk.Panedwindow(self.root,orient='horizontal');pane.pack(fill='both',expand=True,padx=8,pady=8)
  left=ttk.Frame(pane);right=ttk.Frame(pane);pane.add(left,weight=1);pane.add(right,weight=3)
  ttk.Label(left,text='Люди и группы').pack(anchor='w');self.contacts=tk.Listbox(left,font=('Segoe UI',11));self.contacts.pack(fill='both',expand=True);self.contacts.bind('<<ListboxSelect>>',self.choose)
  self.heading=ttk.Label(right,text='Выберите чат',font=('Segoe UI',13,'bold'));self.heading.pack(anchor='w')
  self.history=tk.Text(right,state='disabled',wrap='word',font=('Segoe UI',10));self.history.pack(fill='both',expand=True,pady=7)
  line=ttk.Frame(right);line.pack(fill='x');self.entry=ttk.Entry(line);self.entry.pack(side='left',fill='x',expand=True);self.entry.bind('<Return>',lambda e:self.send_message())
  ttk.Button(line,text='Отправить',command=self.send_message).pack(side='left',padx=3)
  ttk.Button(line,text='Файл',command=self.send_file).pack(side='left')
  ttk.Button(right,text='История выбранного чата',command=self.open_history).pack(anchor='e',pady=3)
  ttk.Button(right,text='Скачать выбранный файл по ID',command=self.download).pack(anchor='e',pady=5)
  self.progress=ttk.Label(right,text='Передачи: ожидание');self.progress.pack(anchor='w')
  livebar=ttk.Frame(self.root,padding=8);livebar.pack(fill='x')
  for label,kind,mode in [('Аудиозвонок','audio','view'),('Запросить экран','screen','view'),('Запросить управление','screen','control')]:
   ttk.Button(livebar,text=label,command=lambda k=kind,m=mode:self.start_live(k,m)).pack(side='left',padx=4)
  self.root.after(4000,self.auto_refresh)
 def start_live(self,kind,mode):
  if self.live:self.live.start(kind,mode)
  else:messagebox.showinfo('Связь','Дождитесь загрузки списка пользователей')
 def auto_refresh(self):
  self.refresh();self.root.after(4000,self.auto_refresh)
 def refresh(self):
  if self.busy:return
  self.busy=True
  def done(s):
   self.busy=False;self.state=s;self.render_contacts();self.render_chat();self.auto_receive()
   if self.live is None:
    from live_client import LiveClient
    self.live=LiveClient(self)
  def worker():
   try: s=self.request('/state');self.root.after(0,lambda:done(s))
   except Exception:self.root.after(0,lambda:setattr(self,'busy',False))
  threading.Thread(target=worker,daemon=True).start()
 def auto_receive(self):
  # Only incoming direct files; group files are downloaded manually.
  if not self.auto_download.get() or self.auto_running or self.transfer_busy:return
  me=self.state.get('me');now=int(time.time())
  pending=[f for f in self.state.get('files',[]) if f['target']==me and f['sender']!=me and not f.get('expired') and f.get('expires',0)>now]
  if not pending:return
  self.auto_running=True
  server=self.url.get().rstrip('/');token=self.token;username=self.name.get()
  def worker():
   try:
    scope=hashlib.sha256((server+'|'+username).encode()).hexdigest()[:16]
    folder=self.download_dir/scope;folder.mkdir(parents=True,exist_ok=True)
    manifest=folder/'.received.json'
    try:seen=json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError,ValueError):seen={}
    for f in pending:
     key=hashlib.sha256((server+'|'+username+'|'+str(f['id'])).encode()).hexdigest()
     if key in seen and (folder/seen[key]).is_file():continue
     if shutil.disk_usage(folder).free < f['size']+2*1024**3:
      self.progress_update('Недостаточно места для автополучения: '+f['filename']);continue
     safe=Path(str(f['filename']).replace('\\','/')).name or 'file'
     filename=str(f['id'])+'_'+safe
     path=folder/filename;partial=folder/(filename+'.lanpart')
     offset=partial.stat().st_size if partial.exists() else 0
     if offset>f['size']:partial.unlink();offset=0
     url=server+'/download?'+urllib.parse.urlencode({'id':f['id'],'offset':offset})
     headers={'Authorization':'Bearer '+token}
     try:
      with urlopen(urllib.request.Request(url,headers=headers),timeout=120) as response,partial.open('ab') as out:
       while True:
        chunk=response.read(1024*1024)
        if not chunk:break
        out.write(chunk);offset+=len(chunk)
        self.progress_update('Автополучение: '+str(round(offset/max(f['size'],1)*100,1))+'% — '+safe)
      if offset!=f['size']:continue
      try:verify_download(partial,f['size'],f.get('sha256'))
      except IntegrityError:
       partial.unlink(missing_ok=True);raise
      partial.replace(path);seen[key]=filename
      temp=manifest.with_suffix('.tmp');temp.write_text(json.dumps(seen,ensure_ascii=False),encoding='utf-8');temp.replace(manifest)
      self.progress_update('Получено: '+safe)
     except (OSError,urllib.error.URLError) as exc:
      self.progress_update('Ошибка получения '+safe+': '+str(exc));break
   finally:self.root.after(0,lambda:setattr(self,'auto_running',False))
  threading.Thread(target=worker,daemon=True).start()
 def render_contacts(self):
  old=self.selected; self.items=[]
  for u in self.state['users']:
   if u['id']!=self.state['me']:self.items.append(('user',u['id'],u['name']))
  for g in self.state['groups']:self.items.append(('group',g['id'],g['name']))
  self.contacts.delete(0,'end')
  for kind,ident,name in self.items:self.contacts.insert('end',('👥 ' if kind=='group' else '• ')+name+'  ['+str(ident)+']')
  for i,(kind,ident,name) in enumerate(self.items):
   if old==(kind,ident):self.contacts.selection_set(i);break
 def choose(self,event=None):
  sel=self.contacts.curselection()
  if sel:self.selected=self.items[sel[0]][:2];self.render_chat()
 def render_chat(self):
  if not self.selected:return
  kind,ident=self.selected;self.heading.config(text=next((x[2] for x in self.items if x[:2]==self.selected),'Чат'))
  messages=[('message',m) for m in self.state.get('messages',[]) if (m['gid']==ident if kind=='group' else (m['gid'] is None and {m['sender'],m['target']}=={self.state['me'],ident}))]
  files=[('file',f) for f in self.state.get('files',[]) if (f['gid']==ident if kind=='group' else (f['gid'] is None and {f['sender'],f['target']}=={self.state['me'],ident}))]
  lines=[]
  for typ,r in sorted(messages+files,key=lambda x:(x[1]['created'],x[1]['id'],x[0])):
   if typ=='message':lines.append(r['sender_name']+': '+r['body'])
   else:lines.append(r['sender_name']+': 📎 '+r['filename']+' (ID '+str(r['id'])+', '+str(r['size'])+' байт'+(', срок хранения истёк' if r.get('expired') or r.get('expires',0)<=int(time.time()) else ', доступен')+')')
  self.history.config(state='normal');self.history.delete('1.0','end');self.history.insert('end','\n\n'.join(lines));self.history.config(state='disabled');self.history.see('end')
 def open_history(self):
  try:target=self.target()
  except Exception as e:return messagebox.showinfo('История',str(e))
  window=tk.Toplevel(self.root);window.title('LAN Connect — история чата');window.geometry('800x600')
  text=tk.Text(window,state='disabled',wrap='word');text.pack(fill='both',expand=True,padx=8,pady=8)
  bar=ttk.Frame(window);bar.pack(fill='x',padx=8,pady=8)
  cursors={'before':''};stack=[];next_page={};busy=[False];generation=[0]
  notice=ttk.Label(window,text='Файлы из истории можно скачать в основном окне по ID.');notice.pack(fill='x')
  def fetch(cursor,new_stack=None):
   if busy[0]:return
   busy[0]=True;generation[0]+=1;current=generation[0]
   older.config(state='disabled');previous.config(state='disabled');latest.config(state='disabled')
   def done(result,error=None):
    if not window.winfo_exists() or current!=generation[0]:return
    busy[0]=False
    latest.config(state='normal');previous.config(state='normal' if stack else 'disabled')
    if error:
     notice.config(text='Ошибка: '+error);older.config(state='normal');return
    if new_stack is not None:stack[:]=new_stack
    previous.config(state='normal' if stack else 'disabled')
    cursors.clear();cursors.update(cursor);next_page.clear();next_page.update({'before':result['before']})
    records=[('message',m) for m in result['messages']]+[('file',f) for f in result['files']]
    lines=[]
    for kind,r in sorted(records,key=lambda pair:(pair[1]['created'],pair[1]['id'],pair[0])):
     lines.append(r['sender_name']+': '+(r['body'] if kind=='message' else '📎 '+r['filename']+' • ID '+str(r['id'])+' • '+str(r['size'])+' байт'+(' • срок истёк' if r['expired'] or r['expires']<=time.time() else '')))
    text.config(state='normal');text.delete('1.0','end');text.insert('end','\n\n'.join(lines) or 'Сообщений нет');text.config(state='disabled');text.see('1.0')
    older.config(state='disabled' if not result['has_more'] else 'normal')
    notice.config(text='Страница '+str(len(stack)+1)+' • файлы скачиваются в основном окне по ID')
   def work():
    try:
     result=self.request('/history?'+urllib.parse.urlencode(dict(target,**cursor)))
     self.root.after(0,lambda:done(result))
    except Exception as exc:self.root.after(0,lambda err=str(exc):done(None,err))
   threading.Thread(target=work,daemon=True).start()
  def go_older():
   if busy[0] or not next_page:return
   fetch(dict(next_page),stack+[dict(cursors)])
  def go_previous():
   if not busy[0] and stack:fetch(stack[-1],stack[:-1])
  def go_latest():
   if not busy[0]:fetch({'before':''},[])
  previous=ttk.Button(bar,text='Новее',command=go_previous);previous.pack(side='left')
  older=ttk.Button(bar,text='Раньше',command=go_older);older.pack(side='left',padx=4)
  latest=ttk.Button(bar,text='Последние',command=go_latest);latest.pack(side='right')
  fetch(dict(cursors))
 def target(self):
  if not self.selected:raise RuntimeError('Выберите чат')
  return {'gid' if self.selected[0]=='group' else 'target':self.selected[1]}
 def send_message(self):
  body=self.entry.get().strip()
  if not body:return
  try:p=self.target()
  except Exception as e:return messagebox.showwarning('Чат',str(e))
  self.entry.delete(0,'end');self.async_task(lambda:self.request('/message',dict(p,body=body)),lambda _:self.refresh())
 def progress_update(self,text):
  self.root.after(0,lambda:self.progress.config(text=text))
 def send_file(self):
  try:p=self.target()
  except Exception as e:return messagebox.showwarning('Чат',str(e))
  path=filedialog.askopenfilename()
  if not path:return
  size=os.path.getsize(path)
  if size>5*1024**3:return messagebox.showwarning('Ограничение','Максимальный размер файла 5 ГБ')
  if self.transfer_busy:return messagebox.showwarning('Передача','Дождитесь завершения текущей передачи')
  self.transfer_busy=True
  def work():
   # Persist the upload ID locally so interrupted uploads can resume after restart.
   marker=Path(path+'.lanconnect-upload.json')
   ident=None;offset=0;chunk_size=1024**2
   if marker.exists():
    try:
     meta=json.loads(marker.read_text(encoding='utf-8'))
     if meta.get('server')==self.url.get().rstrip('/') and meta.get('size')==size and meta.get('target')==p:
      status=self.request('/upload/status',{'upload_id':meta['upload_id']})
      if status['size']==size:
       ident=meta['upload_id'];offset=status['offset']
    except Exception:pass
   if ident is None:
    started=self.request('/upload/start',dict(p,name=Path(path).name,size=size))
    ident=started['upload_id'];offset=started['offset'];chunk_size=started['chunk_size']
    marker.write_text(json.dumps({'server':self.url.get().rstrip('/'),'size':size,'target':p,'upload_id':ident}),encoding='utf-8')
   digest=hashlib.sha256()
   with open(path,'rb') as stream:
    # Hash the entire source, including already-uploaded bytes.
    for part in iter(lambda:stream.read(1024**2),b''):digest.update(part)
    stream.seek(offset)
    while offset<size:
     chunk=stream.read(min(chunk_size,size-offset))
     if not chunk:raise RuntimeError('Исходный файл изменился во время передачи')
     query=urllib.parse.urlencode({'id':ident,'offset':offset})
     result=self.request('/upload/chunk?'+query,file=chunk)
     offset=result['offset']
     self.progress_update('Отправка: '+str(round(offset/max(size,1)*100,1))+'% — '+Path(path).name)
   result=self.request('/upload/finish',{'upload_id':ident,'sha256':digest.hexdigest()})
   marker.unlink(missing_ok=True)
   return result
  def worker():
   try:work();self.root.after(0,lambda:(self.progress.config(text='Файл отправлен'),self.refresh()))
   except Exception as e:self.root.after(0,lambda err=str(e):messagebox.showerror('Передача',err))
   finally:self.root.after(0,lambda:setattr(self,'transfer_busy',False))
  threading.Thread(target=worker,daemon=True).start()
 def download(self):
  fid=simpledialog.askinteger('Файл','Введите ID файла из переписки')
  if not fid:return
  self.async_task(lambda:self.request('/file/info?'+urllib.parse.urlencode({'id':fid})),self.download_file)
 def download_file(self,f):
  fid=f['id']
  if f.get('expired') or f.get('expires',0)<=int(time.time()):return messagebox.showwarning('Файл','Срок хранения истёк')
  path=filedialog.asksaveasfilename(initialfile=f['filename'])
  if not path:return
  if self.transfer_busy:return messagebox.showwarning('Передача','Дождитесь завершения текущей передачи')
  self.transfer_busy=True
  def worker():
   try:
    part_path=Path(path+'.lanpart');offset=part_path.stat().st_size if part_path.exists() else 0
    if offset>f['size']:raise RuntimeError('Частично скачанный файл больше исходного')
    url=self.url.get().rstrip('/')+'/download?'+urllib.parse.urlencode({'id':fid,'offset':offset})
    headers={'Authorization':'Bearer '+self.token}
    with urlopen(urllib.request.Request(url,headers=headers),timeout=120) as response,part_path.open('ab') as out:
     while True:
      chunk=response.read(1024*1024)
      if not chunk:break
      out.write(chunk);offset+=len(chunk)
      self.progress_update('Скачивание: '+str(round(offset/max(f['size'],1)*100,1))+'% — '+f['filename'])
    if offset!=f['size']:raise RuntimeError('Скачивание прервано. Повторите, чтобы продолжить')
    try:verify_download(part_path,f['size'],f.get('sha256'))
    except IntegrityError:
     part_path.unlink(missing_ok=True);raise
    part_path.replace(path)
    self.root.after(0,lambda:messagebox.showinfo('Готово','Файл сохранён'))
   except Exception as e:self.root.after(0,lambda err=str(e):messagebox.showerror('Скачивание',err))
   finally:self.root.after(0,lambda:setattr(self,'transfer_busy',False))
  threading.Thread(target=worker,daemon=True).start()
 def create_group(self):
  name=simpledialog.askstring('Группа','Название новой группы')
  if name:self.async_task(lambda:self.request('/group',{'name':name}),lambda _:self.refresh())
 def invite(self):
  groups=[g for g in self.state.get('groups',[]) if g['owner']==self.state['me']]
  if not groups:return messagebox.showinfo('Группы','Сначала создайте свою группу')
  gid=simpledialog.askinteger('Приглашение','ID группы (ваши: '+', '.join(str(g['id']) for g in groups)+')')
  if gid is None:return
  uid=simpledialog.askinteger('Приглашение','ID пользователя (виден в списке)')
  if uid:self.async_task(lambda:self.request('/invite',{'gid':gid,'uid':uid}),lambda _:self.refresh())
if __name__=='__main__':
 import sys
 if len(sys.argv)==3 and sys.argv[1]=='--self-test':
  from client_selftest import run
  raise SystemExit(run(sys.argv[2]))
 if os.name=='nt':
  import ctypes
  try:ctypes.windll.user32.SetProcessDPIAware()
  except OSError:pass
 App()
