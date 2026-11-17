"""Motor reproducible. No elimina ruido ni deduce emociones de reglas acústicas."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import platform
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import parselmouth
import soundfile as sf
from parselmouth.praat import call
from scipy.signal import stft

from . import __version__


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def finite(value, digits=6):
    return round(float(value), digits) if value is not None and np.isfinite(value) else None


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


class SpeakerSetupError(ValueError):
    """Falta la declaración de una voz o los intervalos anotados de una entrevista."""


def validate_speaker_setup(speaker, single_speaker, turns_path=None):
    """Validación ligera antes de decodificar un audio potencialmente largo."""
    if not isinstance(speaker, str) or not speaker.strip() or speaker == 'sin_identificar':
        raise ValueError('Escriba o seleccione la persona cuya voz desea analizar.')
    if not single_speaker and not turns_path:
        raise SpeakerSetupError(
            'Falta indicar quién habla en el audio.\n\n'
            'Si habla una sola persona durante toda la grabación, marque '
            '«Una sola persona en todo el audio».\n\n'
            'Si hay varias voces, cargue «Turnos / transcripción…» con el inicio, '
            'fin y nombre de cada persona en los intervalos que desea analizar. '
            'Puede anotar sólo una parte de la entrevista; no hace falta completarla.\n\n'
            'La referencia debe quedar dentro de una intervención de la persona elegida '
            '(al menos 4 segundos con la configuración inicial).')
    if turns_path and not Path(turns_path).is_file():
        raise ValueError('No se encuentra el archivo de turnos. Cárguelo nuevamente.')


@dataclass
class Config:
    window_sec: float = 2.0
    step_sec: float = 0.5
    frame_sec: float = 0.01
    pitch_floor: float = 60.0
    pitch_ceiling: float = 500.0
    silence_dbfs: float = -45.0
    minimum_voiced: float = 0.5
    clipping_limit: float = 0.01
    robust_threshold: float = 2.5
    channel: int = 0

    def validate(self, sample_rate):
        numeric = [v for k, v in asdict(self).items() if k != 'channel']
        if not all(math.isfinite(v) for v in numeric):
            raise ValueError('La configuración debe contener números finitos.')
        if not 0.5 <= self.window_sec <= 30 or not 0.05 <= self.step_sec <= self.window_sec:
            raise ValueError('Ventana: 0.5–30 s; paso: 0.05 s hasta el tamaño de ventana.')
        if not 0.005 <= self.frame_sec <= 0.05:
            raise ValueError('Paso de cuadros fuera de 0.005–0.05 s.')
        if not 30 <= self.pitch_floor < self.pitch_ceiling < sample_rate / 2:
            raise ValueError('Revise piso/techo F0 y frecuencia de muestreo.')
        if not -100 <= self.silence_dbfs <= -10 or not 0 < self.minimum_voiced <= 1:
            raise ValueError('Umbral de energía o cobertura periódica inválidos.')
        if not 0 < self.clipping_limit < 0.5 or not 1 <= self.robust_threshold <= 10:
            raise ValueError('Umbral de clipping o desviación inválido.')
        if not isinstance(self.channel, int) or self.channel < 0:
            raise ValueError('Seleccione un canal entero desde 0.')


def load_audio(path, channel=0):
    """Conserva ganancia y muestreo; no promedia canales. FFmpeg sólo decodifica."""
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError(f'No existe el audio: {path}')
    decoder = 'libsndfile'
    try:
        values, sr = sf.read(path, dtype='float64', always_2d=True)
    except (RuntimeError, sf.LibsndfileError):
        with tempfile.TemporaryDirectory(prefix='vozafecto_') as temp:
            decoded = Path(temp) / 'decoded.wav'
            try:
                proc = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path),
                                       '-map', '0:a:0', '-c:a', 'pcm_f32le', str(decoded)],
                                      capture_output=True, text=True, timeout=300,
                                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except FileNotFoundError as e:
                raise ValueError('Formato no soportado. Use WAV/FLAC o instale FFmpeg para decodificar.') from e
            if proc.returncode:
                raise ValueError('No se pudo decodificar el audio: ' + proc.stderr[-500:])
            values, sr = sf.read(decoded, dtype='float64', always_2d=True)
            decoder = 'ffmpeg PCM float, sin normalización'
    if channel >= values.shape[1] or channel < 0:
        raise ValueError(f'El audio tiene {values.shape[1]} canal(es); seleccione 0 a {values.shape[1]-1}.')
    if sr < 8000 or len(values) < sr * 0.5:
        raise ValueError('Se requiere al menos 0.5 segundos y muestreo de 8 kHz.')
    if not np.isfinite(values).all():
        raise ValueError('El audio contiene muestras no finitas.')
    meta = {'path': str(path), 'sha256': digest(path), 'duration_sec': len(values) / sr,
            'sample_rate': sr, 'channels': values.shape[1], 'selected_channel': channel,
            'decoder': decoder, 'normalization': 'ninguna', 'sample_count': len(values)}
    return values[:, channel].copy(), sr, meta


FEATURES = {
    'f0_median_hz': ('Hz', 'Mediana F0 en cuadros periódicos'),
    'f0_range_st': ('semitonos', '12 log2(p90/p10) de F0'),
    'f0_slope_st_sec': ('semitonos/s', 'Regresión del contorno F0 sonoro, sin interpolar huecos'),
    'rms_dbfs': ('dBFS', '20 log10(RMS); referencia amplitud digital 1, sin calibración SPL'),
    'jitter_local_pct': ('%', 'Perturbación local de períodos, Praat'),
    'shimmer_local_pct': ('%', 'Perturbación local de amplitudes, Praat'),
    'hnr_db': ('dB', 'Mediana de armonicidad CC en cuadros periódicos'),
    'centroid_hz': ('Hz', 'Centroide de magnitud STFT, cuadros con energía'),
    'spectral_tilt_db_oct': ('dB/octava', 'Ajuste log2(f) de espectro de potencia entre 500–4000 Hz'),
    'low_high_db': ('dB', '10 log10 potencia 50–1000 / 1000–5000 Hz'),
    'low_energy_ratio': ('proporción', 'Cuadros RMS bajo umbral fijo; no equivale a ausencia de habla'),
    'voiced_ratio': ('proporción', 'Cuadros con periodicidad estimada; no equivale a VAD'),
}


def extract_segment(y, sr, cfg):
    cfg.validate(sr)
    duration = len(y) / sr
    measures = {k: None for k in FEATURES}
    reasons = {}
    warnings = []
    clip = float(np.mean(np.abs(y) >= 0.999))
    rms = float(np.sqrt(np.mean(y*y)))
    measures['rms_dbfs'] = finite(20 * np.log10(rms)) if rms > 0 else None
    width, hop = max(1, int(.025*sr)), max(1, int(cfg.frame_sec*sr))
    frames = np.lib.stride_tricks.sliding_window_view(y, width)[::hop]
    frame_rms = np.sqrt(np.mean(frames**2, axis=1))
    frame_db = 20*np.log10(np.maximum(frame_rms, 1e-15))
    measures['low_energy_ratio'] = finite(np.mean(frame_db < cfg.silence_dbfs))
    sound = parselmouth.Sound(y, sampling_frequency=sr)
    f0_times, f0 = np.array([]), np.array([])
    try:
        pitch = sound.to_pitch_cc(time_step=cfg.frame_sec, pitch_floor=cfg.pitch_floor,
                                  pitch_ceiling=cfg.pitch_ceiling)
        all_f0 = pitch.selected_array['frequency']
        mask = all_f0 > 0
        f0, f0_times = all_f0[mask], pitch.xs()[mask]
        measures['voiced_ratio'] = finite(np.mean(mask))
    except parselmouth.PraatError as e:
        reasons['voiced_ratio'] = str(e).strip()
    eligible = (len(f0) >= 10 and (measures['voiced_ratio'] or 0) >= cfg.minimum_voiced
                and rms >= 10**(cfg.silence_dbfs/20) and clip < cfg.clipping_limit)
    if clip >= cfg.clipping_limit:
        warnings.append('CLIPPING: proporción de muestras cerca de saturación supera umbral')
    if not eligible:
        warnings.append('COBERTURA_INSUFICIENTE_O_CALIDAD: no comparar como voz válida')
    if eligible:
        measures['f0_median_hz'] = finite(np.median(f0))
        p10, p90 = np.percentile(f0, [10, 90])
        measures['f0_range_st'] = finite(12*np.log2(p90/p10))
        measures['f0_slope_st_sec'] = finite(np.polyfit(f0_times, 12*np.log2(f0/100), 1)[0])
        try:
            harmonicity = sound.to_harmonicity_cc(time_step=cfg.frame_sec, minimum_pitch=cfg.pitch_floor)
            htimes = harmonicity.xs()
            hv = harmonicity.values[0]
            # Sin interpolar sobre pausas: sólo cuadros con F0 sonoro más cercano.
            nearest = np.searchsorted(pitch.xs(), htimes).clip(0, len(all_f0)-1)
            good = np.isfinite(hv) & (hv > -199) & (all_f0[nearest] > 0)
            measures['hnr_db'] = finite(np.median(hv[good])) if good.any() else None
        except parselmouth.PraatError as e:
            reasons['hnr_db'] = str(e).strip()
        # Perturbación sólo en ventanas mayormente periódicas. Continúa siendo exploratoria.
        if measures['voiced_ratio'] >= .8 and duration >= .5:
            try:
                pp = call(sound, 'To PointProcess (periodic, cc)', cfg.pitch_floor, cfg.pitch_ceiling)
                floor_period, ceiling_period = .8/cfg.pitch_ceiling, 1.25/cfg.pitch_floor
                measures['jitter_local_pct'] = finite(100*call(pp, 'Get jitter (local)', 0, 0,
                                                               floor_period, ceiling_period, 1.3))
                measures['shimmer_local_pct'] = finite(100*call([sound, pp], 'Get shimmer (local)', 0, 0,
                                                                floor_period, ceiling_period, 1.3, 1.6))
            except parselmouth.PraatError as e:
                reasons['jitter_local_pct'] = reasons['shimmer_local_pct'] = str(e).strip()
        else:
            reasons['jitter_local_pct'] = reasons['shimmer_local_pct'] = 'Se exige cobertura periódica >= 0.8.'
    for key in ['f0_median_hz', 'f0_range_st', 'f0_slope_st_sec', 'hnr_db', 'jitter_local_pct', 'shimmer_local_pct']:
        if measures[key] is None:
            reasons.setdefault(key, 'Medición indefinida o ventana sin cobertura/calidad suficiente.')
    # Magnitud y potencia: STFT Hann 25 ms, paso 10 ms configurable, sin padding.
    freq, times, spec = stft(y, sr, window='hann', nperseg=width, noverlap=width-hop,
                             boundary=None, padded=False)
    magnitude = np.abs(spec)
    active = frame_db[:magnitude.shape[1]] >= cfg.silence_dbfs
    if active.any() and clip < cfg.clipping_limit:
        m = magnitude[:, active]
        measures['centroid_hz'] = finite(np.median(np.sum(freq[:, None]*m, axis=0)/np.maximum(m.sum(axis=0), 1e-15)))
        power = np.mean(m*m, axis=1)
        band = (freq >= 500) & (freq <= 4000) & (power > 1e-20)
        if sr >= 10000 and band.sum() > 5:
            measures['spectral_tilt_db_oct'] = finite(np.polyfit(np.log2(freq[band]), 10*np.log10(power[band]), 1)[0])
        if sr >= 12000:
            low = np.sum(power[(freq >= 50) & (freq < 1000)])
            high = np.sum(power[(freq >= 1000) & (freq <= 5000)])
            if low > 0 and high > 0:
                measures['low_high_db'] = finite(10*np.log10(low/high))
    for key, value in measures.items():
        if value is None:
            reasons.setdefault(key, 'Sin energía suficiente, banda no disponible o resultado indefinido.')
    return {'measures': measures, 'invalid_reasons': reasons,
            'quality': {'eligible': eligible, 'clipping_ratio': finite(clip), 'warnings': warnings}}


def read_turns(path, duration):
    """Anotaciones suministradas, nunca diarización inferida."""
    if not path:
        return []
    path = Path(path)
    if path.suffix.lower() == '.csv':
        with path.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
    else:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        rows = data.get('segments', []) if isinstance(data, dict) else data
    result = []
    if not isinstance(rows, list):
        raise ValueError('Turnos/transcripción debe ser una lista o contener segments.')
    for i, row in enumerate(rows):
        start, end = float(row['start']), float(row['end'])
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration + .001):
            raise ValueError(f'Intervalo de turno/transcripción inválido en fila {i+1}.')
        result.append({'id': str(row.get('id', i+1)), 'start': start, 'end': min(end, duration),
                       'speaker': str(row.get('speaker') or 'sin_identificar'),
                       'text': str(row.get('text', '')), 'content_note': str(row.get('content_note', '')),
                       'interaction_note': str(row.get('interaction_note', '')),
                       'overlap': str(row.get('overlap', '')).lower() in ('1', 'true', 'si', 'sí'),
                       'origin': str(path.resolve())})
    return sorted(result, key=lambda x: (x['start'], x['end']))


# Pisos evitan escalas explosivas cuando MAD=0. No son umbrales clínicos/afectivos.
SCALE_FLOORS = {'f0_median_hz': 8., 'f0_range_st': 1., 'rms_dbfs': 3.,
                'hnr_db': 3., 'jitter_local_pct': .5, 'shimmer_local_pct': 2.,
                'centroid_hz': 200., 'spectral_tilt_db_oct': 2., 'low_high_db': 3.,
                'low_energy_ratio': .1}


def build_baseline(y, sr, cfg, start, end):
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= len(y)/sr):
        raise ValueError('La referencia debe estar dentro del audio, con inicio menor que fin.')
    if end-start < max(2., 2*cfg.window_sec):
        raise ValueError(f'La referencia necesita al menos {max(2., 2*cfg.window_sec):g} s (dos ventanas completas).')
    # Subventanas no solapadas para no presentar cuadros repetidos como observaciones independientes.
    parts = []
    block = cfg.window_sec
    for t in np.arange(start, end-block+1e-7, block):
        segment = extract_segment(y[round(t*sr):round((t+block)*sr)], sr, cfg)
        if segment['quality']['eligible']:
            parts.append(segment['measures'])
    if len(parts) < 2:
        raise ValueError('Referencia no válida: se necesitan al menos dos subventanas vocales sin saturación.')
    statistics = {}
    for name, floor in SCALE_FLOORS.items():
        values = [r[name] for r in parts if r[name] is not None]
        if len(values) >= 2:
            median = float(np.median(values))
            mad = float(np.median(np.abs(np.array(values)-median)))
            statistics[name] = {'median': finite(median), 'mad': finite(mad),
                                'scale': finite(max(1.4826*mad, floor)),
                                'scale_floor': floor, 'n_valid': len(values)}
    if 'f0_median_hz' not in statistics:
        raise ValueError('La referencia carece de F0 válido suficiente.')
    return {'start': start, 'end': end, 'valid_subwindows': len(parts), 'subwindow_sec': block,
            'statistics': statistics, 'meaning': 'Referencia descriptiva seleccionada; neutralidad no presumida.'}


def compare(record, baseline, cfg):
    zscores, evidence = {}, []
    if not record['quality']['eligible']:
        return {'status': 'abstain', 'reason': 'Calidad/cobertura insuficiente', 'zscores': {},
                'activation_index': None, 'instability_index': None, 'evidence': []}
    for name, ref in baseline['statistics'].items():
        value = record['measures'][name]
        if value is not None:
            z = (value-ref['median'])/ref['scale']
            zscores[name] = finite(z)
            if abs(z) >= cfg.robust_threshold:
                evidence.append({'feature': name, 'observed': value, 'reference': ref['median'],
                                 'robust_deviation': finite(z), 'unit': FEATURES[name][0]})
    # Índices firmados y adimensionales de contraste, no probabilidades ni emociones estimadas.
    activation = [zscores[n] for n in ['f0_median_hz', 'rms_dbfs'] if n in zscores]
    instability = [max(0., sign*zscores[n]) for n, sign in
                   [('f0_range_st', 1), ('jitter_local_pct', 1), ('shimmer_local_pct', 1), ('hnr_db', -1)]
                   if n in zscores]
    return {'status': 'descriptive', 'zscores': zscores,
            'activation_index': finite(np.mean(np.clip(activation, -6, 6))) if len(activation) == 2 else None,
            'instability_index': finite(np.mean(np.clip(instability, 0, 6))) if instability else None,
            'evidence': evidence,
            'interpretation': 'Índices exploratorios de contraste vocal; no estiman estado interno, valencia ni certeza.'}


def candidates_from_windows(windows, cfg):
    result = []
    families = {'f0_median_hz': 'prosodia', 'f0_range_st': 'prosodia', 'rms_dbfs': 'energía',
                'hnr_db': 'calidad', 'jitter_local_pct': 'calidad', 'shimmer_local_pct': 'calidad',
                'centroid_hz': 'espectro', 'spectral_tilt_db_oct': 'espectro', 'low_high_db': 'espectro',
                'low_energy_ratio': 'temporalidad'}
    for row in windows:
        ev = row['comparison']['evidence']
        groups = set(families[e['feature']] for e in ev)
        if not ev or row.get('is_reference'):
            continue
        # El color describe revisión humana; prioridad numérica no cambia alerta a PIN.
        maximum = max(abs(e['robust_deviation']) for e in ev)
        priority = 3 if len(groups) >= 3 and maximum >= 4 else 2 if len(groups) >= 2 else 1
        level = 'amarillo'
        summary = 'Variación vocal: ' + ', '.join(sorted(groups))
        # Fusionar sólo una firma comparable; no convertir variaciones distintas en un intervalo gigante.
        signature = tuple(sorted((e['feature'], e['robust_deviation'] > 0) for e in ev))
        previous_signature = tuple(sorted((e['feature'], e['robust_deviation'] > 0) for e in result[-1]['evidence'])) if result else ()
        consecutive = result and int(row['id'][1:]) == int(result[-1]['window_ids'][-1][1:])+1
        if consecutive and result[-1]['end'] >= row['start'] and previous_signature == signature:
            event = result[-1]
            event['end'] = row['end']
            event['priority'] = max(event['priority'], priority)
            event['window_ids'].append(row['id'])
            # Mantener por rasgo la desviación más marcada; las ventanas completas siguen en reporte.
            by_feature = {e['feature']: e for e in event['evidence']}
            for e in ev:
                if e['feature'] not in by_feature or abs(e['robust_deviation']) > abs(by_feature[e['feature']]['robust_deviation']):
                    by_feature[e['feature']] = e
            event['evidence'] = list(by_feature.values())
            event['summary'] = 'Variación vocal: ' + ', '.join(sorted({families[e['feature']] for e in event['evidence']}))
        else:
            result.append({'id': f'C{len(result)+1:04d}', 'start': row['start'], 'end': row['end'],
                           'level': level, 'priority': priority, 'summary': summary, 'evidence': list(ev), 'window_ids': [row['id']],
                           'review': {'status': 'pending', 'author': '', 'comment': '', 'updated_at': None}})
    return result


def analyze(audio_path, baseline_start, baseline_end, *, cfg=None, speaker='hablante_1',
            single_speaker=False, turns_path=None, progress: Callable | None=None):
    cfg = cfg or Config()
    validate_speaker_setup(speaker, single_speaker, turns_path)
    y, sr, source = load_audio(audio_path, cfg.channel)
    cfg.validate(sr)
    turns = read_turns(turns_path, source['duration_sec'])
    if not single_speaker:
        target = [t for t in turns if t['speaker'] == speaker and not t['overlap']]
        if not target:
            available = sorted({t['speaker'] for t in turns if t['speaker'] != 'sin_identificar'})
            names = ', '.join(available) if available else 'ninguna persona identificada'
            raise SpeakerSetupError(
                f'No hay intervenciones sin solapamiento para «{speaker}».\n\n'
                f'Personas indicadas en los turnos: {names}.\n\n'
                'Seleccione una de ellas en «Hablante» y una referencia dentro de su intervención. '
                'Una transcripción sin nombres de persona todavía necesita esos turnos anotados.')
        # La referencia debe quedar contenida en un turno anotado del objetivo.
        if not any(t['start'] <= baseline_start and baseline_end <= t['end'] for t in target):
            raise ValueError('La referencia debe pertenecer a un turno sin solapamiento del hablante objetivo.')
        if any(t['start'] < baseline_end and baseline_start < t['end'] and
               (t['overlap'] or t['speaker'] != speaker) for t in turns):
            raise ValueError('La referencia se cruza con otro hablante/solapamiento anotado.')
    baseline = build_baseline(y, sr, cfg, float(baseline_start), float(baseline_end))
    baseline['speaker'] = speaker
    windows = []
    starts = np.arange(0, source['duration_sec'], cfg.step_sec)
    for idx, t in enumerate(starts):
        end = min(float(t+cfg.window_sec), source['duration_sec'])
        if end-t < .5:
            continue
        row = extract_segment(y[round(t*sr):round(end*sr)], sr, cfg)
        context = [x for x in turns if x['start'] < end and t < x['end']]
        row.update({'id': f'W{idx+1:06d}', 'start': finite(t), 'end': finite(end),
                    'speaker': speaker if single_speaker else 'sin_atribuir', 'target_speaker': speaker, 'context': context,
                    'is_reference': bool(t < baseline_end and baseline_start < end)})
        if end-t < cfg.window_sec-1e-8:
            row['quality']['eligible'] = False
            row['quality']['warnings'].append('VENTANA_INCOMPLETA: medidas conservadas, contraste y modelo omitidos')
        if not single_speaker:
            owners = {x['speaker'] for x in context}
            contained = any(x['speaker'] == speaker and not x['overlap'] and
                            x['start'] <= t and end <= x['end'] for x in context)
            row['speaker'] = next(iter(owners)) if len(owners) == 1 else 'mixto' if owners else 'sin_atribuir'
            if owners != {speaker} or not contained or any(x['overlap'] for x in context):
                row['quality']['eligible'] = False
                row['quality']['warnings'].append('TURNO_NO_ATRIBUIBLE: otro hablante, borde o solapamiento')
        row['comparison'] = compare(row, baseline, cfg)
        windows.append(row)
        if progress:
            progress((idx+1)/len(starts))
    report = {'schema_version': 'vozafecto-0.1', 'software': {'name': 'EMBox Voz Afectiva',
              'version': __version__, 'python': platform.python_version(), 'numpy': np.__version__,
              'parselmouth': parselmouth.__version__, 'praat': parselmouth.PRAAT_VERSION},
              'created_at': utc_now(), 'source': source, 'config': asdict(cfg),
              'speaker_mode': 'single_speaker_user_declared' if single_speaker else 'provided_turns',
              'turns_source': {'path': str(Path(turns_path).resolve()), 'sha256': digest(turns_path)} if turns_path else None,
              'baseline': baseline, 'feature_dictionary': {k: {'unit': v[0], 'definition': v[1]} for k,v in FEATURES.items()},
              'windows': windows, 'candidates': candidates_from_windows(windows, cfg),
              'affective_model': {'status': 'not_loaded', 'predictions': [],
                                  'reason': 'No se incluyen pesos entrenados ni validación de emociones.'},
              'review_log': [], 'limitations': [
                  'Prueba de concepto de seminario. No hay validación afectiva en entrevistas reales.',
                  'Activación e inestabilidad descriptivas son índices heurísticos sin calibración psicológica.',
                  'Amarillo: candidato; naranja: segmento marcado por revisor; rojo: PIN aceptado por revisor.',
                  'Prioridad 1–3 es heurística y no expresa severidad clínica ni certeza.',
                  'Periodicidad y baja energía no son detección fiable de habla, emoción o engaño.',
                  'Jitter/shimmer en habla conectada y espectro dependen de vocal, canal y ruido.',
                  'Sólo se comparan ventanas atribuidas al hablante objetivo; no hay diarización automática.',
                  'Resolución de exportación 1 ms; resolución analítica y exactitud son diferentes.']}
    return report


def review_candidate(report, candidate_id, status, author, comment):
    if status not in ('accepted', 'rejected', 'pending', 'segment') or not author.strip() or not comment.strip():
        raise ValueError('La revisión requiere estado, nombre del revisor y una justificación.')
    candidate = next((c for c in report['candidates'] if c['id'] == candidate_id), None)
    if candidate is None:
        raise ValueError('No existe el candidato.')
    previous = dict(candidate['review'])
    candidate['review'] = {'status': status, 'author': author.strip(), 'comment': comment.strip(), 'updated_at': utc_now()}
    candidate['level'] = {'accepted': 'rojo', 'segment': 'naranja', 'pending': 'amarillo', 'rejected': 'gris'}[status]
    report['review_log'].append({'candidate_id': candidate_id, 'previous': previous, 'review': dict(candidate['review'])})


def save_report(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory/'analisis_voz.json', report)
    with (directory/'mediciones.csv').open('w', encoding='utf-8-sig', newline='') as f:
        fields = ['id', 'start', 'end', 'speaker', 'eligible'] + list(FEATURES)
        writer = csv.DictWriter(f, fields)
        writer.writeheader()
        for w in report['windows']:
            writer.writerow({**{k:w[k] for k in fields[:4]}, 'eligible': w['quality']['eligible'], **w['measures']})
    write_json(directory/'candidatos.json', {'source_sha256': report['source']['sha256'], 'candidates': report['candidates']})
    return directory/'analisis_voz.json'
