"""Fixture de ingeniería determinista, no corpus emocional ni voz humana."""
from pathlib import Path
import numpy as np
import soundfile as sf


def generate(directory):
    directory = Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    sr = 16000
    seconds = 24
    t = np.arange(sr*seconds)/sr
    f0 = np.full(t.shape,150.)
    amplitude = np.full(t.shape,.11)
    # Referencia 0–4; cambio de tono/energía 6–11; modulación 14–18; silencio 20–22.
    changed = (t>=6)&(t<11)
    f0[changed] = 245.
    amplitude[changed] = .30
    modulated = (t>=14)&(t<18)
    f0[modulated] = 180 + 35*np.sin(2*np.pi*4*t[modulated])
    phase = 2*np.pi*np.cumsum(f0)/sr
    y = amplitude*(np.sin(phase)+.32*np.sin(2*phase)+.12*np.sin(3*phase))
    y[(t>=20)&(t<22)] = 0
    sf.write(directory/'demo_sintetica.wav',y,sr,subtype='PCM_16')
    (directory/'LEEME_DEMO.txt').write_text('SEÑAL SINTÉTICA, NO VOZ HUMANA.\n'
        '24 s, 16 kHz mono. Referencia 0–4 s: 150 Hz. 6–11 s: 245 Hz y mayor amplitud.\n'
        '14–18 s: F0 modulado. 20–22 s: ceros digitales. Las transiciones no son emociones.\n'
        'Objetivo: verificar medición, abstención, revisión y exportación.\n',encoding='utf-8')


if __name__=='__main__':
    generate(Path(__file__).resolve().parents[1]/'data')
