"""Comprobaciones acústicas y de trazabilidad con señales de valor conocido.

No son un benchmark de reconocimiento emocional ni una validación pericial.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from vozafecto.core import Config, analyze, digest, extract_segment, review_candidate, save_report


SR = 16000


def tone(frequency: float, duration: float, amplitude: float = .2) -> np.ndarray:
    time = np.arange(round(SR * duration), dtype=np.float64) / SR
    return amplitude * np.sin(2 * np.pi * frequency * time)


def wav(tmp_path: Path, samples: np.ndarray, name: str = "prueba.wav") -> Path:
    path = tmp_path / name
    sf.write(path, samples, SR, subtype="FLOAT")
    return path


def changing_voice(tmp_path: Path) -> Path:
    return wav(tmp_path, np.concatenate([tone(180, 3), tone(260, 3)]))


def strict_json(path: Path):
    def reject_constant(value):
        raise AssertionError(f"Constante JSON no interoperable: {value}")
    return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=reject_constant)


@pytest.mark.parametrize("frequency", [120., 180., 320.])
def test_f0_of_known_tone(frequency):
    """El extractor debe recuperar la frecuencia fundamental de señales conocidas."""
    row = extract_segment(tone(frequency, 2), SR, Config())
    assert row["quality"]["eligible"] is True
    assert row["measures"]["f0_median_hz"] == pytest.approx(frequency, rel=.01)
    assert row["measures"]["voiced_ratio"] >= .95


def test_silent_reference_is_rejected(tmp_path):
    path = wav(tmp_path, np.concatenate([np.zeros(2 * SR), tone(180, 3)]))
    with pytest.raises(ValueError, match="Referencia no válida"):
        analyze(path, 0, 2, single_speaker=True, cfg=Config(window_sec=1))


def test_silence_has_absent_f0_and_exports_as_null(tmp_path):
    """Un fallo de periodicidad no se convierte en 0 Hz ni en emoción neutra."""
    path = wav(tmp_path, np.concatenate([tone(180, 3), np.zeros(3 * SR)]))
    report = analyze(path, 0, 2, single_speaker=True, cfg=Config(window_sec=1, step_sec=1))
    silent = [row for row in report["windows"] if row["start"] >= 3]
    assert silent
    for row in silent:
        assert row["measures"]["f0_median_hz"] is None
        assert row["measures"]["rms_dbfs"] is None
        assert row["measures"]["low_energy_ratio"] == 1
        assert row["quality"]["eligible"] is False
        assert row["comparison"]["status"] == "abstain"
        assert row["comparison"]["evidence"] == []
        assert row["invalid_reasons"]["f0_median_hz"]
    exported = strict_json(save_report(report, tmp_path / "salida"))
    assert exported["windows"][-1]["measures"]["f0_median_hz"] is None
    assert not exported["affective_model"]["predictions"]


def test_clipping_prevents_comparison_as_valid_voice():
    clipped = np.clip(tone(180, 2, amplitude=3), -1, 1)
    row = extract_segment(clipped, SR, Config())
    assert row["quality"]["clipping_ratio"] > .1
    assert row["quality"]["eligible"] is False
    assert row["measures"]["f0_median_hz"] is None
    assert any("CLIPPING" in warning for warning in row["quality"]["warnings"])


def test_stereo_channel_is_selected_without_downmix(tmp_path):
    samples = np.column_stack([tone(180, 4), tone(280, 4, amplitude=.05)])
    path = wav(tmp_path, samples)
    original_hash = digest(path)
    report = analyze(path, 0, 4, single_speaker=True, cfg=Config(channel=1))
    assert report["source"]["channels"] == 2
    assert report["source"]["selected_channel"] == 1
    assert report["source"]["normalization"] == "ninguna"
    assert report["baseline"]["statistics"]["f0_median_hz"]["median"] == pytest.approx(280, rel=.01)
    assert digest(path) == original_hash == report["source"]["sha256"]
    with pytest.raises(ValueError, match="canal"):
        analyze(path, 0, 2, single_speaker=True, cfg=Config(channel=2))


def test_other_speaker_is_not_compared_or_attributed_to_target(tmp_path):
    path = changing_voice(tmp_path)
    turns = tmp_path / "turnos.json"
    turns.write_text(json.dumps({"segments": [
        {"id": "a", "start": 0, "end": 3, "speaker": "A"},
        {"id": "b", "start": 3, "end": 6, "speaker": "B"},
    ]}), encoding="utf-8")
    report = analyze(path, 0, 2, speaker="A", turns_path=turns,
                     cfg=Config(window_sec=1, step_sec=1))
    second_speaker = [row for row in report["windows"] if row["start"] >= 3]
    assert second_speaker
    for row in second_speaker:
        assert row["speaker"] == "B"
        assert row["quality"]["eligible"] is False
        assert row["comparison"]["status"] == "abstain"
        assert row["comparison"]["evidence"] == []
    assert report["source"]["sha256"] == digest(path)
    assert report["turns_source"]["sha256"] == digest(turns)


def test_reference_crossing_another_speaker_is_rejected(tmp_path):
    path = wav(tmp_path, tone(180, 5))
    turns = tmp_path / "turnos.json"
    turns.write_text(json.dumps([
        {"start": 0, "end": 5, "speaker": "A"},
        {"start": 1, "end": 1.5, "speaker": "B"},
    ]), encoding="utf-8")
    with pytest.raises(ValueError, match="otro hablante/solapamiento"):
        analyze(path, 0, 3, speaker="A", turns_path=turns)


def test_unattributed_window_abstains(tmp_path):
    path = wav(tmp_path, tone(180, 5))
    turns = tmp_path / "turnos.json"
    turns.write_text(json.dumps([
        {"start": 0, "end": 2.5, "speaker": "A"},
    ]), encoding="utf-8")
    report = analyze(path, 0, 2, speaker="A", turns_path=turns,
                     cfg=Config(window_sec=1, step_sec=1))
    missing = [row for row in report["windows"] if row["start"] >= 3]
    assert missing
    assert all(row["speaker"] != "A" for row in missing)
    assert all(row["comparison"]["status"] == "abstain" for row in missing)


def test_speaker_declaration_is_required_when_turns_are_absent(tmp_path):
    path = wav(tmp_path, tone(180, 4))
    with pytest.raises(ValueError, match="Falta indicar quién habla"):
        analyze(path, 0, 2)


def test_review_history_and_decisions_survive_json_round_trip(tmp_path):
    report = analyze(changing_voice(tmp_path), 0, 2, single_speaker=True,
                     cfg=Config(window_sec=1, step_sec=.5))
    assert report["candidates"], "El cambio conocido de tono debe ofrecer al menos un candidato de revisión."
    assert all(row["review"]["status"] == "pending" for row in report["candidates"])
    # Amarillo corresponde a sugerencia automática, independientemente de su prioridad.
    assert all(row["level"] == "amarillo" for row in report["candidates"])
    candidate = report["candidates"][0]
    review_candidate(report, candidate["id"], "segment", "Revisora A", "Se delimita un segmento para examinar.")
    assert candidate["level"] == "naranja"
    review_candidate(report, candidate["id"], "accepted", "Revisora A", "Revisado con el audio original.")
    first = strict_json(save_report(report, tmp_path / "resultado"))
    saved = next(row for row in first["candidates"] if row["id"] == candidate["id"])
    assert saved["review"]["status"] == "accepted"
    assert saved["review"]["author"] == "Revisora A"
    assert saved["review"]["comment"] == "Revisado con el audio original."
    assert saved["review"]["updated_at"]
    assert saved["level"] == "rojo"
    assert first["review_log"][0]["previous"]["status"] == "pending"
    review_candidate(first, candidate["id"], "rejected", "Revisor B", "Cambio atribuible al contexto de la grabación.")
    final = strict_json(save_report(first, tmp_path / "resultado"))
    saved_final = next(row for row in final["candidates"] if row["id"] == candidate["id"])
    assert saved_final["review"]["status"] == "rejected"
    assert saved_final["level"] == "gris"
    assert len(final["review_log"]) == 3
    assert final["review_log"][1]["previous"]["status"] == "segment"
    assert final["review_log"][2]["previous"]["status"] == "accepted"
    assert final["review_log"][0]["review"]["author"] == "Revisora A"


def test_review_requires_author_and_justification(tmp_path):
    report = analyze(changing_voice(tmp_path), 0, 2, single_speaker=True, cfg=Config(window_sec=1))
    candidate = report["candidates"][0]
    with pytest.raises(ValueError, match="revisión requiere"):
        review_candidate(report, candidate["id"], "accepted", "", "Justificación")
    with pytest.raises(ValueError, match="revisión requiere"):
        review_candidate(report, candidate["id"], "accepted", "Revisor", "")
    assert candidate["review"]["status"] == "pending"
    assert report["review_log"] == []


def test_exported_intervals_are_inside_original_audio(tmp_path):
    path = wav(tmp_path, np.concatenate([tone(180, 3), tone(260, 2.7)]))
    report = analyze(path, 0, 2, single_speaker=True, cfg=Config(window_sec=1, step_sec=.5))
    exported = strict_json(save_report(report, tmp_path / "resultado"))
    for row in exported["windows"] + exported["candidates"]:
        assert 0 <= row["start"] < row["end"] <= exported["source"]["duration_sec"]


def test_reference_and_analyzed_windows_use_equal_duration(tmp_path):
    """Un contorno variable no debe compararse con rangos obtenidos de ventanas menores."""
    time = np.arange(6 * SR) / SR
    frequency = 160 + 30 * np.sin(2 * np.pi * time / 2)
    samples = .2 * np.sin(2 * np.pi * np.cumsum(frequency) / SR)
    report = analyze(wav(tmp_path, samples), 0, 4, single_speaker=True,
                     cfg=Config(window_sec=2, step_sec=2))
    assert report["baseline"]["subwindow_sec"] == 2
    assert report["baseline"]["valid_subwindows"] == 2
    reference_windows = [row for row in report["windows"] if row["end"] <= 4]
    assert len(reference_windows) == 2
    reference_ranges = [row["measures"]["f0_range_st"] for row in reference_windows]
    assert all(value is not None for value in reference_ranges)
    assert report["baseline"]["statistics"]["f0_range_st"]["median"] == pytest.approx(
        np.median(reference_ranges), abs=1e-6)


@pytest.mark.parametrize("window_sec,reference_end", [(2, 3.9), (1.5, 2.9)])
def test_reference_shorter_than_two_full_windows_is_rejected(tmp_path, window_sec, reference_end):
    path = wav(tmp_path, tone(180, 6))
    with pytest.raises(ValueError, match="dos ventanas"):
        analyze(path, 0, reference_end, single_speaker=True, cfg=Config(window_sec=window_sec))


def test_incomplete_tail_is_not_compared_to_full_duration_reference(tmp_path):
    path = wav(tmp_path, tone(180, 5.7))
    report = analyze(path, 0, 4, single_speaker=True, cfg=Config(window_sec=2, step_sec=.5))
    compared = [row for row in report["windows"] if row["comparison"]["status"] != "abstain"]
    assert compared
    assert all(row["end"] - row["start"] == pytest.approx(2) for row in compared)
