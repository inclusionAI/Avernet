"""Unit tests for the Arkgw upload flow (签名 + HTTP 请求)。

直接测试签名逻辑和请求组装，mock 掉 requests.post 避免真实网络调用。
"""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad


BASE_URL = "https://devapi.teamclaw.com"
ACCESS_KEY = "dummy-access-key"
ACCESS_SECRET = "dummy-secret-16b"
TENANT = "alipay"

FILE_PATH = "/Users/wenpan/Desktop/发票报销/26512000002321987881_蚂蚁蓉信（成都）网络科技有限公司.pdf"
STAFF_ID = "100000"
SOURCE_ID = "agentCoding"


def do_sign(access_key: str, access_secret: str, timestamp: int) -> str:
    sign_str = f"accessKey={access_key}&timestamp={timestamp}"
    key = access_secret.encode("utf-8")
    cipher = AES.new(key, AES.MODE_ECB)
    encrypted_data = cipher.encrypt(pad(sign_str.encode("utf-8"), AES.block_size))
    return encrypted_data.hex().upper()


@pytest.mark.unit
class TestArkgwUploadSign:
    def test_do_sign_produces_hex_string(self):
        timestamp_ms = int(time.time() * 1000)
        signature = do_sign(ACCESS_KEY, ACCESS_SECRET, timestamp_ms)
        assert isinstance(signature, str)
        assert len(signature) > 0
        assert all(c in "0123456789ABCDEF" for c in signature)

    def test_do_sign_deterministic(self):
        ts = 1780983588000
        sig1 = do_sign(ACCESS_KEY, ACCESS_SECRET, ts)
        sig2 = do_sign(ACCESS_KEY, ACCESS_SECRET, ts)
        assert sig1 == sig2

    def test_do_sign_different_timestamp_different_result(self):
        sig1 = do_sign(ACCESS_KEY, ACCESS_SECRET, 1000000000000)
        sig2 = do_sign(ACCESS_KEY, ACCESS_SECRET, 2000000000000)
        assert sig1 != sig2


@pytest.mark.unit
class TestArkgwUploadRequest:
    @patch("requests.post")
    def test_upload_file_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "code": "ARK_RS_100000200",
            "message": "",
            "data": {
                "fileId": "202606090010003263459",
                "fileName": "test.pdf",
                "fileSize": "55.93KB",
                "fileSizeNum": 57269,
                "url": "https://antsys-ark-oss.cn-shanghai-alipay-office.oss-alipay.aliyuncs.com/test.pdf",
            },
            "traceId": "c4d32f158b6e3e549193461dcb076ae1",
        }
        mock_post.return_value = mock_response

        file_content = b"%PDF-1.4 fake content for testing"
        file_name = "test.pdf"

        url = f"{BASE_URL}/arkgw/openapi/fileManager/file/upload"
        timestamp_ms = int(time.time() * 1000)
        signature = do_sign(ACCESS_KEY, ACCESS_SECRET, timestamp_ms)

        import random
        import string
        trace_id = "".join(random.choices(string.hexdigits[:16], k=32))

        headers = {
            "AccessKey": ACCESS_KEY,
            "Signature": signature,
            "Timestamp": str(timestamp_ms),
            "ARK_OPENAPI_TENANT": TENANT,
            "ARK_OPENAPI_TRACE": trace_id,
        }

        form_data = {
            "staffId": STAFF_ID,
            "sourceId": SOURCE_ID,
        }

        files = {"file": (file_name, file_content, "application/pdf")}

        response = requests.post(
            url=url,
            data=form_data,
            files=files,
            headers=headers,
            timeout=60,
            verify=False,
        )

        result = response.json()
        assert result["success"] is True
        assert result["code"] == "ARK_RS_100000200"
        assert result["data"]["fileId"] == "202606090010003263459"
        assert result["data"]["fileName"] == "test.pdf"

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["data"]["staffId"] == STAFF_ID
        assert call_kwargs["data"]["sourceId"] == SOURCE_ID
        assert call_kwargs["url"] == url

    @patch("requests.post")
    def test_upload_file_failure(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": False,
            "code": "ARK_RS_520000500",
            "message": "无法识别的用户，staffId=000000",
            "data": None,
            "traceId": "7042d8b75f57d00dfaa6be68c237cf19",
        }
        mock_post.return_value = mock_response

        url = f"{BASE_URL}/arkgw/openapi/fileManager/file/upload"
        timestamp_ms = int(time.time() * 1000)
        signature = do_sign(ACCESS_KEY, ACCESS_SECRET, timestamp_ms)

        import random
        import string
        trace_id = "".join(random.choices(string.hexdigits[:16], k=32))

        headers = {
            "AccessKey": ACCESS_KEY,
            "Signature": signature,
            "Timestamp": str(timestamp_ms),
            "ARK_OPENAPI_TENANT": TENANT,
            "ARK_OPENAPI_TRACE": trace_id,
        }

        form_data = {"staffId": "000000", "sourceId": SOURCE_ID}
        files = {"file": ("test.pdf", b"content", "application/pdf")}

        response = requests.post(
            url=url,
            data=form_data,
            files=files,
            headers=headers,
            timeout=60,
            verify=False,
        )

        result = response.json()
        assert result["success"] is False
        assert result["code"] == "ARK_RS_520000500"
        assert "无法识别的用户" in result["message"]

    @patch("requests.post")
    def test_upload_url_transfer(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "code": "ARK_RS_100000200",
            "message": "",
            "data": {"fileId": "999", "fileName": "image.png"},
        }
        mock_post.return_value = mock_response

        url = f"{BASE_URL}/arkgw/openapi/fileManager/file/upload"
        timestamp_ms = int(time.time() * 1000)
        signature = do_sign(ACCESS_KEY, ACCESS_SECRET, timestamp_ms)

        import random
        import string
        trace_id = "".join(random.choices(string.hexdigits[:16], k=32))

        headers = {
            "AccessKey": ACCESS_KEY,
            "Signature": signature,
            "Timestamp": str(timestamp_ms),
            "ARK_OPENAPI_TENANT": TENANT,
            "ARK_OPENAPI_TRACE": trace_id,
        }

        form_data = {
            "staffId": STAFF_ID,
            "sourceId": SOURCE_ID,
            "url": "https://example.com/image.png",
        }

        response = requests.post(
            url=url,
            data=form_data,
            headers=headers,
            timeout=60,
            verify=False,
        )

        result = response.json()
        assert result["success"] is True
        assert result["data"]["fileId"] == "999"

        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["data"]["url"] == "https://example.com/image.png"
        assert "files" not in call_kwargs or call_kwargs.get("files") is None

    @patch("requests.post")
    def test_upload_network_error(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("Network unreachable")

        url = f"{BASE_URL}/arkgw/openapi/fileManager/file/upload"
        timestamp_ms = int(time.time() * 1000)
        signature = do_sign(ACCESS_KEY, ACCESS_SECRET, timestamp_ms)

        import random
        import string
        trace_id = "".join(random.choices(string.hexdigits[:16], k=32))

        headers = {
            "AccessKey": ACCESS_KEY,
            "Signature": signature,
            "Timestamp": str(timestamp_ms),
            "ARK_OPENAPI_TENANT": TENANT,
            "ARK_OPENAPI_TRACE": trace_id,
        }

        form_data = {"staffId": STAFF_ID, "sourceId": SOURCE_ID}
        files = {"file": ("test.pdf", b"content", "application/pdf")}

        with pytest.raises(requests.exceptions.ConnectionError):
            requests.post(
                url=url,
                data=form_data,
                files=files,
                headers=headers,
                timeout=60,
                verify=False,
            )
