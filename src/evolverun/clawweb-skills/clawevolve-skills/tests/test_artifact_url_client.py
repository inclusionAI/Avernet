import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PATH = Path(__file__).parents[1] / "clawevolve-workflow/scripts/handlers/lib_artifact_url_client.py"
SPEC = importlib.util.spec_from_file_location("artifact_url_client", PATH)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class ArtifactUrlClientTest(unittest.TestCase):
    def test_upload_declares_size_and_sha256(self):
        with tempfile.TemporaryDirectory() as root:
            artifact = Path(root) / "artifact.zip"
            artifact.write_bytes(b"immutable")
            ticket = {
                "method": "PUT", "url": "https://oss.test/signed", "headers": {},
                "artifact": {"kind": "pack", "ref": "oss://bucket/key"},
            }
            with mock.patch.object(MOD, "_json_request", return_value=ticket) as request, \
                 mock.patch.object(MOD, "_put_file"):
                MOD.ArtifactUrlClient("https://clawweb.test", "EV-1", "STEP-1").upload(
                    "snapshot-pack", artifact, "application/zip",
                )
            payload = request.call_args.args[1]
            self.assertEqual(payload["size"], len(b"immutable"))
            self.assertEqual(payload["sha256"], MOD.file_sha256(artifact))

    def test_connection_error_reports_host_without_signed_query(self):
        with tempfile.TemporaryDirectory() as root:
            artifact = Path(root) / "artifact.zip"
            artifact.write_bytes(b"data")
            connection = mock.Mock()
            connection.endheaders.side_effect = PermissionError(1, "Operation not permitted")
            with mock.patch.object(MOD.http.client, "HTTPSConnection", return_value=connection):
                with self.assertRaisesRegex(RuntimeError, r"host=oss\.test.*PermissionError") as raised:
                    MOD._put_file("https://oss.test/object?AccessKeyId=secret&Signature=secret", artifact, {})
            self.assertNotIn("AccessKeyId", str(raised.exception))
            self.assertNotIn("Signature", str(raised.exception))


if __name__ == "__main__": unittest.main()
