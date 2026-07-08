"""CodeFuse token 工具（可复用，无框架依赖）。

把原先散在 ``adapters/http/aicoding/router.py`` 的 CodeFuse auth_code 解码与
codefuse.json 写入命令构建逻辑下沉到 core 层，供：
- HTTP 端点 ``save_codefuse_token``（事后重授权）复用；
- device 层（arca / baas）在容器就绪初始化序列里写 codefuse.json 复用。

设计要点：
- 解码失败抛 ``ValueError``（不依赖 FastAPI 的 ``HTTPException``），由调用方按各自
  语境转换（HTTP 端点转 400，device 层让异常冒泡阻断启动）。
- ``build_codefuse_write_cmd`` 生成的 shell：base64 透传 JSON patch，merge 写入
  （已有值更新 token/workid/authType，其余字段保留），与容器 ``setup_cfuse.sh`` 的
  "非空保留" merge 行为兼容——先写好非空 token，setup_cfuse 不会覆盖。
"""
from __future__ import annotations

import base64
import json
import re


# CodeFuse 鉴权配置文件在容器内的路径（与 setup_cfuse.sh 一致）。
CODEFUSE_JSON_PATH = "/home/admin/.codefuse/fuse/codefuse.json"

# Hex token：至少 16 位十六进制。
_HEX_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{16,}$")


def decode_auth_code(auth_code: str) -> tuple[str, str]:
    """把 CodeFuse SSO 的 auth_code 解码成 ``(token, workid)``。

    auth_code 是 base64 字符串，解码后是 JSON ``{"t":"<token>","w":"<workid>"}``。

    Raises:
        ValueError: base64 / JSON 解析失败，或 token/workid 校验不通过。
    """
    try:
        raw = base64.b64decode(auth_code).decode("utf-8")
    except Exception as e:
        raise ValueError(f"invalid auth_code: base64 decode failed ({e})") from e

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid auth_code: JSON parse failed ({e})") from e

    if not isinstance(payload, dict):
        raise ValueError("invalid auth_code: expected JSON object")

    token = payload.get("t") or ""
    workid = payload.get("w") or ""

    if not token:
        raise ValueError("invalid auth_code: missing token (t)")
    if len(token) < 16:
        raise ValueError("invalid auth_code: token too short (<16)")
    if not _HEX_TOKEN_RE.match(token):
        raise ValueError("invalid auth_code: token must be hex")
    if not workid:
        raise ValueError("invalid auth_code: missing workid (w)")

    return token, workid


def _shell_quote(s: str) -> str:
    """单引号包裹，安全用于 shell 插值。"""
    return "'" + s.replace("'", "'\\''") + "'"


def build_codefuse_write_cmd(token: str, workid: str) -> str:
    """构建把 token/workid 写入 codefuse.json 的 shell 命令。

    命令行为：
      1. 读取已存在的 codefuse.json（不存在则空 dict）
      2. merge：强制更新 token/workid/authType，保留其余字段
      3. 写回
      4. 读取一次校验可读
    """
    # base64 透传 JSON patch，避免 shell 引号问题。
    patch = json.dumps({"token": token, "workid": workid, "authType": "OAUTH"})
    b64_patch = base64.b64encode(patch.encode()).decode()
    path = _shell_quote(CODEFUSE_JSON_PATH)

    return (
        f"mkdir -p /home/admin/.codefuse/fuse && "
        f'python3 -c "'
        f"import json,base64,os,sys; "
        f"p={path}; "
        f"patch=json.loads(base64.b64decode('{b64_patch}').decode()); "
        f"d=json.load(open(p)) if os.path.isfile(p) else {{}}; "
        f"d.update(patch); "
        f"json.dump(d,open(p,'w'),indent=2); "
        f"open(p).read()"  # verify readable
        f'"'
    )


def build_codefuse_write_cmd_from_auth_code(auth_code: str) -> str:
    """便捷封装：解码 auth_code 后直接产出写入命令。

    Raises:
        ValueError: auth_code 非法（见 :func:`decode_auth_code`）。
    """
    token, workid = decode_auth_code(auth_code)
    return build_codefuse_write_cmd(token, workid)
