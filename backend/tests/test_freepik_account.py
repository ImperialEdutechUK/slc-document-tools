import asyncio
import os
import threading
import unittest
from unittest.mock import patch

from app.services.freepik_account import (
    account_fallback_configured,
    configured_accounts,
    download_with_account,
    download_with_accounts,
)


class TestFreepikAccountConfiguration(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "FREEPIK_ACCOUNT_FALLBACK": "true",
            "FREEPIK_ACCOUNT_EMAIL": "legacy@example.com",
            "FREEPIK_ACCOUNT_PASSWORD": "legacy-secret",
            "FREEPIK_ACCOUNT_2_EMAIL": "second@example.com",
            "FREEPIK_ACCOUNT_2_PASSWORD": "second-secret",
        },
        clear=True,
    )
    def test_legacy_account_then_indexed_account_two(self):
        self.assertEqual(
            [
                ("legacy@example.com", "legacy-secret"),
                ("second@example.com", "second-secret"),
            ],
            configured_accounts(),
        )

    @patch.dict(
        os.environ,
        {
            "FREEPIK_ACCOUNT_FALLBACK": "true",
            "FREEPIK_ACCOUNT_1_EMAIL": "first@example.com",
            "FREEPIK_ACCOUNT_1_PASSWORD": "first-secret",
            "FREEPIK_ACCOUNT_2_EMAIL": "second@example.com",
            "FREEPIK_ACCOUNT_2_PASSWORD": "second-secret",
            "FREEPIK_ACCOUNT_EMAIL": "legacy@example.com",
            "FREEPIK_ACCOUNT_PASSWORD": "legacy-secret",
        },
        clear=True,
    )
    def test_explicit_indexed_accounts_are_tried_before_legacy(self):
        self.assertEqual(
            [
                ("first@example.com", "first-secret"),
                ("second@example.com", "second-secret"),
                ("legacy@example.com", "legacy-secret"),
            ],
            configured_accounts(),
        )

    @patch.dict(
        os.environ,
        {
            "FREEPIK_ACCOUNT_FALLBACK": "true",
            "FREEPIK_ACCOUNT_EMAIL": "same@example.com",
            "FREEPIK_ACCOUNT_PASSWORD": "same-secret",
            "FREEPIK_ACCOUNT_2_EMAIL": "same@example.com",
            "FREEPIK_ACCOUNT_2_PASSWORD": "same-secret",
        },
        clear=True,
    )
    def test_duplicate_accounts_are_ignored(self):
        self.assertEqual([("same@example.com", "same-secret")], configured_accounts())

    @patch.dict(os.environ, {"FREEPIK_ACCOUNT_FALLBACK": "false"}, clear=True)
    def test_disabled_fallback_has_no_configured_accounts(self):
        self.assertEqual([], configured_accounts())
        self.assertFalse(account_fallback_configured())

    @patch.dict(
        os.environ,
        {
            "FREEPIK_ACCOUNT_FALLBACK": "true",
            "FREEPIK_ACCOUNT_1_EMAIL": "first@example.com",
            "FREEPIK_ACCOUNT_1_PASSWORD": "first-secret",
            "FREEPIK_ACCOUNT_2_EMAIL": "second@example.com",
            "FREEPIK_ACCOUNT_2_PASSWORD": "second-secret",
        },
        clear=True,
    )
    @patch("app.services.freepik_account.download_with_account")
    def test_failed_account_moves_to_next_account(self, single_download):
        single_download.side_effect = [ValueError("login failed"), b"image-bytes"]
        self.assertEqual(b"image-bytes", download_with_accounts("https://www.freepik.com/example"))
        self.assertEqual(2, single_download.call_count)
        first = single_download.call_args_list[0].kwargs
        second = single_download.call_args_list[1].kwargs
        self.assertEqual("first@example.com", first["email"])
        self.assertEqual("second@example.com", second["email"])

    @patch("app.services.freepik_account._download_with_account_sync")
    def test_single_account_uses_worker_thread_inside_asyncio_loop(self, sync_download):
        caller_thread = threading.get_ident()

        def fake_download(resource_url, *, email, password):
            self.assertNotEqual(caller_thread, threading.get_ident())
            self.assertEqual("https://www.freepik.com/example", resource_url)
            self.assertEqual("first@example.com", email)
            self.assertEqual("secret", password)
            return b"image-bytes"

        sync_download.side_effect = fake_download

        async def run():
            return download_with_account(
                "https://www.freepik.com/example",
                email="first@example.com",
                password="secret",
            )

        self.assertEqual(b"image-bytes", asyncio.run(run()))



if __name__ == "__main__":
    unittest.main()
