"""Regresiones de configuración de personas antes de procesar una entrevista."""
from __future__ import annotations

import json
from unittest.mock import Mock

import numpy as np
import pytest
import soundfile as sf

from vozafecto import core


def test_missing_person_setup_stops_before_audio_decoding(monkeypatch, tmp_path):
    decode = Mock(side_effect=AssertionError("No debe decodificarse un audio sin configuración de personas."))
    monkeypatch.setattr(core, "load_audio", decode)
    with pytest.raises(core.SpeakerSetupError) as caught:
        core.analyze(tmp_path / "entrevista_larga.mp3", 0, 4, speaker="Entrevistada")
    decode.assert_not_called()
    text = str(caught.value)
    assert "Una sola persona en todo el audio" in text
    assert "varias voces" in text
    assert "Turnos / transcripción" in text
    assert "sólo una parte" in text
    assert "speaker" not in text
    assert "monohablante" not in text


@pytest.mark.parametrize("speaker", [None, "", "   ", "sin_identificar"])
def test_missing_target_name_stops_before_audio_decoding(monkeypatch, tmp_path, speaker):
    decode = Mock(side_effect=AssertionError("No debe decodificarse un audio sin persona objetivo."))
    monkeypatch.setattr(core, "load_audio", decode)
    with pytest.raises(ValueError, match="persona cuya voz"):
        core.analyze(tmp_path / "entrevista.mp3", 0, 4, speaker=speaker, single_speaker=True)
    decode.assert_not_called()


def test_missing_turn_file_stops_before_audio_decoding(monkeypatch, tmp_path):
    decode = Mock(side_effect=AssertionError("No debe decodificarse un audio con turnos inexistentes."))
    monkeypatch.setattr(core, "load_audio", decode)
    with pytest.raises(ValueError, match="archivo de turnos"):
        core.analyze(tmp_path / "entrevista.mp3", 0, 4, speaker="Ana",
                     turns_path=tmp_path / "turnos_inexistentes.csv")
    decode.assert_not_called()


def test_explicit_single_person_setup_needs_no_turn_file():
    assert core.validate_speaker_setup("Ana", True) is None


def test_unknown_person_message_lists_names_from_supplied_turns(tmp_path):
    sr = 16000
    time = np.arange(8 * sr) / sr
    audio = tmp_path / "dos_personas.wav"
    sf.write(audio, .2 * np.sin(2 * np.pi * 180 * time), sr)
    turns = tmp_path / "turnos.json"
    turns.write_text(json.dumps({"segments": [
        {"start": 0, "end": 4, "speaker": "Ana"},
        {"start": 4, "end": 8, "speaker": "Bruno"},
    ]}), encoding="utf-8")
    with pytest.raises(core.SpeakerSetupError) as caught:
        core.analyze(audio, 0, 4, speaker="Carolina", turns_path=turns)
    text = str(caught.value)
    assert "Carolina" in text
    assert "Ana, Bruno" in text
    assert "Seleccione" in text
    assert "referencia dentro de su intervención" in text
    assert "speaker" not in text

