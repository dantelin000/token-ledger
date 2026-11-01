"""Ensure the release audit rejects private files without echoing their values."""
import json
import unittest

from scripts.audit_release import scan


class ReleaseAuditTests(unittest.TestCase):
    def test_secrets_are_detected_without_echoing_values(self):
        token = "gho_" + "x" * 32
        findings = scan("sample.py", ("value = '" + token + "'").encode(), home="/unused-home")
        self.assertIn("credential_token", {row["kind"] for row in findings})
        self.assertNotIn(token, json.dumps(findings))

    def test_local_home_and_private_databases_are_rejected(self):
        self.assertTrue(scan("data/ledger.sqlite3", b"sample", home="/unused-home"))
        self.assertTrue(scan("source.py", b"path = '/local-owner/record'", home="/local-owner"))

    def test_synthetic_usage_example_is_allowed(self):
        self.assertEqual(scan("examples/tokenlog-usage.sample.jsonl", b'{"input_tokens": 100}', home="/unused-home"), [])


if __name__ == "__main__":
    unittest.main()
