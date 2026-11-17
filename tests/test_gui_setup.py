"""Estado visible al cambiar de demo a entrevista y al importar personas.

Usa una ventana Tk retirada inmediatamente; no abre entrevistas reales ni EXE.
"""
from __future__ import annotations

import hashlib
import json
from unittest.mock import Mock

import numpy as np
import pytest

from vozafecto import gui


@pytest.fixture
def app(monkeypatch):
    window = gui.App()
    window.withdraw()
    window.info_messages = []
    window.error_messages = []
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *args, **kwargs: window.info_messages.append(args))
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args, **kwargs: window.error_messages.append(args))
    yield window
    for timer in window.tk.call("after", "info"):
        # Los callbacks del Canvas pertenecen a ese widget; cancelar sólo el
        # temporizador evita borrar dos veces su comando durante destroy().
        window.tk.call("after", "cancel", timer)
    window.destroy()


def test_selecting_new_audio_invalidates_inherited_single_person_declaration(app, monkeypatch, tmp_path):
    audio = tmp_path / "entrevista.mp3"
    audio.write_bytes(b"Fuente de prueba opaca: no debe leerse ni modificarse.")
    before = hashlib.sha256(audio.read_bytes()).hexdigest()
    app.single.set(True)
    app.speaker.set("sintetico_A")
    app.speaker_control.configure(values=("sintetico_A",))
    app.turns_path.set("turnos_de_la_grabacion_anterior.json")
    app.report = {"source": "analisis_anterior"}
    app.audio, app.sr = np.zeros(10), 16000
    app.tree.insert("", "end", iid="C0001", text="C0001")
    monkeypatch.setattr(gui.filedialog, "askopenfilename", lambda **kwargs: str(audio))
    decode = Mock(side_effect=AssertionError("Seleccionar una ruta no debe analizar su audio."))
    monkeypatch.setattr(gui, "load_audio", decode)

    app.choose_audio()

    assert app.audio_path.get() == str(audio)
    assert app.single.get() is False
    assert app.turns_path.get() == ""
    assert app.speaker.get() == "hablante_1"
    assert tuple(app.speaker_control.cget("values")) == ()
    assert app.report is None and app.audio is None and app.sr is None
    assert app.tree.get_children() == ()
    assert "turnos" in app.status.get().lower()
    assert "manualmente" in app.detail.get("1.0", "end")
    decode.assert_not_called()
    assert hashlib.sha256(audio.read_bytes()).hexdigest() == before


def test_canceling_audio_selection_preserves_current_source_declaration(app, monkeypatch):
    app.audio_path.set("fuente_actual.wav")
    app.single.set(True)
    app.speaker.set("Ana")
    monkeypatch.setattr(gui.filedialog, "askopenfilename", lambda **kwargs: "")
    app.choose_audio()
    assert app.audio_path.get() == "fuente_actual.wav"
    assert app.single.get() is True
    assert app.speaker.get() == "Ana"


@pytest.mark.parametrize("previous,expected", [("hablante_1", "Ana"), ("Bruno", "Bruno")])
def test_importing_turns_populates_person_selector_and_clears_single_person_mode(
        app, monkeypatch, tmp_path, previous, expected):
    path = tmp_path / "turnos.json"
    path.write_text(json.dumps({"segments": [
        {"start": 0, "end": 4, "speaker": "Bruno"},
        {"start": 4, "end": 8, "speaker": "Ana"},
        {"start": 8, "end": 9},
    ]}), encoding="utf-8")
    before = path.read_bytes()
    app.single.set(True)
    app.speaker.set(previous)
    monkeypatch.setattr(gui.filedialog, "askopenfilename", lambda **kwargs: str(path))

    app.choose_turns()

    assert app.single.get() is False
    assert app.turns_path.get() == str(path)
    assert tuple(app.speaker_control.cget("values")) == ("Ana", "Bruno")
    assert app.speaker.get() == expected
    assert "Ana, Bruno" in app.status.get()
    assert app.error_messages == []
    assert path.read_bytes() == before


def test_missing_person_setup_is_explained_before_starting_analysis_thread(app, monkeypatch, tmp_path):
    audio = tmp_path / "entrevista_larga.mp3"
    audio.write_bytes(b"No es necesario decodificar esta fuente para validar el formulario.")
    app.audio_path.set(str(audio))
    app.single.set(False)
    app.turns_path.set("")
    worker = Mock(side_effect=AssertionError("La configuración inválida no debe iniciar trabajo."))
    monkeypatch.setattr(gui.threading, "Thread", worker)

    app.run_analysis()

    worker.assert_not_called()
    assert app.busy is False
    assert str(app.run_button.cget("state")) == "normal"
    assert len(app.info_messages) == 1
    assert "quién habla" in app.info_messages[0][0]
    assert "Una sola persona en todo el audio" in app.info_messages[0][1]
    assert app.error_messages == []
