"""Bounded, in-memory live sessions. Nothing survives server restart.
PCM is mono, signed little-endian 16 bit, 16000 Hz, up to 100 ms per packet.
No external service, discovery, STUN, TURN or Internet endpoint is used.
"""
import collections
import secrets
import threading
import time


class LiveError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class LiveSessions:
    MAX_SESSIONS = 16
    MAX_AUDIO = 3200
    MAX_FRAME = 2 * 1024 * 1024
    MAX_COMMANDS = 64

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.sessions = {}

    def _prune(self):
        now = self.clock()
        for sid, s in list(self.sessions.items()):
            deadline = 60 if s['status'] == 'pending' else 30
            if now - s['updated'] > deadline or now - s['created'] > 3600:
                del self.sessions[sid]

    @staticmethod
    def public(s):
        return {k: s[k] for k in ('id', 'kind', 'mode', 'requester', 'owner', 'status')}

    def start(self, uid, owner, kind, mode='view'):
        if kind not in ('audio', 'screen') or mode not in ('view', 'control'):
            raise LiveError(400, 'Неверный вид сеанса')
        if kind == 'audio':
            mode = 'view'
        if owner == uid:
            raise LiveError(400, 'Выберите другого пользователя')
        with self.lock:
            self._prune()
            if len(self.sessions) >= self.MAX_SESSIONS:
                raise LiveError(429, 'Лимит одновременных сеансов')
            # At most one audio call / screen session per participant and kind.
            for s in self.sessions.values():
                if s['kind'] == kind and {uid, owner} & {s['owner'], s['requester']}:
                    raise LiveError(409, 'Участник уже занят этим видом связи')
            sid = secrets.token_urlsafe(24)
            now = self.clock()
            s = dict(id=sid, requester=uid, owner=owner, kind=kind, mode=mode,
                     status='pending', created=now, updated=now, frame=b'', frame_seq=0,
                     commands=collections.deque(), queues={uid: collections.deque(maxlen=8), owner: collections.deque(maxlen=8)},
                     sequences={uid: -1, owner: -1})
            self.sessions[sid] = s
            return self.public(s)

    def _session(self, sid, uid, active=False):
        self._prune()
        s = self.sessions.get(sid)
        if not s:
            raise LiveError(410, 'Сеанс завершён или истёк')
        if uid not in (s['owner'], s['requester']):
            raise LiveError(403, 'Нет доступа к сеансу')
        if active and s['status'] != 'approved':
            raise LiveError(409, 'Нет согласия принимающей стороны')
        return s

    def list(self, uid):
        with self.lock:
            self._prune()
            return [self.public(s) for s in self.sessions.values() if uid in (s['requester'], s['owner'])]

    def decision(self, sid, uid, approve):
        if not isinstance(approve, bool):
            raise LiveError(400, 'Требуется явное решение')
        with self.lock:
            s = self._session(sid, uid)
            if uid != s['owner']:
                raise LiveError(403, 'Решение принимает только владелец')
            if s['status'] != 'pending':
                raise LiveError(409, 'Решение уже принято')
            if not approve:
                del self.sessions[sid]
                return {'status': 'denied'}
            s['status'] = 'approved'
            s['updated'] = self.clock()
            return self.public(s)

    def end(self, sid, uid):
        with self.lock:
            self._session(sid, uid)
            del self.sessions[sid]
            return {'status': 'ended'}

    def heartbeat(self, sid, uid):
        with self.lock:
            s = self._session(sid, uid, True)
            s['updated'] = self.clock()
            return self.public(s)

    def audio_push(self, sid, uid, sequence, raw):
        if not raw or len(raw) > self.MAX_AUDIO or len(raw) % 2:
            raise LiveError(400, 'Неверный размер PCM-пакета')
        if not isinstance(sequence, int) or sequence < 0:
            raise LiveError(400, 'Неверный номер пакета')
        with self.lock:
            s = self._session(sid, uid, True)
            if s['kind'] != 'audio':
                raise LiveError(400, 'Это не аудиосеанс')
            if sequence <= s['sequences'][uid]:
                raise LiveError(409, 'Повторный или устаревший пакет')
            s['sequences'][uid] = sequence
            other = s['owner'] if uid == s['requester'] else s['requester']
            s['queues'][other].append(raw)
            s['updated'] = self.clock()
            return {'ok': True}

    def audio_pull(self, sid, uid):
        with self.lock:
            s = self._session(sid, uid, True)
            if s['kind'] != 'audio':
                raise LiveError(400, 'Это не аудиосеанс')
            s['updated'] = self.clock()
            q = s['queues'][uid]
            return q.popleft() if q else b''

    def frame_push(self, sid, uid, raw):
        if not raw or len(raw) > self.MAX_FRAME or not raw.startswith(b'\xff\xd8'):
            raise LiveError(400, 'Нужен JPEG до 2 МиБ')
        with self.lock:
            s = self._session(sid, uid, True)
            if s['kind'] != 'screen' or s['owner'] != uid:
                raise LiveError(403, 'Экран передаёт только владелец')
            s['frame'] = raw
            s['frame_seq'] += 1
            s['updated'] = self.clock()
            return {'sequence': s['frame_seq']}

    def frame_pull(self, sid, uid):
        with self.lock:
            s = self._session(sid, uid, True)
            if s['kind'] != 'screen' or s['requester'] != uid:
                raise LiveError(403, 'Экран получает только запросивший')
            s['updated'] = self.clock()
            return s['frame']

    @staticmethod
    def validate_command(command):
        if not isinstance(command, dict):
            raise LiveError(400, 'Неверная команда')
        action = command.get('action')
        if action == 'click':
            x, y = command.get('x'), command.get('y')
            if type(x) not in (int, float) or type(y) not in (int, float) or not (0 <= x <= 1 and 0 <= y <= 1):
                raise LiveError(400, 'Координаты вне экрана')
            button = command.get('button', 'left')
            if button not in ('left', 'right'):
                raise LiveError(400, 'Неизвестная кнопка')
            return dict(action=action, x=x, y=y, button=button)
        if action == 'key':
            key = command.get('key')
            if key not in ('Return', 'BackSpace', 'Tab', 'Escape', 'Left', 'Right', 'Up', 'Down', 'Delete', 'space'):
                raise LiveError(400, 'Неизвестная клавиша')
            return dict(action=action, key=key)
        if action == 'text':
            text = command.get('text')
            if not isinstance(text, str) or not 1 <= len(text) <= 256 or any(ord(c) < 32 for c in text):
                raise LiveError(400, 'Неверный текст')
            return dict(action=action, text=text)
        raise LiveError(400, 'Неизвестное действие')

    def command_push(self, sid, uid, command):
        command = self.validate_command(command)
        with self.lock:
            s = self._session(sid, uid, True)
            if s['kind'] != 'screen' or s['mode'] != 'control' or s['requester'] != uid:
                raise LiveError(403, 'Управление не разрешено')
            if len(s['commands']) >= self.MAX_COMMANDS:
                raise LiveError(429, 'Очередь команд заполнена')
            s['commands'].append(command)
            s['updated'] = self.clock()
            return {'ok': True}

    def command_pull(self, sid, uid):
        with self.lock:
            s = self._session(sid, uid, True)
            if s['kind'] != 'screen' or s['owner'] != uid:
                raise LiveError(403, 'Команды получает только владелец')
            s['updated'] = self.clock()
            commands = list(s['commands'])
            s['commands'].clear()
            return {'commands': commands}


