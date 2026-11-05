"""Tests of scripts/scan.py: inputs, the report reader and the failure rule.

composer audit is replaced by a fake subprocess.run: no Composer, no network.

Run: python3 -I -B -m unittest discover -s tests -v (from the component folder).
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import scan  # noqa: E402

FIXTURE = HERE / "fixtures" / "basic"
EXPECTED = HERE / "fixtures" / "expected" / "basic.env"

REPORT = {
    "advisories": {
        "vendor/a": [
            {"advisoryId": "PKSA-1", "title": "Remote code execution", "link": "https://example.com/1", "severity": "critical"},
            {"advisoryId": "PKSA-2", "title": "Open redirect", "link": "https://example.com/2", "severity": "medium"},
        ],
        "vendor/b": {"0": {"advisoryId": "PKSA-3", "title": "Header leak", "severity": "high"}},
        "vendor/c": [{"advisoryId": "PKSA-4", "title": "No score", "severity": None}],
    },
    "abandoned": {"vendor/old": "vendor/new", "vendor/gone": None},
}


def run(argv):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        status = scan.main(argv)
    return status, buffer.getvalue()


class InputTest(unittest.TestCase):
    def test_severities_are_validated_and_ordered(self):
        self.assertEqual(scan.parse_severities("high, critical"), ("critical", "high"))
        self.assertEqual(scan.parse_severities("critical\nhigh medium"), ("critical", "high", "medium"))
        for value in ("", " , ", "severe", "critical, moderate"):
            with self.subTest(value=value), self.assertRaisesRegex(scan.ScanError, "blocking-severities"):
                scan.parse_severities(value)

    def test_directory_stays_in_the_checkout_and_holds_composer_json(self):
        with self.assertRaisesRegex(scan.ScanError, "leaves the checkout"):
            scan.resolve_directory(FIXTURE, "..")
        with self.assertRaisesRegex(scan.ScanError, "relative"):
            scan.resolve_directory(FIXTURE, "/tmp")
        with self.assertRaisesRegex(scan.ScanError, "no composer.json"):
            scan.resolve_directory(HERE, ".")
        self.assertEqual(scan.resolve_directory(FIXTURE, "."), FIXTURE)

    def test_dry_run_plan_matches_the_expected_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "out")
            status, log = run(["plan", "--workspace", str(FIXTURE), "--blocking-severities", "high, critical",
                               "--dry-run", "true", "--github-output", output])
            self.assertEqual(status, 0)
            self.assertIn("would run: composer audit", log)
            self.assertEqual(Path(output).read_text(), EXPECTED.read_text())


class CommandTest(unittest.TestCase):
    def test_audits_installed_packages_then_the_lock_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with self.assertRaisesRegex(scan.ScanError, "nothing to audit"):
                scan.audit_command(directory)
            (directory / "composer.lock").write_text("{}")
            self.assertEqual(scan.audit_command(directory)[-1], "--locked")
            (directory / "vendor" / "composer").mkdir(parents=True)
            (directory / "vendor" / "composer" / "installed.json").write_text("{}")
            self.assertEqual(scan.audit_command(directory), list(scan.AUDIT_COMMAND))


class ReportTest(unittest.TestCase):
    def test_reads_lists_and_mappings_of_advisories(self):
        advisories = scan.read_advisories(REPORT)
        self.assertEqual([a.severity for a in advisories], ["critical", "medium", "high", "unknown"])
        self.assertEqual(advisories[2].package, "vendor/b")

    def test_empty_report_has_no_advisory(self):
        self.assertEqual(scan.read_advisories({"advisories": []}), [])

    def test_report_must_be_json(self):
        with self.assertRaises(scan.ScanError):
            scan.parse_report("")
        with self.assertRaises(scan.ScanError):
            scan.parse_report("Could not reach packagist {")
        self.assertEqual(scan.parse_report('noise\n{"advisories": []}'), {"advisories": []})

    def test_abandoned_packages_are_listed(self):
        self.assertEqual(scan.read_abandoned(REPORT), ["vendor/gone (no replacement)", "vendor/old (use vendor/new)"])


class RunTest(unittest.TestCase):
    def scan_with(self, report, severities, stdout=None):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "composer.json").write_text("{}")
            (directory / "composer.lock").write_text("{}")
            output = directory / "out"
            text = json.dumps(report) if stdout is None else stdout
            fake = mock.Mock(return_value=subprocess.CompletedProcess([], 1, stdout=text, stderr=""))
            with mock.patch.object(scan.subprocess, "run", fake):
                status, log = run(["run", "--workspace", tmp, "--blocking-severities", severities,
                                   "--github-output", str(output)])
            outputs = dict(line.split("=", 1) for line in output.read_text().splitlines()) if output.exists() else {}
            return status, log, outputs, fake

    def test_fails_on_a_blocking_severity_only(self):
        status, log, outputs, _ = self.scan_with(REPORT, "critical")
        self.assertEqual(status, 1)
        self.assertEqual(outputs, {"advisories": "4", "blocking": "1"})
        self.assertIn("::error::vendor/a: critical: Remote code execution", log)
        self.assertIn("::warning::vendor/b: high: Header leak", log)
        self.assertIn("::warning::abandoned package: vendor/old (use vendor/new)", log)

    def test_passes_when_no_advisory_is_blocking(self):
        report = {"advisories": {"vendor/a": [{"title": "x", "severity": "low"}]}}
        status, _, outputs, fake = self.scan_with(report, "critical, high")
        self.assertEqual(status, 0)
        self.assertEqual(outputs["blocking"], "0")
        self.assertEqual(fake.call_args.args[0][-1], "--locked")

    def test_unknown_severity_is_reported_not_blocking(self):
        status, log, _, _ = self.scan_with({"advisories": {"vendor/c": [{"title": "t", "severity": None}]}}, "low")
        self.assertEqual(status, 0)
        self.assertIn("::warning::vendor/c: unknown", log)

    def test_audit_without_report_fails(self):
        status, log, _, _ = self.scan_with(None, "critical", stdout="")
        self.assertEqual(status, 1)
        self.assertIn("did not print a JSON report", log)


if __name__ == "__main__":
    unittest.main()
