"""Prueba integrada real de interfaz Tk, revisión, guardado y exportación."""
import json
import ctypes
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import ImageGrab
from vozafecto.gui import App
from vozafecto.core import save_report
from vozafecto.embox import embox_export

root = Path(__file__).resolve().parents[1]
validation = root/'validacion'
validation.mkdir(exist_ok=True)
app = App(demo=True)
attempts = 0
result = {}

def finish():
    global attempts
    attempts += 1
    if app.busy or app.report is None:
        if attempts>100:
            result['error'] = 'No se completó análisis de demo en 50 s.'
            app.destroy()
            return
        app.after(500,finish)
        return
    try:
        assert len(app.report['candidates'])>0
        candidate = app.report['candidates'][0]
        app.tree.selection_set(candidate['id'])
        app.select_candidate()
        app.author.set('Verificación técnica automatizada')
        app.comment.set('Fixture sintética: verificar persistencia; no evaluación humana de afecto.')
        app.review('segment')
        app.review('accepted')
        assert candidate['level']=='rojo'
        with tempfile.TemporaryDirectory(prefix='voz_gui_') as temp:
            output = Path(temp)/'analisis'
            report_path = save_report(app.report,output)
            with patch('vozafecto.gui.filedialog.askopenfilename',return_value=str(report_path)):
                app.open_report()
            assert len(app.report['review_log'])==2
            assert app.report['candidates'][0]['review']['status']=='accepted'
            exported = embox_export(app.report,Path(temp)/'embox')
            assert exported['exported_count']==1
        app.tree.selection_set(candidate['id'])
        app.select_candidate()
        app.update()
        app.fig.savefig(validation/'graficas_demo.png',dpi=150)
        # Capturar sólo nuestra ventana; nunca el escritorio u otras aplicaciones.
        hwnd = ctypes.windll.user32.GetAncestor(app.winfo_id(),2)
        ImageGrab.grab(window=hwnd).save(validation/'interfaz_demo.png')
        result.update({'gui':'PASS','source_verified':True,'review_roundtrip':'PASS',
                       'export_accepted_pin':'PASS','synthetic_only':True,
                       'windows':len(app.report['windows']),'candidates':len(app.report['candidates'])})
    except Exception as e:
        result['error'] = repr(e)
    finally:
        app.destroy()

app.after(1000,finish)
app.mainloop()
(validation/'prueba_gui.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
if 'error' in result:
    raise SystemExit(1)
