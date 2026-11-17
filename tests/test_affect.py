"""Pruebas del baseline dimensional con tonos y etiquetas numéricas sintéticas.

Estos datos no son hablantes humanos ni evidencian reconocimiento de emociones.
Sólo permiten verificar extracción, separación por grupos, álgebra y auditoría.
"""

from copy import deepcopy
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import soundfile as sf

from vozafecto.affect import MODEL_FEATURES, apply_model, ccc, train
from vozafecto.core import Config, digest


class AffectPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory(prefix="vozafecto_model_test_")
        cls.base = Path(cls.temp.name)
        cls.cfg = Config()
        cls.rows = []
        sr = 16000
        segment_sec = .75
        time = np.arange(round(segment_sec * sr)) / sr
        for speaker_index, speaker in enumerate(("synthetic_a", "synthetic_b", "synthetic_c")):
            parts = []
            for interval in range(4):
                f0 = 130 + speaker_index * 30 + interval * 8
                phase = 2 * np.pi * (f0 * time + .2 * np.sin(2 * np.pi * 2 * time))
                amplitude = .12 + .02 * interval + .01 * speaker_index
                parts.append(amplitude * np.sin(phase) + .035 * np.sin(2 * phase))
                cls.rows.append({"audio": f"{speaker}.wav", "start": interval * segment_sec,
                                 "end": (interval + 1) * segment_sec, "speaker": speaker,
                                 "arousal": (-.6, -.2, .2, .6)[interval],
                                 "valence": (.4, .1, -.1, -.4)[interval]})
            sf.write(cls.base / f"{speaker}.wav", np.concatenate(parts), sr, subtype="PCM_16")
        cls.csv_path = cls.write_csv("corpus.csv", cls.rows)
        cls.model_path = cls.base / "model.json"
        cls.model = train(cls.csv_path, cls.model_path, test_speakers=["synthetic_c"], cfg=cls.cfg)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def write_csv(cls, name, rows):
        path = cls.base / name
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["audio", "start", "end", "speaker", "arousal", "valence"])
            writer.writeheader()
            writer.writerows(rows)
        return path

    def model_file(self, model, name="mutated.json"):
        path = self.base / name
        path.write_text(json.dumps(model), encoding="utf-8")
        return path

    def report_for_held(self, speaker="synthetic_c"):
        examples = [row for row in self.model["corpus"]["examples"] if row["speaker"] == speaker]
        return {"config": vars(self.cfg), "source": {"path": str(self.base / f"{speaker}.wav"),
                    "sha256": digest(self.base / f"{speaker}.wav")},
                "windows": [{"id": f"SYNTHETIC_{i}", "start": item["start"], "end": item["end"],
                             "quality": {"eligible": True}, "measures": dict(zip(MODEL_FEATURES, item["x"]))}
                            for i, item in enumerate(examples)]}

    def test_train_is_held_by_speaker_and_transforms_fit_on_train_only(self):
        model = self.model
        self.assertEqual(model["training_speakers"], ["synthetic_a", "synthetic_b"])
        self.assertEqual(model["test_speakers"], ["synthetic_c"])
        self.assertFalse(set(model["training_speakers"]) & set(model["test_speakers"]))
        self.assertEqual((model["train_n"], model["test_n"]), (8, 4))
        training = [row for row in model["corpus"]["examples"] if row["speaker"] in model["training_speakers"]]
        x = np.array([row["x"] for row in training])
        medians = np.array([np.nanmedian(x[:, j]) if np.isfinite(x[:, j]).any() else 0 for j in range(x.shape[1])])
        clean = np.where(np.isfinite(x), x, medians)
        np.testing.assert_allclose(model["medians"], medians)
        np.testing.assert_allclose(model["means"], clean.mean(axis=0))
        np.testing.assert_allclose(model["scales"], np.maximum(clean.std(axis=0), 1e-8))
        np.testing.assert_allclose(model["target_mean"], np.mean([row["y"] for row in training], axis=0))

    def test_held_labels_cannot_change_fit_or_transforms(self):
        rows = deepcopy(self.rows)
        for row in rows:
            if row["speaker"] == "synthetic_c":
                row["arousal"], row["valence"] = .99, -.99
        changed = train(self.write_csv("changed_test_labels.csv", rows), self.base / "changed_model.json",
                        test_speakers=["synthetic_c"], cfg=self.cfg)
        for key in ("medians", "means", "scales", "coef", "target_mean"):
            np.testing.assert_allclose(changed[key], self.model[key], err_msg=key)
        self.assertNotEqual(changed["metrics"], self.model["metrics"])

    def test_apply_reproduces_held_metrics_and_bounds(self):
        report = apply_model(self.report_for_held(), self.model_path)
        predictions = report["affective_model"]["predictions"]
        self.assertEqual(len(predictions), 4)
        held = [row for row in self.model["corpus"]["examples"] if row["speaker"] == "synthetic_c"]
        for axis, name in enumerate(("arousal", "valence")):
            actual = np.array([row["y"][axis] for row in held])
            predicted = np.array([row[name] for row in predictions])
            self.assertTrue(np.all(np.abs(predicted) <= 1))
            self.assertAlmostEqual(self.model["metrics"][name]["mae"], np.mean(np.abs(actual - predicted)), places=5)
            self.assertAlmostEqual(self.model["metrics"][name]["rmse"], np.sqrt(np.mean((actual - predicted) ** 2)), places=5)
            self.assertAlmostEqual(self.model["metrics"][name]["ccc"], ccc(actual, predicted), places=5)
            constant = self.model["target_mean"][axis]
            self.assertAlmostEqual(self.model["metrics"][name]["constant_baseline_mae"], np.mean(np.abs(actual - constant)), places=5)

    def test_ineligible_windows_abstain_and_missing_feature_is_disclosed(self):
        report = self.report_for_held()
        report["windows"][0]["quality"]["eligible"] = False
        report["windows"][1]["measures"]["hnr_db"] = None
        result = apply_model(report, self.model_path)["affective_model"]["predictions"]
        self.assertEqual(len(result), 3)
        self.assertNotIn("SYNTHETIC_0", [row["window_id"] for row in result])
        self.assertIn("hnr_db", result[0]["imputed_features"])

    def test_source_overlap_discloses_training_and_test_reuse(self):
        training = apply_model(self.report_for_held("synthetic_a"), self.model_path)["affective_model"]["predictions"]
        testing = apply_model(self.report_for_held(), self.model_path)["affective_model"]["predictions"]
        self.assertTrue(all(row["overlaps_training_data"] and not row["overlaps_test_data"] for row in training))
        self.assertTrue(all(row["overlaps_test_data"] and not row["overlaps_training_data"] for row in testing))
        unseen = self.report_for_held()
        unseen["source"]["sha256"] = "0" * 64
        result = apply_model(unseen, self.model_path)["affective_model"]["predictions"]
        self.assertTrue(all(not row["overlaps_training_data"] and not row["overlaps_test_data"] for row in result))

    def test_overlapping_hash_identical_audio_cannot_use_different_speaker(self):
        rows = deepcopy(self.rows)
        alias = self.base / "same_audio_other_name.wav"
        alias.write_bytes((self.base / "synthetic_a.wav").read_bytes())
        rows[4]["audio"] = alias.name
        with self.assertRaisesRegex(ValueError, "solapados"):
            train(self.write_csv("leakage.csv", rows), self.base / "forbidden.json", test_speakers=["synthetic_c"])

    def test_invalid_training_labels_alpha_and_split_refused(self):
        for alpha in (0, -1, float("nan"), float("inf")):
            with self.subTest(alpha=alpha), self.assertRaises(ValueError):
                train(self.csv_path, self.base / "forbidden.json", alpha=alpha)
        for held in (["absent"], ["synthetic_a", "synthetic_b"]):
            with self.subTest(held=held), self.assertRaises(ValueError):
                train(self.csv_path, self.base / "forbidden.json", test_speakers=held)
        rows = deepcopy(self.rows)
        rows[0]["arousal"] = float("nan")
        with self.assertRaises(ValueError):
            train(self.write_csv("nan_labels.csv", rows), self.base / "forbidden.json")

    def test_incompatible_extractor_and_malformed_matrices_refused(self):
        report = self.report_for_held()
        report["config"] = {**report["config"], "pitch_floor": 61}
        with self.assertRaises(ValueError):
            apply_model(report, self.model_path)
        for field, value in (("coef", [[0, 0]]), ("scales", [0] * len(MODEL_FEATURES)),
                             ("medians", [float("nan")] * len(MODEL_FEATURES))):
            with self.subTest(field=field):
                model = deepcopy(self.model)
                model[field] = value
                with self.assertRaises(ValueError):
                    apply_model(self.report_for_held(), self.model_file(model))

    def test_semantically_malformed_model_is_refused(self):
        for field, value in (("type", "another_algorithm"), ("labels", ["valence", "arousal"]),
                             ("label_range", [0, 100]), ("config", None)):
            with self.subTest(field=field):
                model = deepcopy(self.model)
                model[field] = value
                with self.assertRaises(ValueError):
                    apply_model(self.report_for_held(), self.model_file(model))
        with self.assertRaises(ValueError):
            apply_model(self.report_for_held(), self.model_file([]))

    def test_corrupt_provenance_and_overlapping_split_metadata_refused(self):
        for field, value in (("corpus", None), ("corpus", {"examples": "invalid"}),
                             ("corpus", {"examples": [{}]}), ("metrics", None),
                             ("training_speakers", "synthetic_a"),
                             ("training_speakers", ["synthetic_c"])):
            with self.subTest(field=field, value=value):
                model = deepcopy(self.model)
                model[field] = value
                with self.assertRaises(ValueError):
                    apply_model(self.report_for_held(), self.model_file(model))

    def test_concordance_perfect_opposite_and_degenerate(self):
        self.assertEqual(ccc([-1, 0, 1], [-1, 0, 1]), 1)
        self.assertEqual(ccc([-1, 0, 1], [1, 0, -1]), -1)
        self.assertIsNone(ccc([0, 0], [0, 0]))


if __name__ == "__main__":
    unittest.main()
