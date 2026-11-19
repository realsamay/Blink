import unittest
from unittest.mock import patch, MagicMock
import io
import json

from blink.core.updater import parse_semver, is_newer_version, check_for_updates, CURRENT_VERSION


class TestUpdater(unittest.TestCase):
    def test_parse_semver(self):
        self.assertEqual(parse_semver("v2.2.0"), (2, 2, 0))
        self.assertEqual(parse_semver("2.1.0"), (2, 1, 0))
        self.assertEqual(parse_semver("v3.0.1"), (3, 0, 1))
        self.assertEqual(parse_semver("1.0"), (1, 0, 0))
        self.assertEqual(parse_semver("invalid"), (0, 0, 0))

    def test_is_newer_version(self):
        self.assertTrue(is_newer_version("2.2.1", "2.2.0"))
        self.assertTrue(is_newer_version("2.3.0", "2.2.0"))
        self.assertTrue(is_newer_version("3.0.0", "2.2.0"))
        self.assertFalse(is_newer_version("2.2.0", "2.2.0"))
        self.assertFalse(is_newer_version("2.1.9", "2.2.0"))
        self.assertFalse(is_newer_version("1.9.9", "2.2.0"))

    @patch("urllib.request.urlopen")
    def test_check_for_updates_mock_available(self, mock_urlopen):
        fake_payload = {
            "tag_name": "v2.3.0",
            "name": "Blink v2.3.0 Release",
            "body": "- Fixed camera latency\n- Improved accuracy",
            "published_at": "2026-11-20T00:00:00Z",
            "html_url": "https://github.com/realsamay/Blink/releases/tag/v2.3.0",
            "assets": [
                {
                    "name": "blink_2.3.0_amd64.deb",
                    "browser_download_url": "https://github.com/realsamay/Blink/releases/download/v2.3.0/blink_2.3.0_amd64.deb",
                    "size": 45000000
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(fake_payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = check_for_updates()
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["has_update"])
        self.assertEqual(res["latest_version"], "2.3.0")
        self.assertIn("blink_2.3.0_amd64.deb", res["deb_url"])
        self.assertIn("Fixed camera latency", res["changelog"])

    @patch("urllib.request.urlopen")
    def test_check_for_updates_mock_up_to_date(self, mock_urlopen):
        fake_payload = {
            "tag_name": f"v{CURRENT_VERSION}",
            "name": f"Blink v{CURRENT_VERSION}",
            "body": "Current release",
            "assets": []
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(fake_payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = check_for_updates()
        self.assertEqual(res["status"], "success")
        self.assertFalse(res["has_update"])


if __name__ == "__main__":
    unittest.main()
