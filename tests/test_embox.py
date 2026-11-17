"""Pruebas de límites, trazabilidad y conservación del contrato nativo."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import wave
import zipfile

from vozafecto.embox import CHANNEL, EmboxExportError, SERVICE, AUDIO_PROVIDER, embox_export


class EmboxExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.audio = self.base / "entrada.wav"
        with wave.open(str(self.audio), "wb") as handle:
            handle.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            handle.writeframes(b"\0\0" * 16000)
        self.jar = self.base / "audio.jar"
        with zipfile.ZipFile(self.jar, "w") as archive:
            archive.writestr(SERVICE, AUDIO_PROVIDER)
        self.report = {
            "source": {"path": str(self.audio), "sha256": hashlib.sha256(self.audio.read_bytes()).hexdigest(), "duration_sec": 2.0},
            "candidates": [self.candidate("a", "accepted"), self.candidate("b", "pending"), self.candidate("c", "rejected")],
        }
        self.discovery = patch("vozafecto.embox._discover_audio_jar", return_value=self.jar)
        self.discovery.start()
        self.addCleanup(self.discovery.stop)

    @staticmethod
    def candidate(candidate_id, status):
        return {"id": candidate_id, "start": 0.125, "end": 1.5,
                "level": "review", "summary": "Cambio vocal a revisar.", "evidence": {"f0_hz": 180},
                "review": {"status": status, "author": "Revisor" if status == "accepted" else "", "comment": "", "updated_at": "2026-11-17T10:00:00+00:00"}}

    def read(self, path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def test_default_only_accepted_native_fields_ms_and_copy_hash(self):
        result = embox_export(self.report, self.base / "salida")
        project, tags = self.read(result["project_path"]), self.read(result["tags_path"])
        self.assertEqual(result["exported_count"], 1)
        self.assertEqual(project["tags"], tags)
        self.assertEqual(set(tags[0]), {"canal", "source", "code", "desc", "initTimeInMS", "lengthInMS"})
        self.assertEqual(tags[0]["canal"], "Audio PlugIn")
        self.assertEqual((tags[0]["initTimeInMS"], tags[0]["lengthInMS"]), (125, 1375))
        self.assertTrue(Path(tags[0]["source"]).is_absolute())
        self.assertEqual(Path(result["source_path"]).read_bytes(), self.audio.read_bytes())
        provenance = self.read(result["provenance_path"])
        self.assertEqual(provenance["mapping"][0]["review"]["author"], "Revisor")
        self.assertFalse(provenance["compatibility"]["native_affective_panel"])
        self.assertFalse(provenance["compatibility"]["tags_json_direct_import"])

    def test_pending_is_explicit_and_rejected_never_exported(self):
        result = embox_export(self.report, self.base / "salida", include_pending=True)
        tags = self.read(result["tags_path"])
        self.assertEqual(len(tags), 2)
        self.assertTrue(tags[1]["code"].startswith("CANDIDATO "))
        self.assertEqual([item["candidate_id"] for item in self.read(result["provenance_path"])["mapping"]], ["a", "b"])

    def test_human_segment_is_distinct_and_opt_in(self):
        self.report["candidates"].append(self.candidate("d", "segment"))
        self.report["candidates"][-1]["priority"] = 2
        default = embox_export(self.report, self.base / "default")
        self.assertEqual(default["exported_count"], 1)
        result = embox_export(self.report, self.base / "optional", include_pending=True)
        tags = self.read(result["tags_path"])
        self.assertEqual(len(tags), 3)
        self.assertEqual(tags[-1]["code"], "SEGMENTO A REVISAR d")
        mapping = self.read(result["provenance_path"])["mapping"]
        self.assertEqual(mapping[-1]["priority"], 2)

    def test_existing_project_preserved_without_duplicates(self):
        first = embox_export(self.report, self.base / "inicial")
        input_path = Path(first["project_path"])
        project = self.read(input_path)
        project["custom_metadata"] = {"preservar": True}
        input_path.write_text(json.dumps(project), encoding="utf-8")
        before = input_path.read_bytes()
        second = embox_export(self.report, self.base / "copia", input_path)
        self.assertEqual(input_path.read_bytes(), before)
        self.assertEqual(self.read(second["project_path"]), project)
        self.assertEqual(second["exported_count"], 1)
        self.assertEqual(second["added_count"], 0)

    def test_input_project_never_overwritten(self):
        result = embox_export(self.report, self.base / "inicial")
        path = Path(result["project_path"])
        before = path.read_bytes()
        with self.assertRaises(EmboxExportError):
            embox_export(self.report, path.parent, path)
        self.assertEqual(path.read_bytes(), before)

    def test_existing_forward_slashes_are_preserved_in_source_reference(self):
        first = embox_export(self.report, self.base / "inicial")
        path = Path(first["project_path"])
        project = self.read(path)
        reference = Path(project["sources"][0]["pathToSource"]).as_posix()
        project["sources"][0]["pathToSource"] = reference
        project["tags"] = []
        path.write_text(json.dumps(project), encoding="utf-8")
        result = embox_export(self.report, self.base / "copia", path)
        exported = self.read(result["project_path"])
        self.assertEqual(exported["sources"][0]["pathToSource"], reference)
        self.assertEqual(exported["tags"][0]["source"], reference)

    def test_invalid_times_and_types_rejected_before_creating_output(self):
        for start, end in [(True, 1), ("0", 1), (float("nan"), 1), (0, float("inf")), (-1, 1), (1, 1), (0, 3), (0.1, 0.1001)]:
            with self.subTest(start=start, end=end):
                report = deepcopy(self.report)
                report["candidates"][0].update(start=start, end=end)
                output = self.base / "invalida"
                with self.assertRaises(EmboxExportError):
                    embox_export(report, output)
                self.assertFalse(output.exists())

    def test_changed_audio_or_duration_refused(self):
        for update in [{"sha256": "0" * 64}, {"duration_sec": 3}, {"path": "relativa.wav"}]:
            with self.subTest(update=update):
                report = deepcopy(self.report)
                report["source"].update(update)
                with self.assertRaises(EmboxExportError):
                    embox_export(report, self.base / "fallo")

    def test_acceptance_requires_author_and_unique_ids(self):
        report = deepcopy(self.report)
        report["candidates"][0]["review"]["author"] = " "
        with self.assertRaises(EmboxExportError):
            embox_export(report, self.base / "fallo")
        report = deepcopy(self.report)
        report["candidates"][1]["id"] = "a"
        with self.assertRaises(EmboxExportError):
            embox_export(report, self.base / "fallo")

    def test_wrong_catalog_name_and_multiple_sources_refused(self):
        first = embox_export(self.report, self.base / "inicial")
        for mode in ("catalog_name", "multiple_sources"):
            with self.subTest(mode=mode):
                project = self.read(first["project_path"])
                if mode == "catalog_name":
                    project["sources"][0]["canal"] = "Complemento Vocal"
                else:
                    second_audio = self.base / "segunda.wav"
                    second_audio.write_bytes(self.audio.read_bytes())
                    project["sources"].append({"canal": CHANNEL, "pathToSource": str(second_audio)})
                path = self.base / f"{mode}.json"
                path.write_text(json.dumps(project), encoding="utf-8")
                with self.assertRaises(EmboxExportError):
                    embox_export(self.report, self.base / f"output_{mode}", path)

    def test_unrelated_output_project_not_overwritten(self):
        output = self.base / "ajeno"
        output.mkdir()
        original = '{"name":"otra persona"}'
        (output / "project.json").write_text(original, encoding="utf-8")
        with self.assertRaises(EmboxExportError):
            embox_export(self.report, output)
        self.assertEqual((output / "project.json").read_text(encoding="utf-8"), original)

    def test_safe_reexport_is_idempotent(self):
        first = embox_export(self.report, self.base / "salida")
        before = Path(first["project_path"]).read_bytes()
        second = embox_export(self.report, self.base / "salida")
        self.assertEqual(second["exported_count"], 1)
        self.assertEqual(Path(first["project_path"]).read_bytes(), before)

    def test_explicit_valid_jar_works_without_auto_discovery(self):
        with patch("vozafecto.embox._discover_audio_jar", side_effect=AssertionError("No usar autodescubrimiento")):
            result = embox_export(self.report, self.base / "portable", audio_jar=self.jar)
        project = self.read(result["project_path"])
        self.assertEqual(project["channels"], [str(self.jar.resolve())])
        self.assertEqual(result["exported_count"], 1)

    def test_explicit_incompatible_jar_refused_before_output(self):
        incompatible = self.base / "otro_canal.jar"
        with zipfile.ZipFile(incompatible, "w") as archive:
            archive.writestr(SERVICE, "example.AnotherProvider")
        output = self.base / "incompatible"
        with self.assertRaisesRegex(EmboxExportError, "no provee AudioController"):
            embox_export(self.report, output, audio_jar=incompatible)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
