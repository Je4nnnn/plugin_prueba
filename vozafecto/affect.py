"""Baseline supervisado dimensional opcional, sin pesos ni corpus ficticios.

Regresión Ridge multisalida con validación reservada por hablante. Etiquetas
arousal/valence en [-1,1], aportadas por el investigador, no por reglas del motor.
"""
from __future__ import annotations
import csv
import json
from pathlib import Path

import numpy as np
import parselmouth

from .core import Config, digest, extract_segment, load_audio, utc_now, write_json, finite

MODEL_FEATURES = ['f0_median_hz', 'f0_range_st', 'f0_slope_st_sec', 'rms_dbfs',
                  'hnr_db', 'centroid_hz', 'spectral_tilt_db_oct', 'low_high_db',
                  'low_energy_ratio', 'voiced_ratio']


def ccc(actual, predicted):
    a, p = np.asarray(actual), np.asarray(predicted)
    denom = np.var(a)+np.var(p)+(np.mean(a)-np.mean(p))**2
    return finite(2*np.mean((a-a.mean())*(p-p.mean()))/denom) if denom > 0 else None


def train(corpus_csv, output, *, alpha=10., test_speakers=None, cfg=None,
          label_definition='expresión afectiva percibida, según anotación del corpus'):
    cfg = cfg or Config()
    if not np.isfinite(alpha) or alpha <= 0:
        raise ValueError('alpha debe ser positivo y finito; fijarlo antes del test.')
    corpus_csv = Path(corpus_csv).resolve()
    output = Path(output).resolve()
    if output == corpus_csv:
        raise ValueError('La salida del modelo debe ser distinta del CSV de entrada.')
    with corpus_csv.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    if len(rows) < 8:
        raise ValueError('Se requieren al menos 8 intervalos etiquetados; esto no garantiza suficiencia estadística.')
    required = {'audio', 'start', 'end', 'speaker', 'arousal', 'valence'}
    if not required <= set(rows[0]):
        raise ValueError('CSV necesita audio,start,end,speaker,arousal,valence.')
    examples, excluded, cache, used_intervals = [], [], {}, {}
    for i, row in enumerate(rows):
        path = (corpus_csv.parent/row['audio']).resolve()
        if output == path:
            raise ValueError('La salida del modelo no puede sobrescribir un audio del corpus.')
        if str(path) not in cache:
            cache[str(path)] = load_audio(path, cfg.channel)
        y, sr, meta = cache[str(path)]
        start, end = float(row['start']), float(row['end'])
        labels = np.array([float(row['arousal']), float(row['valence'])])
        speaker = row['speaker'].strip()
        if not speaker or not np.isfinite(labels).all() or (np.abs(labels)>1).any():
            raise ValueError(f'Fila {i+2}: hablante o etiquetas fuera de [-1,1].')
        if not 0 <= start < end <= len(y)/sr or end-start < .5:
            raise ValueError(f'Fila {i+2}: intervalo inválido.')
        # No permitir reutilizar el mismo tramo con otro speaker para contaminar test.
        for old_start, old_end, old_speaker in used_intervals.get(meta['sha256'], []):
            if start < old_end and old_start < end and speaker != old_speaker:
                raise ValueError('Intervalos solapados del mismo audio se asignaron a distintos hablantes.')
        used_intervals.setdefault(meta['sha256'], []).append((start, end, speaker))
        record = extract_segment(y[round(start*sr):round(end*sr)], sr, cfg)
        if not record['quality']['eligible']:
            excluded.append({'csv_row': i+2, 'reason': record['quality']['warnings']})
            continue
        features = [record['measures'][k] if record['measures'][k] is not None else np.nan for k in MODEL_FEATURES]
        examples.append({'x': features, 'y': labels.tolist(), 'speaker': speaker,
                         'source_sha256': meta['sha256'], 'start': start, 'end': end})
    groups = sorted({e['speaker'] for e in examples})
    if len(groups) < 3:
        raise ValueError('Se requieren al menos 3 hablantes con ventanas válidas: >=2 entrenamiento, >=1 test.')
    held = set(test_speakers or [groups[-1]])
    if not held <= set(groups) or len(set(groups)-held) < 2:
        raise ValueError('Reserve hablantes existentes y conserve al menos dos para entrenar.')
    mask = np.array([e['speaker'] not in held for e in examples])
    train_sources = {e['source_sha256'] for e in examples if e['speaker'] not in held}
    test_sources = {e['source_sha256'] for e in examples if e['speaker'] in held}
    if train_sources & test_sources:
        raise ValueError('Un mismo archivo de audio aparece en entrenamiento y test. Reserve grabaciones completas además de hablantes.')
    x = np.array([e['x'] for e in examples], dtype=float)
    y = np.array([e['y'] for e in examples], dtype=float)
    if mask.sum() < 4 or (~mask).sum() < 2:
        raise ValueError('Se requieren >=4 intervalos de entrenamiento y >=2 de test válidos.')
    # Todas las transformaciones se ajustan EXCLUSIVAMENTE con entrenamiento.
    medians = np.array([np.nanmedian(x[mask, j]) if np.isfinite(x[mask, j]).any() else 0.
                        for j in range(x.shape[1])])
    clean = np.where(np.isfinite(x), x, medians)
    means, scales = clean[mask].mean(axis=0), clean[mask].std(axis=0)
    scales = np.maximum(scales, 1e-8)
    z = (clean-means)/scales
    target_mean = y[mask].mean(axis=0)
    coef = np.linalg.solve(z[mask].T@z[mask]+alpha*np.eye(z.shape[1]), z[mask].T@(y[mask]-target_mean))
    predictions = np.clip(z[~mask]@coef+target_mean, -1, 1)
    metrics = {}
    for j, label in enumerate(['arousal', 'valence']):
        actual, pred = y[~mask, j], predictions[:, j]
        constant = np.full_like(actual, target_mean[j])
        metrics[label] = {'mae': finite(np.mean(np.abs(actual-pred))),
                          'rmse': finite(np.sqrt(np.mean((actual-pred)**2))), 'ccc': ccc(actual, pred),
                          'constant_baseline_mae': finite(np.mean(np.abs(actual-constant)))}
    model = {'schema_version': 'vozafecto-ridge-0.1', 'created_at': utc_now(), 'type': 'ridge_dimensional',
             'features': MODEL_FEATURES, 'labels': ['arousal', 'valence'], 'label_range': [-1, 1],
             'label_definition': label_definition,
             'extractor': {'parselmouth': parselmouth.__version__, 'praat': parselmouth.PRAAT_VERSION,
                           'numpy': np.__version__, 'version': '0.1.0'},
             'config': vars(cfg), 'alpha': alpha, 'medians': medians.tolist(), 'means': means.tolist(),
             'scales': scales.tolist(), 'coef': coef.tolist(), 'target_mean': target_mean.tolist(),
             'training_speakers': sorted(set(groups)-held), 'test_speakers': sorted(held),
             'train_n': int(mask.sum()), 'test_n': int((~mask).sum()), 'metrics': metrics,
             'corpus': {'path': str(corpus_csv), 'sha256': digest(corpus_csv), 'examples': examples, 'excluded': excluded},
             'limitations': ['Baseline exploratorio, no calibración probabilística.',
                             'Duración de intervalos puede diferir de las ventanas de inferencia; evaluar ese cambio de dominio.',
                             'Una partición reservada no demuestra generalización a entrevistas ni otros idiomas.',
                             'No ajustar hiperparámetros sobre el test; realizar validación agrupada en un estudio posterior.',
                             'Calidad de etiquetas y consentimiento deben documentarse en el protocolo del corpus.']}
    write_json(output, model)
    return model


