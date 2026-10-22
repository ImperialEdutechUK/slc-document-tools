"""Regression tests for Railway bucket configuration and storage error handling."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError, EndpointConnectionError

from app.services.storage import Storage, StorageError, StorageNotFoundError


class StorageTests(unittest.TestCase):
    def test_local_read_write_and_delete(self):
        with tempfile.TemporaryDirectory() as tempdir:
            with patch.dict(os.environ, {"STORAGE_DIR": tempdir}, clear=True):
                service = Storage()
            self.assertIsNone(service.s3)
            service.put_bytes("jobs/example/file.pdf", b"%PDF", "application/pdf")
            self.assertEqual(b"%PDF", service.get_bytes("jobs/example/file.pdf"))
            service.delete_bytes("jobs/example/file.pdf")
            with self.assertRaises(StorageNotFoundError):
                service.get_bytes("jobs/example/file.pdf")

    @patch("app.services.storage.boto3.client")
    def test_official_railway_aws_variable_names_and_virtual_url_style(self, client):
        env = {
            "AWS_S3_BUCKET_NAME": "bucket-from-railway",
            "AWS_ENDPOINT_URL": "https://t3.storageapi.dev/",
            "AWS_ACCESS_KEY_ID": "access",
            "AWS_SECRET_ACCESS_KEY": "secret",
            "AWS_DEFAULT_REGION": "auto",
            "AWS_S3_URL_STYLE": "virtual",
        }
        with patch.dict(os.environ, env, clear=True):
            service = Storage()
        self.assertEqual("bucket-from-railway", service.bucket)
        self.assertEqual("virtual", service.addressing_style)
        _, kwargs = client.call_args
        self.assertEqual("https://t3.storageapi.dev", kwargs["endpoint_url"])
        self.assertEqual("auto", kwargs["region_name"])
        self.assertEqual("access", kwargs["aws_access_key_id"])
        self.assertEqual("secret", kwargs["aws_secret_access_key"])
        self.assertEqual("virtual", kwargs["config"].s3["addressing_style"])
        self.assertEqual("when_required", kwargs["config"].request_checksum_calculation)
        self.assertEqual("when_required", kwargs["config"].response_checksum_validation)

    @patch("app.services.storage.boto3.client")
    def test_legacy_s3_variables_support_path_style(self, client):
        env = {
            "S3_BUCKET": "older-bucket",
            "S3_ENDPOINT": "https://old.example.test",
            "S3_ACCESS_KEY_ID": "old-access",
            "S3_SECRET_ACCESS_KEY": "old-secret",
            "S3_REGION": "auto",
            "S3_ADDRESSING_STYLE": "path",
        }
        with patch.dict(os.environ, env, clear=True):
            service = Storage()
        self.assertEqual("path", service.addressing_style)
        self.assertEqual("path", client.call_args.kwargs["config"].s3["addressing_style"])

    @patch("app.services.storage.boto3.client")
    def test_rejects_invalid_addressing_style(self, client):
        with patch.dict(os.environ, {"S3_BUCKET": "test", "S3_ADDRESSING_STYLE": "incorrect"}, clear=True):
            with self.assertRaisesRegex(StorageError, "URL style"):
                Storage()
        client.assert_not_called()

    @patch("app.services.storage.boto3.client")
    def test_signature_mismatch_has_safe_actionable_error(self, client):
        client.return_value.put_object.side_effect = ClientError(
            {"Error": {"Code": "SignatureDoesNotMatch", "Message": "secret should not appear"}},
            "PutObject",
        )
        with patch.dict(os.environ, {"S3_BUCKET": "bucket", "S3_ACCESS_KEY_ID": "access"}, clear=True):
            service = Storage()
        with self.assertRaises(StorageError) as captured:
            service.put_bytes("jobs/a.pdf", b"pdf", "application/pdf")
        text = str(captured.exception)
        self.assertIn("SignatureDoesNotMatch", text)
        self.assertIn("Railway bucket Credentials", text)
        self.assertNotIn("secret should not appear", text)
        self.assertNotIn("access", text.lower().replace("access key", ""))

    @patch("app.services.storage.boto3.client")
    def test_s3_missing_file_distinguished_from_auth_error(self, client):
        with patch.dict(os.environ, {"S3_BUCKET": "bucket"}, clear=True):
            service = Storage()
        client.return_value.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "does not exist"}}, "GetObject"
        )
        with self.assertRaises(StorageNotFoundError):
            service.get_bytes("jobs/does-not-exist.docx")
        client.return_value.get_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "private"}}, "GetObject"
        )
        with self.assertRaises(StorageError) as captured:
            service.get_bytes("jobs/private.docx")
        self.assertNotIsInstance(captured.exception, StorageNotFoundError)

    @patch("app.services.storage.boto3.client")
    def test_network_failure_raises_storage_error(self, client):
        with patch.dict(os.environ, {"S3_BUCKET": "bucket"}, clear=True):
            service = Storage()
        client.return_value.put_object.side_effect = EndpointConnectionError(
            endpoint_url="https://storage.example.test"
        )
        with self.assertRaisesRegex(StorageError, "endpoint and network"):
            service.put_bytes("jobs/a.pdf", b"pdf", "application/pdf")


if __name__ == "__main__":
    unittest.main()
