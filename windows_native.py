"""Windows-only device adapters; importing is safe on other platforms.
WinMM PCM16 mono at 16 kHz. No video camera or background/unattended access.
"""
import ctypes as C
import os
import time

RATE = 16000
PACKET = 3200


def require_windows():
    if os.name != 'nt':
        raise RuntimeError('Захват звука и управление доступны в Windows-клиенте')


class WaveFormat(C.Structure):
    _pack_ = 2
    _fields_ = [('tag', C.c_uint16), ('channels', C.c_uint16), ('rate', C.c_uint32),
                ('bytes_per_sec', C.c_uint32), ('align', C.c_uint16),
                ('bits', C.c_uint16), ('extra', C.c_uint16)]


class WaveHeader(C.Structure):
    _fields_ = [('data', C.c_void_p), ('length', C.c_uint32), ('recorded', C.c_uint32),
                ('user', C.c_size_t), ('flags', C.c_uint32), ('loops', C.c_uint32),
                ('next', C.c_void_p), ('reserved', C.c_size_t)]


class WaveDevice:
    def __init__(self, recording):
        require_windows()
        self.lib = C.WinDLL('winmm')
        self.recording = recording
        self.prefix = 'waveIn' if recording else 'waveOut'
        self.handle = C.c_void_p()
        self.headers = []
        self.buffers = []
        self.opened = False
        for suffix in ('Open', 'PrepareHeader', 'UnprepareHeader', 'Close', 'Reset'):
            fn = getattr(self.lib, self.prefix + suffix)
            fn.restype = C.c_uint32
            if suffix == 'Open':
                fn.argtypes = [C.POINTER(C.c_void_p), C.c_uint32, C.POINTER(WaveFormat), C.c_size_t, C.c_size_t, C.c_uint32]
            elif suffix in ('Close', 'Reset'):
                fn.argtypes = [C.c_void_p]
            else:
                fn.argtypes = [C.c_void_p, C.POINTER(WaveHeader), C.c_uint32]
        for suffix in (('AddBuffer', 'Start') if recording else ('Write',)):
            fn = getattr(self.lib, self.prefix + suffix)
            fn.restype = C.c_uint32
            fn.argtypes = [C.c_void_p] if suffix == 'Start' else [C.c_void_p, C.POINTER(WaveHeader), C.c_uint32]
        fmt = WaveFormat(1, 1, RATE, RATE * 2, 2, 16, 0)
        self.check(getattr(self.lib, self.prefix + 'Open')(C.byref(self.handle), 0xffffffff, C.byref(fmt), 0, 0, 0))
        self.opened = True
        try:
            if recording:
                for _ in range(4):
                    buf = C.create_string_buffer(PACKET)
                    header = WaveHeader(C.addressof(buf), PACKET, 0, 0, 0, 0, None, 0)
                    self.buffers.append(buf)
                    self.headers.append(header)
                    self.call('PrepareHeader', header)
                    self.call('AddBuffer', header)
                self.check(self.lib.waveInStart(self.handle))
        except Exception:
            self.close()
            raise

    @staticmethod
    def check(result):
        if result:
            raise RuntimeError('Ошибка аудиоустройства Windows: ' + str(result))

    def call(self, name, header):
        self.check(getattr(self.lib, self.prefix + name)(self.handle, C.byref(header), C.sizeof(header)))

    def capture(self, stop):
        while not stop.is_set():
            for header in self.headers:
                if header.flags & 1:
                    raw = C.string_at(header.data, header.recorded)
                    header.recorded = 0
                    self.call('AddBuffer', header)
                    if raw:
                        return raw
            stop.wait(.005)
        return b''

    def play(self, raw, stop):
        buf = C.create_string_buffer(raw)
        header = WaveHeader(C.addressof(buf), len(raw), 0, 0, 0, 0, None, 0)
        self.call('PrepareHeader', header)
        try:
            self.call('Write', header)
            while not header.flags & 1 and not stop.wait(.005):
                pass
            if stop.is_set():
                self.check(self.lib.waveOutReset(self.handle))
        finally:
            self.call('UnprepareHeader', header)

    def close(self):
        if not self.opened:
            return
        self.opened = False
        getattr(self.lib, self.prefix + 'Reset')(self.handle)
        for header in self.headers:
            getattr(self.lib, self.prefix + 'UnprepareHeader')(self.handle, C.byref(header), C.sizeof(header))
        getattr(self.lib, self.prefix + 'Close')(self.handle)


def screenshot():
    require_windows()
    from PIL import ImageGrab
    import io
    image = ImageGrab.grab(all_screens=False)
    image.thumbnail((1280, 720))
    out = io.BytesIO()
    image.convert('RGB').save(out, 'JPEG', quality=65)
    return out.getvalue()


class KeyboardInput(C.Structure):
    _fields_ = [('vk', C.c_uint16), ('scan', C.c_uint16), ('flags', C.c_uint32),
                ('time', C.c_uint32), ('extra', C.c_size_t)]


class MouseInput(C.Structure):
    _fields_ = [('dx', C.c_int32), ('dy', C.c_int32), ('data', C.c_uint32),
                ('flags', C.c_uint32), ('time', C.c_uint32), ('extra', C.c_size_t)]


class InputUnion(C.Union):
    _fields_ = [('keyboard', KeyboardInput), ('mouse', MouseInput)]


class Input(C.Structure):
    _anonymous_ = ('value',)
    _fields_ = [('type', C.c_uint32), ('value', InputUnion)]


def apply_command(command):
    require_windows()
    from live import LiveSessions
    command = LiveSessions.validate_command(command)
    user = C.WinDLL('user32', use_last_error=True)
    user.SendInput.argtypes = [C.c_uint32, C.POINTER(Input), C.c_int]
    user.SendInput.restype = C.c_uint32
    user.GetSystemMetrics.argtypes = [C.c_int]
    user.GetSystemMetrics.restype = C.c_int
    user.SetCursorPos.argtypes = [C.c_int, C.c_int]
    user.SetCursorPos.restype = C.c_int
    if command['action'] == 'click':
        width, height = user.GetSystemMetrics(0), user.GetSystemMetrics(1)
        if not user.SetCursorPos(round(command['x'] * (width - 1)), round(command['y'] * (height - 1))):
            raise RuntimeError('Windows не разрешила перемещение указателя')
        down, up = (2, 4) if command['button'] == 'left' else (8, 16)
        events = (Input * 2)(Input(type=0, value=InputUnion(mouse=MouseInput(flags=down))),
                             Input(type=0, value=InputUnion(mouse=MouseInput(flags=up))))
    elif command['action'] == 'key':
        keys = dict(Return=13, BackSpace=8, Tab=9, Escape=27, Left=37, Up=38,
                    Right=39, Down=40, Delete=46, space=32)
        vk = keys[command['key']]
        events = (Input * 2)(Input(type=1, value=InputUnion(keyboard=KeyboardInput(vk=vk))),
                             Input(type=1, value=InputUnion(keyboard=KeyboardInput(vk=vk, flags=2))))
    else:
        raw = command['text'].encode('utf-16-le')
        seq = []
        for i in range(0, len(raw), 2):
            unit = int.from_bytes(raw[i:i+2], 'little')
            seq += [Input(type=1, value=InputUnion(keyboard=KeyboardInput(scan=unit, flags=4))),
                    Input(type=1, value=InputUnion(keyboard=KeyboardInput(scan=unit, flags=6)))]
        events = (Input * len(seq))(*seq)
    if user.SendInput(len(events), events, C.sizeof(Input)) != len(events):
        raise RuntimeError('Windows отклонила ввод (например, окно администратора/UAC)')
