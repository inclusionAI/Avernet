import pytest

from agentclaw.community.core.task.task_runner.client.session_files.bcs_session_files_client_cli import (
    _decode_text_content,
)
from agentclaw.community.core.task.task_runner.client.session_files.bcs_session_files_client import (
    SessionFileContent,
)


def _got(content: bytes, file_name=None, content_type=None):
    return SessionFileContent(content=content, file_name=file_name, content_type=content_type)


# ===== read 文本直出判定:文本类型且能 utf-8 解码才直出,否则落盘 =====
def test_decode_text_md_by_extension_and_mime():
    assert _decode_text_content(_got("# 标题\n正文".encode(), "readme.md",
                                     "text/markdown; charset=utf-8")) == "# 标题\n正文"


def test_decode_text_txt_with_text_mime():
    assert _decode_text_content(_got(b"hello", "a.txt", "text/plain")) == "hello"


def test_decode_text_json_mime_without_extension():
    assert _decode_text_content(_got(b'{"k": 1}', "data", "application/json")) == '{"k": 1}'


def test_decode_text_xlsx_is_not_text():
    assert _decode_text_content(_got(b"PK\x03\x04garbage", "报告.xlsx",
                                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")) is None


def test_decode_text_binary_bytes_with_text_mime_rejected():
    """MIME 声称文本但字节解不开 utf-8 → 判二进制(宁落盘不刷屏)。"""
    assert _decode_text_content(_got(b"\xff\xfe\xfa binary", "bad.md", "text/markdown")) is None


def test_decode_text_octet_stream_unknown_ext_rejected():
    assert _decode_text_content(_got(b"plain ascii", "blob.bin", "application/octet-stream")) is None


def test_decode_text_unknown_mime_with_md_ext_decoded():
    """扩展名是文本白名单成员(BCS mime 是自由字符串,upload 端未传 mime 时常见)。"""
    assert _decode_text_content(_got("内容".encode(), "notes.md", "")) == "内容"


def test_decode_text_empty_text_file():
    assert _decode_text_content(_got(b"", "empty.txt", "text/plain")) == ""


def test_decode_text_none_metadata_never_text():
    assert _decode_text_content(_got(b"whatever", None, None)) is None