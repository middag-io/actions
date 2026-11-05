"""Tests of scripts/scan.py: inputs, the report reader and the failure rule.

go install and govulncheck are replaced by a fake subprocess.run: no Go, no
network. fixtures/reports/x-text-v0.3.7.json is a real report of govulncheck
v1.9.0, trimmed to the messages that the script reads, for a module that
requires golang.org/x/text v0.3.7 and calls language.ParseAcceptLanguage
(GO-2022-1059, CVE-2022-32149). OSV data from the Go vulnerability database
(https://vuln.go.dev), licensed CC BY 4.0.

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
REPORT = (HERE / "fixtures" / "reports" / "x-text-v0.3.7.json").read_text()
CLEAN = json.dumps({"config": {"scanner_name": "govulncheck"}})


def run(argv):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        status = scan.main(argv)
    return status, buffer.getvalue()


class InputTest(unittest.TestCase):
    def test_severities_are_validated_and_ordered(self):
        self.assertEqual(scan.parse_severities("low,critical"), ("critical", "low"))
        for value in ("", "moderate", "info"):
            with self.subTest(value=value), self.assertRaisesRegex(scan.ScanError, "blocking-severities"):
                scan.parse_severities(value)

    def test_directory_stays_in_the_checkout_and_holds_go_mod(self):
        with self.assertRaisesRegex(scan.ScanError, "leaves the checkout"):
            scan.resolve_directory(FIXTURE, "../..")
        with self.assertRaisesRegex(scan.ScanError, "no go.mod"):
            scan.resolve_directory(HERE, ".")
        with self.assertRaisesRegex(scan.ScanError, "relative directory"):
            scan.resolve_directory(FIXTURE, "/tmp")

    def test_dry_run_must_be_a_boolean(self):
        status, log = run(["plan", "--workspace", str(FIXTURE), "--dry-run", "yes"])
        self.assertEqual(status, 1)
        self.assertIn("input dry-run must be true or false", log)

    def test_dry_run_plan_matches_the_expected_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "out")
            status, log = run(["plan", "--workspace", str(FIXTURE), "--blocking-severities", "critical, high",
                               "--dry-run", "true", "--github-output", output])
            self.assertEqual(status, 0)
            self.assertIn("would run: govulncheck -format json ./...", log)
            self.assertEqual(Path(output).read_text(), EXPECTED.read_text())


class ReportTest(unittest.TestCase):
    def test_reads_each_vulnerability_once_at_its_deepest_level(self):
        found = scan.read_vulnerabilities(scan.parse_stream(REPORT))
        self.assertEqual([(v.osv, v.severity) for v in found],
                         [("GO-2022-1059", "critical"), ("GO-2026-5970", "low"), ("GO-2026-6629", "low")])
        first = found[0]
        self.assertEqual(first.called, "golang.org/x/text/language.ParseAcceptLanguage")
        self.assertEqual((first.module, first.version, first.fixed_version), ("golang.org/x/text", "v0.3.7", "v0.3.8"))
        self.assertIn("CVE-2022-32149", first.aliases)

    def test_method_names_its_receiver(self):
        frame = {"module": "m", "package": "m/p", "function": "Read", "receiver": "*Reader"}
        self.assertEqual(scan.called_symbol(frame), "m/p.*Reader.Read")

    def test_output_without_a_config_message_is_no_report(self):
        for stdout in ("", "go: cannot find main module", json.dumps({"progress": {}})):
            with self.subTest(stdout=stdout), self.assertRaisesRegex(scan.ScanError, "JSON report"):
                scan.parse_stream(stdout)


class RunTest(unittest.TestCase):
    def scan_with(self, stdout, severities, returncode=0):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "go.mod").write_text("module example.com/m\n")
            output = Path(tmp) / "out"
            results = [subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                       subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")]
            fake = mock.Mock(side_effect=results)
            with mock.patch.object(scan.subprocess, "run", fake):
                status, log = run(["run", "--workspace", tmp, "--blocking-severities", severities,
                                   "--github-output", str(output)])
            outputs = dict(line.split("=", 1) for line in output.read_text().splitlines()) if output.exists() else {}
            return status, log, outputs, fake

    def test_fails_on_a_called_vulnerability_with_the_default(self):
        status, log, outputs, fake = self.scan_with(REPORT, "critical")
        self.assertEqual(status, 1)
        self.assertEqual(outputs, {"advisories": "3", "blocking": "1"})
        self.assertIn("::error::GO-2022-1059 (CVE-2022-32149, GHSA-69ch-w2m2-3vjp): golang.org/x/text@v0.3.7: critical", log)
        self.assertIn("::warning::GO-2026-5970", log)
        install, scan_call = fake.call_args_list
        self.assertEqual(install.args[0], ["go", "install", scan.TOOL])
        self.assertEqual(install.kwargs["env"]["GOTOOLCHAIN"], "auto")
        self.assertEqual(scan_call.args[0][1:], list(scan.SCAN_ARGS))

    def test_low_blocks_the_vulnerabilities_that_are_not_called(self):
        status, _, outputs, _ = self.scan_with(REPORT, "low")
        self.assertEqual((status, outputs["blocking"]), (1, "2"))

    def test_high_and_medium_match_no_finding(self):
        status, log, outputs, _ = self.scan_with(REPORT, "high, medium")
        self.assertEqual((status, outputs["blocking"]), (0, "0"))
        self.assertNotIn("::error::", log)

    def test_passes_when_nothing_is_found(self):
        status, _, outputs, _ = self.scan_with(CLEAN, "critical")
        self.assertEqual((status, outputs), (0, {"advisories": "0", "blocking": "0"}))

    def test_tool_error_fails(self):
        status, log, outputs, _ = self.scan_with("", "critical", returncode=1)
        self.assertEqual((status, outputs), (1, {}))
        self.assertIn("govulncheck failed with exit code 1", log)

    def test_failed_install_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "go.mod").write_text("module example.com/m\n")
            fake = mock.Mock(return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="no network"))
            with mock.patch.object(scan.subprocess, "run", fake):
                status, log = run(["run", "--workspace", tmp])
        self.assertEqual(status, 1)
        self.assertIn("go install golang.org/x/vuln/cmd/govulncheck@v1.9.0 failed: no network", log)

    def test_missing_go_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "go.mod").write_text("module example.com/m\n")
            with mock.patch.object(scan.subprocess, "run", mock.Mock(side_effect=FileNotFoundError("go"))):
                status, log = run(["run", "--workspace", tmp])
        self.assertEqual(status, 1)
        self.assertIn("go not found; set up Go before this action", log)


if __name__ == "__main__":
    unittest.main()
