"""Explicit-consent live UI for the Tk Windows client."""
from network import urlopen
import io
import json
import queue
import threading
import urllib.request
from urllib.parse import urlencode
import tkinter as tk
from tkinter import ttk, messagebox


class LiveClient:
    def __init__(self, app):
        self.app = app
        self.stopped = threading.Event()
        self.active = {}
        self.pending = {}
        self.asked = set()
        self.ended = set()
        self.base = app.url.get().rstrip('/')
        self.token = app.token
        self.me = app.state['me']
        self.names = {u['id']: u['name'] for u in app.state['users']}
        threading.Thread(target=self.poll, daemon=True).start()

    def request(self, path, obj=None, raw=None, binary=False):
        data = raw if raw is not None else json.dumps(obj).encode() if obj is not None else None
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={'Authorization': 'Bearer ' + self.token})
        with urlopen(req, timeout=5) as response:
            body = response.read()
            return body if binary else json.loads(body)

    def ui(self, fn):
        if not self.stopped.is_set():
            self.app.root.after(0, fn)

    def report(self, error):
        self.ui(lambda msg=str(error): self.app.progress.config(text='Связь: ' + msg))

    def start(self, kind, mode='view'):
        selected = self.app.selected
        if not selected or selected[0] != 'user':
            return messagebox.showinfo('Связь', 'Выберите человека в списке')
        owner = selected[1]
        def work():
            try:
                session = self.request('/live/start', dict(owner=owner, kind=kind, mode=mode))
                self.ui(lambda: self.waiting(session))
                self.report('Запрос отправлен, ожидается согласие')
            except Exception as exc:
                self.report(exc)
        threading.Thread(target=work, daemon=True).start()

    def waiting(self, session):
        sid = session['id']
        if sid in self.ended or self.stopped.is_set():return
        if sid in self.active or sid in self.pending:return
        window = tk.Toplevel(self.app.root)
        window.title('LAN Connect — ожидание согласия')
        ttk.Label(window, text='Ожидается согласие собеседника. Запрос действует до 60 секунд.').pack(padx=12, pady=12)
        ttk.Button(window, text='Отменить запрос', command=lambda: self.end(sid)).pack(padx=12, pady=8)
        window.protocol('WM_DELETE_WINDOW', lambda: self.end(sid))
        self.pending[sid] = window

    def poll(self):
        while not self.stopped.is_set():
            try:
                sessions = self.request('/live/sessions')['sessions']
                ids = {s['id'] for s in sessions}
                self.ui(lambda present=ids: self.remove_missing(present))
                for s in sessions:
                    if s['status'] == 'pending' and s['owner'] == self.me and s['id'] not in self.asked:
                        self.asked.add(s['id'])
                        self.ui(lambda session=s: self.ask(session))
                    elif s['status'] == 'approved':
                        self.ui(lambda session=s: self.activate(session))
            except Exception as exc:
                # Fail closed on connection loss; never keep a local desktop stream alive.
                self.ui(lambda: self.remove_missing(set()))
                self.report(exc)
            self.stopped.wait(1)

    def ask(self, session):
        who = self.names.get(session['requester'], str(session['requester']))
        description = ('аудиозвонок (микрофон включится после согласия)' if session['kind'] == 'audio'
                       else 'просмотр вашего экрана' if session['mode'] == 'view'
                       else 'просмотр экрана И управление мышью и клавиатурой')
        approve = messagebox.askyesno('LAN Connect — запрос разрешения',
                                     who + ' запрашивает ' + description + '.\nРазрешить этот сеанс?\nДоступ можно остановить отдельной кнопкой.')
        def work():
            try:
                self.request('/live/decision', dict(id=session['id'], approve=approve))
            except Exception as exc:
                self.report(exc)
        threading.Thread(target=work, daemon=True).start()

    def activate(self, s):
        sid = s['id']
        if sid in self.active or sid in self.ended or self.stopped.is_set():
            return
        pending = self.pending.pop(sid, None)
        if pending:pending.destroy()
        stop = threading.Event()
        window = tk.Toplevel(self.app.root)
        window.title('LAN Connect — активный сеанс')
        other = s['owner'] if self.me == s['requester'] else s['requester']
        title = self.names.get(other, str(other))
        title += ' • аудиозвонок' if s['kind'] == 'audio' else ' • просмотр экрана' if s['mode'] == 'view' else ' • УПРАВЛЕНИЕ ЭКРАНОМ'
        ttk.Label(window, text=title, wraplength=650).pack(padx=12, pady=8)
        tk.Button(window, text='ОСТАНОВИТЬ СЕАНС', bg='#af2034', fg='white',
                  command=lambda: self.end(sid)).pack(fill='x', padx=12, pady=8)
        window.protocol('WM_DELETE_WINDOW', lambda: self.end(sid))
        self.active[sid] = (stop, window)
        if s['kind'] == 'audio':
            ttk.Label(window, text='Микрофон включён. Для проверки используйте наушники.').pack(padx=12, pady=8)
            threading.Thread(target=self.audio, args=(sid, stop), daemon=True).start()
        elif self.me == s['owner']:
            window.attributes('-topmost', True)
            ttk.Label(window, text='Ваш основной экран передаётся.\n' +
                      ('Собеседник может выполнять щелчки и ввод.' if s['mode'] == 'control' else 'Управление вводом не разрешено.')).pack(padx=12, pady=8)
            threading.Thread(target=self.share, args=(sid, stop, s['mode']), daemon=True).start()
        else:
            canvas = tk.Canvas(window, width=960, height=540, bg='#091526', highlightthickness=0)
            canvas.pack(fill='both', expand=True)
            controls = queue.Queue(maxsize=32)
            if s['mode'] == 'control':
                canvas.bind('<Button-1>', lambda event: self.queue_click(controls, canvas, event, 'left'))
                canvas.bind('<Button-3>', lambda event: self.queue_click(controls, canvas, event, 'right'))
                row = ttk.Frame(window);row.pack(fill='x')
                text = ttk.Entry(row);text.pack(side='left', fill='x', expand=True)
                def send_text():
                    value = text.get()[:256]
                    if value:
                        self.enqueue(controls, dict(action='text', text=value));text.delete(0, 'end')
                ttk.Button(row, text='Ввести текст', command=send_text).pack(side='left')
                for key in ('Return', 'BackSpace', 'Tab', 'Escape'):
                    ttk.Button(row, text=key, command=lambda k=key: self.enqueue(controls, dict(action='key', key=k))).pack(side='left')
            threading.Thread(target=self.view, args=(sid, stop, canvas, controls), daemon=True).start()

    @staticmethod
    def enqueue(q, item):
        try:q.put_nowait(item)
        except queue.Full:pass

    def queue_click(self, q, canvas, event, button):
        box = getattr(canvas, 'image_box', None)
        if not box:return
        x, y, w, h = box
        if x <= event.x < x+w and y <= event.y < y+h:
            self.enqueue(q, dict(action='click', x=(event.x-x)/w, y=(event.y-y)/h, button=button))

    def remove_missing(self, present):
        for sid in set(self.active) | set(self.pending):
            if sid not in present:self.end(sid)

    def end(self, sid):
        self.ended.add(sid)
        pending = self.pending.pop(sid, None)
        if pending:pending.destroy()
        entry = self.active.pop(sid, None)
        if entry:
            entry[0].set()  # local capture and input stop before any network operation
            entry[1].destroy()
        def work():
            try:self.request('/live/end', dict(id=sid))
            except Exception:pass
        threading.Thread(target=work, daemon=True).start()

    def close(self):
        self.stopped.set()
        for sid in set(self.active) | set(self.pending):self.end(sid)

    def share(self, sid, stop, mode):
        from windows_native import screenshot, apply_command
        query = urlencode(dict(id=sid))
        try:
            while not stop.is_set():
                raw = screenshot()
                if stop.is_set():break
                self.request('/live/frame?' + query, raw=raw)
                if mode == 'control':
                    for command in self.request('/live/commands?' + query)['commands']:
                        if stop.is_set():break
                        apply_command(command)
                stop.wait(.25)
        except Exception as exc:self.report(exc)
        finally:self.ui(lambda: self.end(sid))

    def view(self, sid, stop, canvas, controls):
        from PIL import Image, ImageTk
        query = urlencode(dict(id=sid))
        def draw(image):
            if stop.is_set() or not canvas.winfo_exists():return
            w, h = max(canvas.winfo_width(), 1), max(canvas.winfo_height(), 1)
            image.thumbnail((w, h))
            iw, ih = image.size
            canvas.image_box = ((w-iw)//2, (h-ih)//2, iw, ih)
            canvas.picture = ImageTk.PhotoImage(image)
            canvas.delete('all')
            canvas.create_image(w//2, h//2, image=canvas.picture)
        try:
            while not stop.is_set():
                raw = self.request('/live/frame?' + query, binary=True)
                if raw:
                    with Image.open(io.BytesIO(raw)) as source:
                        if source.width > 4096 or source.height > 4096:raise RuntimeError('Слишком большой кадр')
                        image = source.convert('RGB')
                    self.ui(lambda picture=image: draw(picture))
                while not controls.empty() and not stop.is_set():
                    self.request('/live/command', dict(id=sid, command=controls.get_nowait()))
                stop.wait(.2)
        except Exception as exc:self.report(exc)
        finally:self.ui(lambda: self.end(sid))

    def audio(self, sid, stop):
        from windows_native import WaveDevice
        packets = queue.Queue(maxsize=4)
        def capture():
            device = None
            try:
                device = WaveDevice(True)
                while not stop.is_set():
                    raw = device.capture(stop)
                    if raw:
                        if packets.full():
                            try:packets.get_nowait()
                            except queue.Empty:pass
                        self.enqueue(packets, raw)
            except Exception as exc:self.report(exc);stop.set()
            finally:
                if device:device.close()
        def send():
            sequence = 0
            try:
                while not stop.is_set():
                    try:raw = packets.get(timeout=.1)
                    except queue.Empty:continue
                    if stop.is_set():break
                    self.request('/live/audio?' + urlencode(dict(id=sid, sequence=sequence)), raw=raw)
                    sequence += 1
            except Exception as exc:self.report(exc);stop.set()
        threading.Thread(target=capture, daemon=True).start()
        threading.Thread(target=send, daemon=True).start()
        device = None
        try:
            device = WaveDevice(False)
            while not stop.is_set():
                raw = self.request('/live/audio?' + urlencode(dict(id=sid)), binary=True)
                if raw and not stop.is_set():device.play(raw, stop)
                else:stop.wait(.025)
        except Exception as exc:self.report(exc)
        finally:
            stop.set()
            if device:device.close()
            self.ui(lambda: self.end(sid))
