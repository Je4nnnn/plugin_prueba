"""Adaptador local al project.json histórico; no implementa un canal afectivo Java.

El contrato se verificó en ScanS-EmBox 0.5 y AudioController del workspace.
Sólo las propuestas aceptadas se exportan por defecto. El reporte científico
permanece separado de los seis campos que entiende la línea de tiempo.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
from typing import Any
import wave
import zipfile


CHANNEL = "Audio PlugIn"
ADAPTER = "vozafecto.embox/1"
SERVICE = "META-INF/services/com.emcoders.scansembox.Models.CanalModel"
AUDIO_PROVIDER = "com.emcoders.audioplugin.AudioController"
TAG_FIELDS = {"canal", "source", "code", "desc", "initTimeInMS", "lengthInMS"}


class EmboxExportError(ValueError):
    """La exportación no puede conservar el vínculo audio-tiempo-revisión."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EmboxExportError(f"{label} debe ser un número, sin cadenas ni booleanos.")
    if not math.isfinite(value):
        raise EmboxExportError(f"{label} debe ser finito.")
    return float(value)


def _absolute_file(value: Any, label: str) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise EmboxExportError(f"Falta {label}.")
    path = Path(value)
    if not path.is_absolute():
        raise EmboxExportError(f"{label} debe ser una ruta absoluta.")
    path = path.resolve()
    if not path.is_file():
        raise EmboxExportError(f"No existe {label}: {path}")
    return path


def _json_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EmboxExportError(f"Los datos no son JSON estándar: {exc}") from exc


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise EmboxExportError(f"No se pudo leer {path}: {exc}") from exc


