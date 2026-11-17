"""Interfaz local de inspección y revisión de candidatos."""
from __future__ import annotations
import argparse
import io
import json
import queue
import shutil
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
import soundfile as sf
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from .affect import apply_model
from .core import Config, analyze, load_audio, review_candidate, save_report
from .embox import EmboxExportError, embox_export


def resource_root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))


class App(tk.Tk):
    def __init__(self, demo=False):
        super().__init__()
        self.title('EMBox · Voz Afectiva | Prototipo de seminario 0.1')
        self.geometry(f'{min(1420,self.winfo_screenwidth()-40)}x{min(900,self.winfo_screenheight()-90)}')
        self.minsize(1100, 720)
        self.configure(bg='#f2f5fa')
        self.report = None
        self.audio = None
        self.sr = None
        self.events = queue.Queue()
        self.busy = False
        self.audio_path = tk.StringVar()
        self.turns_path = tk.StringVar()
        self.model_path = tk.StringVar()
        self.start = tk.StringVar(value='0')
        self.end = tk.StringVar(value='4')
        self.speaker = tk.StringVar(value='hablante_1')
        self.channel = tk.StringVar(value='0')
        self.floor = tk.StringVar(value='60')
        self.ceiling = tk.StringVar(value='500')
        self.single = tk.BooleanVar(value=False)
        self.author = tk.StringVar()
        self.comment = tk.StringVar()
        self.status = tk.StringVar(value='Cargue audio y seleccione una referencia del hablante objetivo.')
        self.selection_artists = []
        self._ui()
        self.after(100, self.poll)
        if demo:
            self.after(250, self.demo)

    def _ui(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TFrame', background='#f2f5fa')
        style.configure('TLabel', background='#f2f5fa', font=('Segoe UI', 10))
        style.configure('TButton', font=('Segoe UI', 10), padding=(9, 5))
        style.configure('Title.TLabel', foreground='#17385b', font=('Segoe UI', 21, 'bold'))
        style.configure('Treeview', rowheight=26, font=('Segoe UI', 10))
        style.configure('Treeview.Heading', font=('Segoe UI', 10, 'bold'))
        top = ttk.Frame(self, padding=(18, 12))
        top.pack(fill='x')
        ttk.Label(top, text='EMBox  /  Voz Afectiva', style='Title.TLabel').pack(side='left')
        ttk.Label(top, text='SEMINARIO · PRUEBA DE CONCEPTO', foreground='#516879').pack(side='right')
        toolbar = ttk.Frame(self, padding=(18, 0, 18, 6))
        toolbar.pack(fill='x')
        ttk.Button(toolbar, text='1  Cargar audio', command=self.choose_audio).pack(side='left')
        ttk.Entry(toolbar, textvariable=self.audio_path).pack(side='left', fill='x', expand=True, padx=8)
        ttk.Button(toolbar, text='Abrir análisis JSON', command=self.open_report).pack(side='left', padx=4)
        ttk.Button(toolbar, text='Demostración sintética', command=self.demo).pack(side='left', padx=4)
        controls = ttk.Frame(self, padding=(18, 2, 18, 6))
        controls.pack(fill='x')
        for label, var, width in [('Referencia inicio (s)', self.start, 7), ('Fin (s)', self.end, 7),
                                   ('Hablante', self.speaker, 15), ('Canal (desde 0)', self.channel, 4),
                                   ('F0 mín.', self.floor, 5), ('F0 máx.', self.ceiling, 5)]:
            ttk.Label(controls, text=label).pack(side='left', padx=(0, 4))
            ttk.Entry(controls, textvariable=var, width=width).pack(side='left', padx=(0, 10))
        ttk.Checkbutton(controls, text='Declaro un solo hablante', variable=self.single).pack(side='left')
        self.run_button = ttk.Button(controls, text='2  Analizar', command=self.run_analysis)
        self.run_button.pack(side='right')
        extra = ttk.Frame(self, padding=(18, 0, 18, 6))
        extra.pack(fill='x')
        ttk.Button(extra, text='Turnos / transcripción…', command=self.choose_turns).pack(side='left')
        ttk.Label(extra, textvariable=self.turns_path, width=40).pack(side='left', padx=6)
        ttk.Button(extra, text='Modelo afectivo entrenado…', command=self.choose_model).pack(side='left')
        self.model_label = ttk.Label(extra, text='Modelo: sin cargar · no hay predicción emocional', foreground='#8a5314')
        self.model_label.pack(side='left', padx=8)
        self.progress = ttk.Progressbar(self, mode='determinate')
        self.progress.pack(fill='x', padx=18)
        pane = ttk.Panedwindow(self, orient='horizontal')
        left, right = ttk.Frame(pane), ttk.Frame(pane)
        pane.add(left, weight=3)
        pane.add(right, weight=2)
        self.fig = Figure(figsize=(8.5, 4.5), dpi=100, facecolor='#ffffff')
        self.axes = self.fig.subplots(4, 1, sharex=True)
        self.canvas = FigureCanvasTkAgg(self.fig, master=left)
        self.canvas.get_tk_widget().pack(fill='both', expand=True)
        NavigationToolbar2Tk(self.canvas, left).update()
        self.canvas.mpl_connect('button_press_event', self.click_plot)
        ttk.Label(left, text='Clic izquierdo: inicio de referencia · clic derecho: fin · barra inferior: zoom y navegación',
                  foreground='#516879').pack(anchor='w')
        ttk.Label(right, text='Candidatos → revisión humana → PIN', font=('Segoe UI', 13, 'bold')).pack(anchor='w', pady=(0, 4))
        ttk.Label(right, text='Amarillo: alerta   Naranja: segmento   Rojo: PIN aceptado', foreground='#516879').pack(anchor='w')
        tree_frame = ttk.Frame(right)
        tree_frame.pack(fill='x', pady=6)
        self.tree = ttk.Treeview(tree_frame, columns=('time','priority','state'), show='tree headings', height=6)
        self.tree.heading('#0', text='ID')
        self.tree.column('#0', width=65, stretch=False)
        for name, label, width in [('time','Intervalo (s)',135), ('priority','Prior.',45), ('state','Revisión',110)]:
            self.tree.heading(name, text=label)
            self.tree.column(name, width=width)
        self.tree.pack(side='left', fill='x', expand=True)
        scrollbar = ttk.Scrollbar(tree_frame, orient='vertical', command=self.tree.yview)
        scrollbar.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.tag_configure('pending', background='#fff0ac')
        self.tree.tag_configure('segment', background='#ffd0a0')
        self.tree.tag_configure('accepted', background='#ffb6b4')
        self.tree.tag_configure('rejected', background='#e1e5e9')
        self.tree.bind('<<TreeviewSelect>>', self.select_candidate)
        self.detail = tk.Text(right, height=10, wrap='word', bg='white', fg='#19344a',
                              font=('Consolas', 10), relief='flat', padx=10, pady=8)
        self.detail.pack(fill='both', expand=True)
        self.set_detail('Cada intervalo conserva mediciones, condiciones de validez y procedencia.\n\n'
                        'Seleccione un candidato para revisar sus evidencias y el texto alineado.\n\n'
                        'Los índices de contraste no son probabilidades de emoción.\n'
                        'Valencia/activación aprendidas sólo aparecen si se carga un modelo entrenado y evaluado.')
        review = ttk.Frame(right, padding=(0, 6))
        review.pack(fill='x')
        ttk.Label(review, text='Revisor').pack(side='left')
        ttk.Entry(review, textvariable=self.author).pack(side='left', fill='x', expand=True, padx=6)
        ttk.Button(review, text='Escuchar intervalo', command=self.play).pack(side='right')
        ttk.Label(right,text='Justificación de la revisión').pack(anchor='w')
        ttk.Entry(right, textvariable=self.comment).pack(fill='x', pady=(0, 5))
        buttons = ttk.Frame(right)
        buttons.pack(fill='x')
        for label, state in [('Marcar segmento', 'segment'), ('Aceptar PIN', 'accepted'), ('Descartar', 'rejected'), ('Reabrir', 'pending')]:
            ttk.Button(buttons, text=label, command=lambda s=state: self.review(s)).pack(side='left', padx=(0, 3))
        # Reservar revisión y botones antes de asignar al detalle el espacio restante.
        for widget in right.winfo_children():
            widget.pack_forget()
        for row, widget in enumerate(right.winfo_children()):
            widget.grid(row=row,column=0,sticky='nsew' if row==3 else 'ew',pady=(0,3))
        right.columnconfigure(0,weight=1)
        right.rowconfigure(3,weight=1)
        export = ttk.Frame(self, padding=(18, 0, 18, 8))
        ttk.Button(export, text='3  Guardar análisis + CSV', command=self.save).pack(side='left')
        ttk.Button(export, text='4  Exportar PIN a EmBox', command=self.export).pack(side='left', padx=8)
        ttk.Label(export, text='La exportación usa PIN aceptados; el detalle afectivo permanece en este visor.',
                  foreground='#516879').pack(side='left')
        ttk.Label(self, textvariable=self.status, padding=(18, 7), foreground='#17385b', background='#dfe9f3').pack(side='bottom',fill='x')
        export.pack(side='bottom',fill='x')
        pane.pack(fill='both',expand=True,padx=18,pady=8)
        self.plot()

    def set_detail(self, text):
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        self.detail.insert('1.0', text)
        self.detail.configure(state='disabled')

    def choose_audio(self):
        path = filedialog.askopenfilename(title='Audio original', filetypes=[('Audio','*.wav *.flac *.mp3 *.ogg *.m4a'),('Todos','*.*')])
        if path:
            self.audio_path.set(path)
            self.status.set('Audio seleccionado. Indique referencia y hablante; no se presupone neutralidad.')

    def choose_turns(self):
        path = filedialog.askopenfilename(title='CSV anotado o JSON segments', filetypes=[('Turnos / transcripción','*.csv *.json')])
        if path:
            self.turns_path.set(path)
            self.status.set('Turnos cargados como insumo externo. No se infiere identidad a partir del texto.')

    def choose_model(self):
        path = filedialog.askopenfilename(title='Modelo Ridge entrenado con vozafecto train', filetypes=[('Modelo','*.json')])
        if path:
            self.model_path.set(path)
            self.model_label.configure(text='Modelo seleccionado: ' + Path(path).name)
            if self.report:
                try:
                    apply_model(self.report, path)
                    self.plot()
                    self.status.set('Predicciones experimentales cargadas. Revise métricas del corpus en el JSON.')
                except Exception as e:
                    messagebox.showerror('Modelo incompatible', str(e))

    def demo(self):
        if self.busy:
            return
        persistent = Path.home()/'AppData'/'Local'/'EMBoxVozAfectiva'/'demo'
        persistent.mkdir(parents=True,exist_ok=True)
        demo_audio = persistent/'demo_sintetica.wav'
        shutil.copy2(resource_root()/'data'/'demo_sintetica.wav',demo_audio)
        self.audio_path.set(str(demo_audio))
        self.turns_path.set('')
        self.start.set('0')
        self.end.set('4')
        self.speaker.set('sintetico_A')
        self.single.set(True)
        self.channel.set('0')
        self.model_path.set('')
        self.model_label.configure(text='DEMO SINTÉTICA · sin emociones ni voces humanas')
        self.run_analysis()

    def run_analysis(self):
        if self.busy:
            return
        try:
            path = self.audio_path.get()
            start, end = float(self.start.get()), float(self.end.get())
            cfg = Config(channel=int(self.channel.get()), pitch_floor=float(self.floor.get()), pitch_ceiling=float(self.ceiling.get()))
            speaker, single, turns, model = self.speaker.get(), self.single.get(), self.turns_path.get(), self.model_path.get()
            if not Path(path).is_file():
                raise ValueError('Seleccione un archivo de audio existente.')
        except Exception as e:
            messagebox.showerror('Revise los parámetros', str(e))
            return
        self.busy = True
        self.run_button.configure(state='disabled')
        self.status.set('Analizando localmente… ventanas de 2 s, paso de 0.5 s. La interfaz continúa disponible.')
        self.progress['value'] = 0
        def work():
            try:
                report = analyze(path, start, end, cfg=cfg, speaker=speaker, single_speaker=single,
                                 turns_path=turns or None, progress=lambda p: self.events.put(('progress', p)))
                if model:
                    apply_model(report, model)
                audio, sr, _ = load_audio(path, cfg.channel)
                self.events.put(('done', (report, audio, sr)))
            except Exception as e:
                self.events.put(('error', str(e)))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'progress':
                    self.progress['value'] = value*100
                elif kind == 'done':
                    self.report, self.audio, self.sr = value
                    self.busy = False
                    self.run_button.configure(state='normal')
                    self.refresh_candidates()
                    self.plot()
                    valid = sum(w['quality']['eligible'] for w in self.report['windows'])
                    self.status.set(f"Análisis listo · {len(self.report['windows'])} ventanas · {valid} válidas · "
                                    f"{len(self.report['candidates'])} candidatos · ninguno es PIN hasta su revisión.")
                    if self.report['candidates']:
                        self.tree.selection_set(self.report['candidates'][0]['id'])
                elif kind == 'error':
                    self.busy = False
                    self.run_button.configure(state='normal')
                    self.status.set('Análisis detenido: revise entrada y referencia.')
                    messagebox.showerror('No se pudo analizar', value)
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def refresh_candidates(self):
        self.tree.delete(*self.tree.get_children())
        states = {'pending':'Alerta', 'segment':'Segmento', 'accepted':'PIN', 'rejected':'Descartado'}
        for c in self.report['candidates']:
            self.tree.insert('', 'end', iid=c['id'], text=c['id'],
                             values=(f"{c['start']:.3f} – {c['end']:.3f}", c.get('priority',''), states[c['review']['status']]),
                             tags=(c['review']['status'],))

    def plot(self):
        for ax in self.axes:
            ax.clear()
            ax.grid(alpha=.2)
        self.selection_artists = []
        for ax, label in zip(self.axes, ['Señal digital', 'F0 (Hz)', 'RMS (dBFS)', 'Contraste vocal']):
            ax.set_ylabel(label, fontsize=9)
        if self.report and self.audio is not None:
            r = self.report
            stride = max(1, len(self.audio)//45000)
            self.axes[0].plot(np.arange(0,len(self.audio),stride)/self.sr, self.audio[::stride], color='#345a7d', lw=.6)
            times = [(w['start']+w['end'])/2 for w in r['windows']]
            to_values = lambda key: [w['measures'][key] if w['measures'][key] is not None else np.nan for w in r['windows']]
            self.axes[1].plot(times,to_values('f0_median_hz'),color='#227f8d',lw=1.5, marker='.',ms=3)
            self.axes[2].plot(times,to_values('rms_dbfs'),color='#7264ac',lw=1.2)
            activation = [w['comparison'].get('activation_index') for w in r['windows']]
            instability = [w['comparison'].get('instability_index') for w in r['windows']]
            self.axes[3].plot(times,[v if v is not None else np.nan for v in activation],label='Activación descriptiva',color='#136d92')
            self.axes[3].plot(times,[v if v is not None else np.nan for v in instability],label='Inestabilidad descriptiva',color='#aa7040')
            for pred_key, color in [('arousal','#8a3b91'), ('valence','#3a9561')]:
                predictions = r['affective_model'].get('predictions', [])
                if predictions:
                    self.axes[3].plot([(p['start']+p['end'])/2 for p in predictions], [p[pred_key] for p in predictions],
                                      label=pred_key+' (modelo; escala −1…1)',color=color,ls='--')
            self.axes[3].legend(loc='upper left', fontsize=7)
            for ax in self.axes:
                ax.axvspan(r['baseline']['start'],r['baseline']['end'],color='#4aa986',alpha=.17)
                for candidate in r['candidates']:
                    color = {'amarillo':'#edc23a','naranja':'#e88334','rojo':'#d44b4b','gris':'#9b9b9b'}[candidate['level']]
                    ax.axvspan(candidate['start'],candidate['end'],color=color,alpha=.15)
            self.axes[0].set_title(Path(r['source']['path']).name + '  |  verde: referencia seleccionada', loc='left', fontsize=10)
            self.axes[0].set_xlim(0,r['source']['duration_sec'])
        else:
            self.axes[0].set_title('Seleccione un audio o abra la demostración sintética', loc='left', fontsize=10)
        self.axes[3].set_xlabel('Tiempo del audio decodificado (s)')
        self.fig.tight_layout(pad=1.1)
        self.canvas.draw_idle()

    def click_plot(self, event):
        if event.xdata is not None and event.inaxes in self.axes:
            if event.button == 1:
                self.start.set(f'{max(0,event.xdata):.3f}')
            elif event.button == 3:
                self.end.set(f'{max(0,event.xdata):.3f}')

    def selected(self):
        if not self.report or not self.tree.selection():
            return None
        return next(c for c in self.report['candidates'] if c['id'] == self.tree.selection()[0])

    def select_candidate(self, _event=None):
        c = self.selected()
        if not c:
            return
        for artist in self.selection_artists:
            artist.remove()
        self.selection_artists = [ax.axvspan(c['start'],c['end'],color='#254d71',alpha=.1) for ax in self.axes]
        self.canvas.draw_idle()
        lines = [f"{c['id']}  |  {c['start']:.3f}–{c['end']:.3f} s", c['summary'],
                 f"Prioridad {c.get('priority',1)} · revisión: {c['review']['status']}", '',
                 'MEDICIONES QUE MOTIVAN LA ALERTA']
        for e in c['evidence']:
            lines.append(f"{e['feature']}: {e['observed']:.3f} {e['unit']}\n"
                         f"  referencia {e['reference']:.3f}; desviación robusta {e['robust_deviation']:+.2f}")
        windows = [w for w in self.report['windows'] if w['id'] in c['window_ids']]
        if windows:
            lines += ['', 'DETALLE DE PRIMERA VENTANA']
            for key, value in windows[0]['measures'].items():
                if value is None:
                    lines.append(key + ': no disponible — ' + windows[0]['invalid_reasons'].get(key,''))
                else:
                    lines.append(f"{key}: {value:.4f} {self.report['feature_dictionary'][key]['unit']}")
            contexts = {x['id']: x for w in windows for x in w['context']}
            lines += ['', 'CONTENIDO / INTERACCIÓN APORTADOS']
            if not contexts:
                lines.append('Sin transcripción/turnos alineados. No se inventa contenido.')
            for x in contexts.values():
                lines.append(f"[{x['start']:.3f}–{x['end']:.3f}] {x['speaker']}: {x['text']}\n"
                             f"Contenido: {x['content_note']}\nInteracción: {x['interaction_note']}")
        lines += ['', 'INTERPRETACIÓN AFECTIVA', self.report['affective_model'].get('interpretation',
                  'Sin modelo afectivo entrenado. Los índices son contrastes descriptivos exploratorios.'),
                  'Revisar también ruido, contenido fonético, micrófono y hablante.',
                  '', 'REVISIÓN', c['review']['author'], c['review']['comment']]
        self.set_detail('\n'.join(lines))
        self.comment.set(c['review']['comment'])

    def review(self, state):
        c = self.selected()
        if not c:
            return
        try:
            review_candidate(self.report,c['id'],state,self.author.get(),self.comment.get())
            cid = c['id']
            self.refresh_candidates()
            self.tree.selection_set(cid)
            self.plot()
            self.status.set('Revisión registrada en memoria. Guarde el análisis para conservar autor, justificación e historial.')
        except ValueError as e:
            messagebox.showerror('Revisión incompleta', str(e))

    def play(self):
        c = self.selected()
        if not c or self.audio is None:
            return
        try:
            import winsound
            segment = self.audio[round(c['start']*self.sr):round(c['end']*self.sr)]
            buffer = io.BytesIO()
            sf.write(buffer, segment, self.sr, format='WAV', subtype='PCM_16')
            audio_bytes = buffer.getvalue()
            threading.Thread(target=lambda: winsound.PlaySound(audio_bytes,winsound.SND_MEMORY),daemon=True).start()
            self.status.set('Reproduciendo el intervalo seleccionado.')
        except Exception as e:
            messagebox.showerror('Reproducción', str(e))

    def save(self):
        if not self.report:
            return
        path = filedialog.askdirectory(title='Carpeta nueva para análisis y CSV')
        if path:
            try:
                save_report(self.report,path)
                self.status.set('Análisis, mediciones y revisiones guardados en ' + path)
            except Exception as e:
                messagebox.showerror('Guardar',str(e))

    def export(self):
        if not self.report:
            return
        if not any(c['review']['status']=='accepted' for c in self.report['candidates']):
            messagebox.showinfo('Sin PIN aceptados','Revise un candidato, indique revisor y justificación, y acepte el PIN antes de exportar.')
            return
        path = filedialog.askdirectory(title='Carpeta nueva para proyecto EmBox')
        if path:
            try:
                try:
                    result = embox_export(self.report,Path(path))
                except EmboxExportError as e:
                    if 'No se encontró el JAR' not in str(e):
                        raise
                    jar = filedialog.askopenfilename(title='Seleccione el plugin de audio de su instalación EmBox',
                                                     filetypes=[('Plugin de EmBox','*.jar')])
                    if not jar:
                        self.status.set('Seleccione el JAR de su EmBox para crear el proyecto; el análisis se puede guardar por separado.')
                        return
                    result = embox_export(self.report,Path(path),audio_jar=Path(jar))
                save_report(self.report,path)
                self.status.set('Exportado proyecto EmBox: ' + str(result['project_path']))
                messagebox.showinfo('Exportación lista','Abra la carpeta exportada como proyecto en EmBox.\n\n'
                                    'La interfaz heredada puede usar sus propios colores y no muestra el panel afectivo de este visor.')
            except Exception as e:
                messagebox.showerror('Exportar EmBox',str(e))

    def open_report(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(title='Análisis guardado',filetypes=[('Análisis','*.json')])
        if not path:
            return
        try:
            report = json.loads(Path(path).read_text(encoding='utf-8-sig'))
            if report.get('schema_version')!='vozafecto-0.1':
                raise ValueError('Seleccione un analisis_voz.json de este prototipo.')
            audio, sr, meta = load_audio(report['source']['path'],report['config']['channel'])
            if meta['sha256'] != report['source']['sha256']:
                raise ValueError('El audio original cambió; no se reabre como si fuera la misma fuente.')
            self.report, self.audio, self.sr = report, audio, sr
            self.audio_path.set(report['source']['path'])
            self.start.set(str(report['baseline']['start']))
            self.end.set(str(report['baseline']['end']))
            self.speaker.set(report['baseline']['speaker'])
            self.channel.set(str(report['config']['channel']))
            self.floor.set(str(report['config']['pitch_floor']))
            self.ceiling.set(str(report['config']['pitch_ceiling']))
            self.turns_path.set((report.get('turns_source') or {}).get('path',''))
            self.model_path.set(report['affective_model'].get('path',''))
            self.model_label.configure(text='Modelo: '+ ('experimental cargado' if self.model_path.get() else 'sin cargar · sin predicción emocional'))
            self.single.set(report['speaker_mode']=='single_speaker_user_declared')
            self.refresh_candidates()
            self.plot()
            self.status.set('Análisis reabierto con fuente verificada por SHA-256; revisiones conservadas.')
        except Exception as e:
            messagebox.showerror('Abrir análisis',str(e))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--demo',action='store_true')
    args = parser.parse_args()
    App(demo=args.demo).mainloop()


if __name__ == '__main__':
    main()
