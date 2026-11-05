"""Tests of scripts/scan.py: inputs, the report reader and the failure rule.

npm audit is replaced by a fake subprocess.run: no npm, no network.

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
    "auditReportVersion": 2,
    "vulnerabilities": {
        "lib-a": {
            "name": "lib-a", "severity": "critical",
            "via": [
                {"source": 1, "name": "lib-a", "title": "Code injection", "url": "https://example.com/1", "severity": "critical"},
                {"source": 2, "name": "lib-a", "title": "ReDoS", "url": "https://example.com/2", "severity": "moderate"},
            ],
        },
        "lib-b": {"name": "lib-b", "severity": "critical", "via": ["lib-a"]},
        "lib-c": {
            "name": "lib-c", "severity": "low",
            "via": [{"source": 3, "name": "lib-c", "title": "Info leak", "url": "https://example.com/3", "severity": "low"}],
        },
    },
    "metadata": {"vulnerabilities": {"critical": 2, "low": 1, "total": 3}},
}


def run(argv):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        status = scan.main(argv)
    return status, buffer.getvalue()


class InputTest(unittest.TestCase):
    def test_severities_are_validated_and_ordered(self):
        self.assertEqual(scan.parse_severities("medium,critical"), ("critical", "medium"))
        for value in ("", "moderate", "info"):
            with self.subTest(value=value), self.assertRaisesRegex(scan.ScanError, "blocking-severities"):
                scan.parse_severities(value)

    def test_directory_stays_in_the_checkout_and_holds_the_lock_file(self):
        with self.assertRaisesRegex(scan.ScanError, "leaves the checkout"):
            scan.resolve_directory(FIXTURE, "../..")
        with self.assertRaisesRegex(scan.ScanError, "no package-lock.json"):
            scan.resolve_directory(HERE, ".")

    def test_dry_run_plan_matches_the_expected_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "out")
            status, log = run(["plan", "--workspace", str(FIXTURE), "--blocking-severities", "critical, high",
                               "--dry-run", "true", "--github-output", output])
            self.assertEqual(status, 0)
            self.assertIn("would run: npm audit --json --package-lock-only", log)
            self.assertEqual(Path(output).read_text(), EXPECTED.read_text())


class ReportTest(unittest.TestCase):
    def test_reads_each_advisory_once_and_maps_moderate(self):
        advisories = scan.read_advisories(REPORT)
        self.assertEqual([(a.package, a.severity) for a in advisories],
                         [("lib-a", "critical"), ("lib-a", "medium"), ("lib-c", "low")])

    def test_npm_error_fails(self):
        with self.assertRaisesRegex(scan.ScanError, "ENOLOCK"):
            scan.read_advisories({"error": {"code": "ENOLOCK", "summary": "no lock file"}})


class RunTest(unittest.TestCase):
    def scan_with(self, report, severities):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "package-lock.json").write_text("{}")
            output = Path(tmp) / "out"
            fake = mock.Mock(return_value=subprocess.CompletedProcess([], 1, stdout=json.dumps(report), stderr=""))
            with mock.patch.object(scan.subprocess, "run", fake):
                status, log = run(["run", "--workspace", tmp, "--blocking-severities", severities,
                                   "--github-output", str(output)])
            outputs = dict(line.split("=", 1) for line in output.read_text().splitlines()) if output.exists() else {}
            return status, log, outputs, fake

    def test_fails_on_a_blocking_severity_only(self):
        status, log, outputs, fake = self.scan_with(REPORT, "medium")
        self.assertEqual(status, 1)
        self.assertEqual(outputs, {"advisories": "3", "blocking": "1"})
        self.assertIn("::error::lib-a: medium: ReDoS", log)
        self.assertIn("::warning::lib-a: critical", log)
        self.assertEqual(fake.call_args.args[0], list(scan.AUDIT_COMMAND))

    def test_passes_with_the_default_when_nothing_is_critical(self):
        report = {"vulnerabilities": {"lib-c": REPORT["vulnerabilities"]["lib-c"]}}
        status, _, outputs, _ = self.scan_with(report, "critical")
        self.assertEqual((status, outputs["blocking"]), (0, "0"))


if __name__ == "__main__":
    unittest.main()
