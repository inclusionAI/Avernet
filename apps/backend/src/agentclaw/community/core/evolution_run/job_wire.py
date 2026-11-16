"""作业协议线格（06 §7）：与框架无关的负载编码。

服务端路由与 worker 客户端共用这一份编解码，避免两端各写一遍 base64。
这里不导入任何 HTTP 框架，保持在 core 的独立性之内。
"""

from __future__ import annotations

import base64

__all__ = ["decode_files", "encode_files"]


def encode_files(contents: dict[str, bytes]) -> dict[str, str]:
    """文件字节 → 可 JSON 传输的 base64 文本。"""

    return {path: base64.b64encode(data).decode() for path, data in contents.items()}


def decode_files(files: dict[str, str]) -> dict[str, bytes]:
    """base64 文本 → 文件字节。"""

    return {path: base64.b64decode(data) for path, data in files.items()}
