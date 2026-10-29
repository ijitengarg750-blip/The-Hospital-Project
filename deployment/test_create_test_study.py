import runpy
import unittest
from pathlib import Path


SCRIPT = runpy.run_path(Path(__file__).with_name("create-test-study.py"))


class CreateTestStudySecurityTests(unittest.TestCase):
    def test_dashboard_url_requires_https_except_loopback(self):
        validate = SCRIPT["validate_dashboard_url"]
        for url in ("https://dashboard.hospital.example", "http://localhost:8080", "http://127.0.0.1:8080"):
            validate(url)
        for url in ("http://192.168.1.10:8080", "http://dashboard.hospital.example", "not-a-url"):
            with self.assertRaises(SystemExit):
                validate(url)

    def test_redirects_are_disabled(self):
        handler = SCRIPT["NoRedirectHandler"]()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "http://example.test"))


if __name__ == "__main__":
    unittest.main()