def apply_model(report, model_path):
    model_path = Path(model_path)
    model = json.loads(model_path.read_text(encoding='utf-8-sig'))
    if not isinstance(model, dict) or model.get('schema_version') != 'vozafecto-ridge-0.1' or model.get('features') != MODEL_FEATURES:
        raise ValueError('Modelo incompatible; se requiere vozafecto-ridge-0.1 con variables documentadas.')
    if model.get('type') != 'ridge_dimensional' or model.get('labels') != ['arousal','valence'] or model.get('label_range') != [-1,1]:
        raise ValueError('Tipo, orden de etiquetas o rango incompatibles.')
    required = ['config','medians','means','scales','coef','target_mean','metrics','training_speakers','test_speakers']
    if any(k not in model for k in required) or not isinstance(model['config'],dict):
        raise ValueError('Modelo incompleto: faltan configuración, transformaciones o evaluación.')
    if (not isinstance(model['metrics'],dict)
        or any(not isinstance(model[k],list) or not all(isinstance(s,str) for s in model[k])
               for k in ['training_speakers','test_speakers'])):
        raise ValueError('Metadatos de evaluación o hablantes inválidos.')
    if set(model['training_speakers']) & set(model['test_speakers']):
        raise ValueError('El modelo mezcla hablantes de entrenamiento y test.')
    # Evitar cambio silencioso del extractor entre entrenamiento e inferencia.
    for key in ['pitch_floor', 'pitch_ceiling', 'frame_sec', 'silence_dbfs', 'minimum_voiced', 'clipping_limit', 'channel']:
        if model['config'].get(key) != report['config'][key]:
            raise ValueError(f'Configuración modelo/análisis distinta: {key}.')
    medians, means, scales = (np.asarray(model[k], dtype=float) for k in ['medians','means','scales'])
    coef, intercept = np.asarray(model['coef'], dtype=float), np.asarray(model['target_mean'], dtype=float)
    n = len(MODEL_FEATURES)
    if any(v.shape != (n,) for v in [medians,means,scales]) or coef.shape != (n,2) or intercept.shape != (2,):
        raise ValueError('Dimensiones de modelo inválidas.')
    if not all(np.isfinite(v).all() for v in [medians,means,scales,coef,intercept]) or (scales <= 0).any():
        raise ValueError('Modelo contiene números inválidos.')
    predictions = []
    corpus = model.get('corpus',{})
    if not isinstance(corpus,dict) or not isinstance(corpus.get('examples',[]),list):
        raise ValueError('Procedencia del corpus inválida.')
    corpus_examples = corpus.get('examples',[])
    for example in corpus_examples:
        if not isinstance(example,dict) or not {'source_sha256','start','end','speaker'} <= example.keys():
            raise ValueError('Ejemplo de procedencia incompleto.')
    for row in report['windows']:
        if not row['quality']['eligible']:
            continue
        x = np.array([row['measures'][k] if row['measures'][k] is not None else np.nan for k in MODEL_FEATURES])
        clean = np.where(np.isfinite(x), x, medians)
        z = (clean-means)/scales
        raw = z@coef+intercept
        pred = np.clip(raw, -1, 1)
        overlap = [e for e in corpus_examples if e.get('source_sha256') == report['source']['sha256']
                   and e['start'] < row['end'] and row['start'] < e['end']]
        predictions.append({'window_id': row['id'], 'start': row['start'], 'end': row['end'],
                            'arousal': finite(pred[0]), 'valence': finite(pred[1]),
                            'imputed_features': [MODEL_FEATURES[j] for j in np.where(~np.isfinite(x))[0]],
                            'outside_training_scale': bool((np.abs(z)>5).any()),
                            'overlaps_training_data': any(e['speaker'] in model['training_speakers'] for e in overlap),
                            'overlaps_test_data': any(e['speaker'] in model['test_speakers'] for e in overlap),
                            'clipped_to_label_range': bool((raw != pred).any())})
    report['affective_model'] = {'status': 'experimental_predictions', 'path': str(model_path.resolve()),
                                'sha256': digest(model_path), 'type': model['type'], 'metrics': model['metrics'],
                                'training_speakers': model['training_speakers'], 'test_speakers': model['test_speakers'],
                                'label_definition': model.get('label_definition','no especificada'),
                                'extractor_version_match': model.get('extractor',{}).get('praat') == parselmouth.PRAAT_VERSION,
                                'predictions': predictions,
                                'interpretation': 'Regresión de etiquetas aportadas; no certeza de emoción interna. Sin intervalo de confianza calibrado.'}
    return report
