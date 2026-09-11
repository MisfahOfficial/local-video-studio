from __future__ import annotations

import unittest

from app.providers.base import ProviderError
from app.providers.http import _request_headers, verified_ssl_context


class ProviderHttpTests(unittest.TestCase):
    def test_verified_ssl_context_requires_certificates(self) -> None:
        context = verified_ssl_context()
        self.assertEqual(context.verify_mode.name, "CERT_REQUIRED")
        self.assertTrue(context.check_hostname)

    def test_api_key_with_unicode_copy_artifact_is_explained(self) -> None:
        with self.assertRaisesRegex(ProviderError, "API key contains an unsupported copied character"):
            _request_headers({"Authorization": "Bearer key–with–smart–dashes"})

    def test_valid_authorization_header_is_preserved(self) -> None:
        headers = _request_headers({"Authorization": "Bearer ascii-key-123"})
        self.assertEqual(headers["Authorization"], "Bearer ascii-key-123")
        self.assertEqual(headers["User-Agent"], "LocalVideoStudio/0.6.0")


if __name__ == "__main__":
    unittest.main()
