import json
from pathlib import Path
import unittest
from unittest.mock import patch

from incident_copilot.analysis import analyze, clean, render_markdown
from incident_copilot.cli import MAX_BODY, make_handler, process


FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "fixtures/oom-alert.json").read_text())


class AnalysisTests(unittest.TestCase):
    def test_oom_case_contains_evidence_and_read_only_checks(self):
        report = analyze(FIXTURE)
        self.assertEqual(report["group_status"], "firing")
        self.assertIn("kubectl describe pod", " ".join(report["alerts"][0]["checks"]))
        self.assertIn("Hipóteses", render_markdown(report))

    def test_invalid_alerts_rejected(self):
        for payload in ({}, {"alerts": []}, {"alerts": ["bad"]}, {"alerts": [{}] * 21}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                analyze(payload)

    def test_sanitizes_annotation_and_command_identifiers(self):
        payload = json.loads(json.dumps(FIXTURE))
        payload["alerts"][0]["annotations"]["summary"] = "token=abc123 Bearer xyz.123 AKIA1234567890123456"
        payload["alerts"][0]["labels"]["pod"] = "x; rm -rf /"
        report = analyze(payload)
        self.assertNotIn("abc123", json.dumps(report))
        self.assertNotIn("xyz.123", json.dumps(report))
        self.assertNotIn("AKIA1234567890123456", json.dumps(report))
        self.assertIn("<pod>", " ".join(report["alerts"][0]["checks"]))
        self.assertNotIn("rm -rf", " ".join(report["alerts"][0]["checks"]))

    def test_ai_unavailable_keeps_deterministic_result(self):
        with patch("incident_copilot.cli.suggest", side_effect=OSError("offline")):
            report = process(FIXTURE, use_ai=True)
        self.assertIn("ai_error", report)
        self.assertTrue(report["alerts"][0]["hypotheses"])


if __name__ == "__main__":
    unittest.main()

