"""Arranca únicamente nuestro EXE y captura únicamente su ventana por HWND."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import time

from PIL import ImageGrab

root = Path(__file__).resolve().parents[1]
executable = root/'dist'/'EMBoxVozAfectiva.exe'
out = root/'validacion'
out.mkdir(exist_ok=True)
proc = subprocess.Popen([str(executable),'--demo'],cwd=root,
                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
user32 = ctypes.windll.user32
callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
hwnd = None
started = time.monotonic()

def own_pids():
    command = f"Get-CimInstance Win32_Process | Where-Object {{ $_.ProcessId -eq {proc.pid} -or $_.ParentProcessId -eq {proc.pid} }} | Select-Object -ExpandProperty ProcessId | ConvertTo-Json"
    result = subprocess.run(['powershell','-NoProfile','-Command',command],capture_output=True,text=True,
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    data = json.loads(result.stdout or '[]')
    return set(data if isinstance(data,list) else [data])

result = {}
try:
    while time.monotonic()-started<40 and hwnd is None:
        pids = own_pids()
        @callback_type
        def scan(window,_):
            global hwnd
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(window,ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(window):
                title = ctypes.create_unicode_buffer(512)
                user32.GetWindowTextW(window,title,512)
                if title.value.startswith('EMBox') and 'seminario' in title.value:
                    hwnd = window
            return True
        user32.EnumWindows(scan,0)
        if hwnd is None:
            time.sleep(.5)
    if hwnd is None:
        raise RuntimeError('El ejecutable no mostró su ventana en 40 s.')
    time.sleep(6)
    ImageGrab.grab(window=hwnd).save(out/'ejecutable_demo.png')
    result = {'executable_opened':'PASS','capture':'ejecutable_demo.png','own_pid':proc.pid,
              'elapsed_sec':round(time.monotonic()-started,2),'scope':'Apertura del EXE y captura de demo; inspección visual requerida.'}
finally:
    if hwnd:
        user32.PostMessageW(hwnd,0x10,0,0)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True)
    (out/'prueba_exe.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