LIVE = LiveSessions()


def handle_live(handler, uid, path, query, post, user_exists):
    """Return True if handled. Authentication is performed by server.py first."""
    if not path.startswith('/live/'):
        return False
    def respond(obj=None, binary=None):
        handler.send(obj=obj, binary=binary)
    sid = query.get('id', [''])[0]
    try:
        if post:
            if path == '/live/audio':
                respond(LIVE.audio_push(sid, uid, int(query.get('sequence', ['-1'])[0]), handler.read(3200)))
            elif path == '/live/frame':
                respond(LIVE.frame_push(sid, uid, handler.read(LIVE.MAX_FRAME)))
            else:
                import json
                p = json.loads(handler.read(20000))
                sid = str(p.get('id', ''))
                if path == '/live/start':
                    owner = int(p['owner'])
                    if not user_exists(owner):
                        raise LiveError(400, 'Получатель не найден')
                    respond(LIVE.start(uid, owner, str(p['kind']), str(p.get('mode', 'view'))))
                elif path == '/live/decision':
                    respond(LIVE.decision(sid, uid, p.get('approve')))
                elif path == '/live/end':
                    respond(LIVE.end(sid, uid))
                elif path == '/live/heartbeat':
                    respond(LIVE.heartbeat(sid, uid))
                elif path == '/live/command':
                    respond(LIVE.command_push(sid, uid, p.get('command')))
                else:
                    raise LiveError(404, 'Неизвестный адрес')
        elif path == '/live/sessions':
            respond({'sessions': LIVE.list(uid)})
        elif path == '/live/audio':
            respond(binary=LIVE.audio_pull(sid, uid))
        elif path == '/live/frame':
            respond(binary=LIVE.frame_pull(sid, uid))
        elif path == '/live/commands':
            respond(LIVE.command_pull(sid, uid))
        else:
            raise LiveError(404, 'Неизвестный адрес')
    except LiveError as exc:
        handler.send(exc.status, {'error': str(exc)})
    return True
