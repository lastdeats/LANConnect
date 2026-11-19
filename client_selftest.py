"""Packaged Windows client probe. No microphone, screen capture or input injection."""
import ctypes
import io
import json
import os
from pathlib import Path
import sys


def run(output):
    report={'ok':False,'platform':sys.platform,'checks':{},'hardware_tested':False}
    root=None
    try:
        if os.name!='nt':raise RuntimeError('Windows is required for the client probe')
        import tkinter as tk
        from tkinter import ttk
        from PIL import Image, ImageTk, ImageGrab
        import windows_native as native
        expected_header=48 if ctypes.sizeof(ctypes.c_void_p)==8 else 32
        expected_input=40 if ctypes.sizeof(ctypes.c_void_p)==8 else 28
        if ctypes.sizeof(native.WaveFormat)!=18 or ctypes.sizeof(native.WaveHeader)!=expected_header or ctypes.sizeof(native.Input)!=expected_input:
            raise RuntimeError('Native structure ABI mismatch')
        ctypes.WinDLL('winmm');ctypes.WinDLL('user32')
        report['checks']['native_libraries_and_abi']=True
        root=tk.Tk();root.withdraw()
        ttk.Label(root,text='LAN Connect TAMBOV')
        root.update_idletasks()
        report['checks']['tk_ttk']=True
        image=Image.new('RGB',(4,4),(10,30,60));raw=io.BytesIO();image.save(raw,'JPEG')
        raw.seek(0)
        with Image.open(raw) as decoded:
            decoded.load();photo=ImageTk.PhotoImage(decoded,master=root)
            if photo.width()!=4:raise RuntimeError('Pillow/Tk bridge failed')
        report['checks']['pillow_jpeg_and_tk_bridge']=True
        report['checks']['screen_capture_module_import_only']=ImageGrab is not None
        report['ok']=True
    except Exception as error:
        report['error']=str(error)
    finally:
        if root is not None:root.destroy()
        Path(output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if report['ok'] else 1