def _audio_jar(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            providers = archive.read(SERVICE).decode("utf-8").splitlines()
            return AUDIO_PROVIDER in [line.split("#", 1)[0].strip() for line in providers]
    except (OSError, KeyError, UnicodeError, zipfile.BadZipFile):
        return False


def _discover_audio_jar(source: Path, output: Path) -> Path:
    roots = []
    for anchor in (Path(__file__).resolve(), source, output, Path.cwd().resolve()):
        roots.extend(anchor.parents)
    for root in dict.fromkeys(roots):
        plugin_dir = root / "EMBOX_LOCAL" / "app" / "plugins"
        for jar in sorted(plugin_dir.glob("*.jar")):
            if _audio_jar(jar):
                return jar.resolve()
    raise EmboxExportError("No se encontró el JAR vocal en EMBOX_LOCAL/app/plugins. Seleccione un proyecto EMBox existente.")


def _source(report: dict) -> tuple[Path, str, float]:
    info = report.get("source")
    if not isinstance(info, dict):
        raise EmboxExportError("El reporte no contiene source.")
    path = _absolute_file(info.get("path"), "source.path")
    expected_hash = info.get("sha256")
    actual_hash = _sha256(path)
    if not isinstance(expected_hash, str) or actual_hash != expected_hash.lower():
        raise EmboxExportError("El SHA-256 del audio no coincide con el reporte. Analice nuevamente la entrada.")
    duration = _number(info.get("duration_sec"), "source.duration_sec")
    if duration <= 0:
        raise EmboxExportError("La duración del audio debe ser positiva.")
    if path.suffix.lower() != ".wav":
        raise EmboxExportError("Esta exportación requiere una copia WAV PCM con el mismo origen temporal; no convierte ni recorta audio.")
    try:
        with wave.open(str(path), "rb") as handle:
            actual_duration = handle.getnframes() / handle.getframerate()
            tolerance = max(0.001, 1 / handle.getframerate())
    except (OSError, EOFError, wave.Error, ZeroDivisionError) as exc:
        raise EmboxExportError("No se pudo verificar el WAV PCM de entrada.") from exc
    if abs(actual_duration - duration) > tolerance:
        raise EmboxExportError("La duración del reporte no coincide con el WAV actual.")
    return path, actual_hash, actual_duration


def _validate_project(project: dict, duration: float, digest: str) -> Path:
    if not isinstance(project, dict) or not isinstance(project.get("name"), str):
        raise EmboxExportError("El proyecto requiere name como cadena.")
    for field in ("channels", "sources", "tags"):
        if not isinstance(project.get(field), list):
            raise EmboxExportError(f"El proyecto requiere {field} como arreglo.")
    jars = [_absolute_file(value, "channels[]") for value in project["channels"]]
    if not any(_audio_jar(jar) for jar in jars):
        raise EmboxExportError("El proyecto no tiene el proveedor Audio PlugIn compatible.")
    source_keys = set()
    vocal_sources = []
    for entry in project["sources"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("canal"), str):
            raise EmboxExportError("Una fuente del proyecto no contiene canal.")
        path = _absolute_file(entry.get("pathToSource"), "sources[].pathToSource")
        key = (entry["canal"], path)
        if key in source_keys:
            raise EmboxExportError("El proyecto contiene una fuente duplicada.")
        source_keys.add(key)
        if entry["canal"] == CHANNEL:
            vocal_sources.append(path)
    if len(vocal_sources) != 1:
        raise EmboxExportError("Se requiere exactamente una fuente Audio PlugIn; el canal histórico conserva un solo reproductor.")
    audio_path = vocal_sources[0]
    if _sha256(audio_path) != digest:
        raise EmboxExportError("El proyecto corresponde a otro audio: su SHA-256 no coincide.")
    for tag in project["tags"]:
        if not isinstance(tag, dict) or not TAG_FIELDS.issubset(tag):
            raise EmboxExportError("El proyecto contiene una etiqueta incompleta.")
        if not all(isinstance(tag[key], str) for key in ("canal", "source", "code", "desc")):
            raise EmboxExportError("Las etiquetas requieren canal/source/code/desc como cadenas.")
        tag_source = _absolute_file(tag["source"], "tags[].source")
        if (tag["canal"], tag_source) not in source_keys:
            raise EmboxExportError("Una etiqueta no corresponde a ninguna fuente de su canal.")
        start = _number(tag["initTimeInMS"], "tags[].initTimeInMS")
        length = _number(tag["lengthInMS"], "tags[].lengthInMS")
        if start < 0 or length <= 0:
            raise EmboxExportError("Las etiquetas requieren inicio no negativo y duración positiva.")
        if tag["canal"] == CHANNEL and start + length > round(duration * 1000):
            raise EmboxExportError("Una etiqueta vocal existente excede la duración del audio.")
    return audio_path


def _candidates(report: dict, duration: float, include_pending: bool) -> list[dict]:
    candidates = report.get("candidates")
    if not isinstance(candidates, list):
        raise EmboxExportError("El reporte requiere candidates como arreglo.")
    chosen = []
    ids = set()
    for entry in candidates:
        if not isinstance(entry, dict):
            raise EmboxExportError("Cada propuesta debe ser un objeto.")
        candidate_id = entry.get("id")
        if not isinstance(candidate_id, str) or not candidate_id.strip() or candidate_id in ids:
            raise EmboxExportError("Cada propuesta requiere un id de texto único y no vacío.")
        ids.add(candidate_id)
        start = _number(entry.get("start"), f"{candidate_id}.start")
        end = _number(entry.get("end"), f"{candidate_id}.end")
        if not 0 <= start < end <= duration:
            raise EmboxExportError(f"Intervalo inválido o fuera del audio: {candidate_id}.")
        if round(end * 1000) <= round(start * 1000):
            raise EmboxExportError(f"El intervalo {candidate_id} colapsa al convertirlo a milisegundos.")
        review = entry.get("review")
        if not isinstance(review, dict) or review.get("status") not in {"pending", "segment", "accepted", "rejected"}:
            raise EmboxExportError(f"Revisión no válida para {candidate_id}.")
        status = review["status"]
        if status == "accepted" and not str(review.get("author") or "").strip():
            raise EmboxExportError(f"La aceptación de {candidate_id} requiere identificar al revisor.")
        if status == "accepted" or (include_pending and status in {"pending", "segment"}):
            if not isinstance(entry.get("summary"), str) or not entry["summary"].strip():
                raise EmboxExportError(f"Falta el resumen de {candidate_id}.")
            chosen.append(entry)
    return sorted(chosen, key=lambda entry: (entry["start"], entry["end"], entry["id"]))


def _tag_key(tag: dict) -> tuple:
    return tuple(str(Path(tag[key]).resolve()) if key == "source" else tag[key] for key in sorted(TAG_FIELDS))


def embox_export(
    report: dict,
    output_dir: Path,
    project_path: Path | None = None,
    include_pending: bool = False,
    audio_jar: Path | None = None,
) -> dict:
    """Genera project.json, tags_embox.json y procedencia_embox.json.

    Si no se entrega proyecto, copia el WAV sin modificar y descubre el JAR
    vocal local. Si se entrega proyecto, conserva todas sus fuentes, canales,
    campos y etiquetas; la fuente vocal debe tener el mismo hash que el reporte.
    Nunca escribe sobre el proyecto de entrada. El JSON de etiquetas es un
    artefacto de inspección, no un formato de importación directa del núcleo.
    """
    if not isinstance(report, dict) or not isinstance(include_pending, bool):
        raise EmboxExportError("Se requiere un reporte dict y include_pending booleano.")
    source, digest, duration = _source(report)
    selected = _candidates(report, duration, include_pending)
    output = Path(output_dir).resolve()
    project_output = output / "project.json"
    tags_output = output / "tags_embox.json"
    provenance_output = output / "procedencia_embox.json"
    copy_audio = False
    input_project = None
    if project_path is not None:
        input_project = Path(project_path).resolve()
        if input_project.is_dir():
            input_project = input_project / "project.json"
        if input_project == project_output:
            raise EmboxExportError("La carpeta de salida debe ser distinta del proyecto original.")
        project = deepcopy(_load_json(input_project))
        source_in_project = _validate_project(project, duration, digest)
        source_reference = next(entry["pathToSource"] for entry in project["sources"] if entry["canal"] == CHANNEL)
    else:
        jar = _absolute_file(audio_jar,'JAR vocal') if audio_jar else _discover_audio_jar(source, output)
        if not _audio_jar(jar):
            raise EmboxExportError('El JAR seleccionado no provee AudioController compatible con EmBox.')
        source_in_project = output / f"audio_{digest[:16]}.wav"
        copy_audio = source_in_project != source
        project = {
            "name": "Voz Afectiva — prueba de seminario",
            "channels": [str(jar)],
            "sources": [{"canal": CHANNEL, "pathToSource": str(source_in_project)}],
            "tags": [],
        }
        source_reference = str(source_in_project)
    new_tags = []
    mapping = []
    for candidate in selected:
        review = candidate["review"]
        state_label = {"accepted": "PIN REVISADO", "segment": "SEGMENTO A REVISAR", "pending": "CANDIDATO"}[review["status"]]
        tag = {
            "canal": CHANNEL,
            "source": source_reference,
            "code": f"{state_label} {candidate['id']}",
            "desc": f"{candidate['summary']} | Evento {candidate['id']}. Estado: {review['status']}. "
                    "Propuesta de revisión vocal; no constituye diagnóstico ni prueba de emoción. "
                    "Método y evidencia en el reporte y procedencia_embox.json.",
            "initTimeInMS": round(candidate["start"] * 1000),
            "lengthInMS": round(candidate["end"] * 1000) - round(candidate["start"] * 1000),
        }
        new_tags.append(tag)
        mapping.append({
            "candidate_id": candidate["id"],
            "start_sec": candidate["start"], "end_sec": candidate["end"],
            "level": candidate.get("level"), "priority": candidate.get("priority"), "summary": candidate["summary"],
            "evidence": deepcopy(candidate.get("evidence")), "review": deepcopy(review),
            "native_tag": tag,
        })
    existing_keys = {_tag_key(tag) for tag in project["tags"]}
    added_count = 0
    for tag in new_tags:
        key = _tag_key(tag)
        if key not in existing_keys:
            project["tags"].append(tag)
            existing_keys.add(key)
            added_count += 1
    provenance = {
        "schema_version": "1.0", "adapter": ADAPTER,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {"path": str(source), "sha256": digest, "duration_sec": duration},
        "source_in_project": str(source_in_project),
        "original_project": str(input_project) if input_project else None,
        "original_project_sha256": _sha256(input_project) if input_project else None,
        "report_sha256": hashlib.sha256(_json_bytes(report)).hexdigest(),
        "include_pending": include_pending, "exported_count": len(new_tags),
        "added_count": added_count, "mapping": mapping,
        "compatibility": {
            "channel": CHANNEL, "time_unit": "milliseconds", "rounding": "nearest millisecond",
            "entrypoint": "Abrir la carpeta como proyecto EMBox; project.json.",
            "native_affective_panel": False,
            "tags_json_direct_import": False,
            "detail": "El canal histórico muestra intervalos. Sus colores y Ver detalle no interpretan el análisis afectivo.",
        },
    }
    payloads = [(project_output, _json_bytes(project)), (tags_output, _json_bytes(new_tags)),
                (provenance_output, _json_bytes(provenance))]
    if project_output.exists():
        previous = _load_json(provenance_output) if provenance_output.is_file() else {}
        previous_source = previous.get("source") if isinstance(previous, dict) else None
        if not isinstance(previous_source, dict) or previous.get("adapter") != ADAPTER or previous_source.get("sha256") != digest:
            raise EmboxExportError("La salida ya contiene un proyecto ajeno a esta exportación; use otra carpeta.")
    if copy_audio and source_in_project.exists() and _sha256(source_in_project) != digest:
        raise EmboxExportError("El archivo de audio de destino ya existe con otro contenido.")
    output.mkdir(parents=True, exist_ok=True)
    if copy_audio and not source_in_project.exists():
        shutil.copy2(source, source_in_project)
        if _sha256(source_in_project) != digest:
            raise EmboxExportError("La copia WAV no conserva el SHA-256 de la fuente.")
    for path, payload in payloads:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_bytes(payload)
        temp.replace(path)
    return {
        "project_path": str(project_output), "tags_path": str(tags_output),
        "provenance_path": str(provenance_output), "source_path": str(source_in_project),
        "exported_count": len(new_tags), "added_count": added_count,
        "include_pending": include_pending,
    }
