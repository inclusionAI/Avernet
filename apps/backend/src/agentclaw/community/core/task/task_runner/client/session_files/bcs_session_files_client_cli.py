"""bcs_session_files_client_cli:本地 e2e 测试用命令行,驱动 BcsSessionFilesClient。

直接跑(脚手架自举 sys.path,无需安装包):

    python apps/backend/src/agentclaw/community/core/task/task_runner/client/\
session_files/bcs_session_files_client_cli.py \\
        list --base-url https://bcs-pre.alipay.com --session-id s1 \\
        --bot-token-file ~/.bcs/drv.token [--prefix 报告] [--status Ready]
    python .../bcs_session_files_client_cli.py \\
        read --base-url ... --session-id s1 --file-id f_abc123 --out ./报告.xlsx

参数规约:
* ``--base-url``:BCS 网络地址;缺省取 ``BCS_BASE_URL`` 环境变量,再缺省
  ``http://127.0.0.1:21000``(本地 BCS 默认端口)
* 身份三选一(可叠加):``--bot-token`` / ``--bot-token-file``(持有者 bot 的
  session_token → ``Authorization: Bearer``,过参与者级 ACL);``--cookie-file``
  (办公网人身份 Cookie,文件内一行字符串 → ``Cookie:`` 头);HMAC 服务签名
  ``--hmac-token/--hmac-secret``(或 ``BCS_HMAC_TOKEN/BCS_HMAC_SECRET``,网关
  需要服务签名时传,缺省占位值照签)
* cookie/token 文件内容原样使用(去尾部换行),均不打日志
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path


def _bootstrap_import_path() -> None:
    """直跑脚本时把 apps/backend/src 挂进 sys.path(目录层级变更需同步此注释)。"""
    if __package__:
        return  # 作为包模块导入(import path 已就绪)
    src_root = Path(__file__).resolve().parents[7]  # .../client/session_files → src
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))


_bootstrap_import_path()

from agentclaw.community.core.task.task_runner.client.bcs_token_provider import (  # noqa: E402
    BcsTokenProvider,
)
from agentclaw.community.core.task.task_runner.client.session_files.bcs_session_files_client import (  # noqa: E402
    BcsSessionFilesClient,
)


logger = logging.getLogger("bcs_session_files_cli")


def _read_secret_file(path: str) -> str:
    """读凭据文件(单行,去首尾空白);路径不存在/为空 → 提示后退出(凭据问题别静默)。"""
    p = Path(path).expanduser()
    try:
        content = p.read_text(encoding="utf-8")
    except OSError as ex:
        raise SystemExit(f"凭据文件读取失败 {path}: {ex}") from ex
    value = content.strip()
    if not value:
        raise SystemExit(f"凭据文件为空: {path}")
    return value


def _pick(name: str, *, flag: str | None, file_flag: str | None, env: str) -> str:
    """取值序:命令行参数 > 凭据文件 > 环境变量;都没有则空串。"""
    if flag:
        return flag
    if file_flag:
        return _read_secret_file(file_flag)
    return os.environ.get(env, "")


class _CliBcsToken(BcsTokenProvider):
    """HMAC 占位 provider:凭据未配置时用空占位照签(带不动 HDC 的部署可无视)。"""

    def __init__(self, token: str, secret: str, base_url: str) -> None:
        self._token = token
        self._secret = secret
        self._base_url = base_url

    @property
    def token(self) -> str:
        return self._token

    @property
    def secret(self) -> str:
        return self._secret

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def task_callback_url(self) -> str:
        return ""


def _connect_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", default=os.environ.get("BCS_BASE_URL")
                        or "http://127.0.0.1:21000", help="BCS 服务地址(默认 $BCS_BASE_URL "
                        "或 http://127.0.0.1:21000)")
    parser.add_argument("--session-id", required=True, help="BCS 会话 id(群/服务会话)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--bot-token", help="持有者 bot 的 session_token(直传)")
    group.add_argument("--bot-token-file", help="bot token 凭据文件路径(单行)")
    parser.add_argument("--cookie-file", help="办公网人身份 Cookie 文件路径"
                        "(文件内整行作 Cookie 头)")
    parser.add_argument("--hmac-token", default=None, help="HMAC X-ECB-Token(默认 "
                        "$BCS_HMAC_TOKEN)")
    parser.add_argument("--hmac-secret", default=None, help="HMAC 签名密钥(默认 "
                        "$BCS_HMAC_SECRET)")
    parser.add_argument("--quiet", action="store_true", help="只打印结果,掩盖请求日志")


def _identity(args) -> tuple[str, dict[str, str] | None]:
    """组装 caller 身份:Bearer(bot)与 Cookie(人)可同时携带;两者皆缺 → 提醒。"""
    bearer = _pick("bot_token", flag=args.bot_token, file_flag=args.bot_token_file,
                   env="BCS_BOT_TOKEN")
    cookie = ""
    if args.cookie_file:
        cookie = _read_secret_file(args.cookie_file)
    extra: dict[str, str] | None = None
    if cookie:
        extra = {"Cookie": cookie}
    if not bearer and not cookie:
        print("提示:未传 --bot-token/--bot-token-file/--cookie-file → 仅 HMAC 匿名,"
              "参与者级 ACL 预计 401/403", file=sys.stderr)
    return bearer, extra


async def _cmd_list(client: BcsSessionFilesClient, args) -> int:
    bearer, extra = _identity(args)
    items = await client.list_files(
        args.session_id,
        prefix=args.prefix, status=args.status, limit=args.limit, offset=args.offset,
        caller_bearer=bearer or None, extra_headers=extra,
    )
    print(f"共 {len(items)} 个文件:")
    for it in items:
        print(f"  [{it.get('status')}] {it.get('file_id')}  {it.get('size')}B  "
              f"{it.get('file_name')}  ({it.get('mime_type')})")
    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
    else:
        for it in items:
            print(f'read 用法示例: ... read --session-id {args.session_id} '
                  f'--file-id {it.get("file_id")}')
    return 0 if items else 3  # e2e 脚本可按返回码断言"拉到列表"


async def _cmd_read(client: BcsSessionFilesClient, args) -> int:
    bearer, extra = _identity(args)
    got = await client.download_file(
        args.session_id, args.file_id,
        show=args.show, caller_bearer=bearer or None, extra_headers=extra,
    )
    out = Path(args.out or got.file_name or f"{args.file_id}.bin").expanduser()
    out.write_bytes(got.content)
    print(f"status=200 bytes={len(got.content)} file_name={got.file_name!r} "
          f"content_type={got.content_type!r} → {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bcs_session_files_client_cli",
        description="BCS 会话共享文件 e2e 调试 CLI(list / read)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="GET /sessions/{sid}/files 列文件")
    _connect_args(p_list)
    p_list.add_argument("--prefix", default=None, help="文件名前缀过滤")
    p_list.add_argument("--status", default=None,
                        choices=["Pending", "Ready", "Deleting", "Failed"],
                        help="状态过滤")
    p_list.add_argument("--limit", type=int, default=None, help="分页(BCS 默认 100)")
    p_list.add_argument("--offset", type=int, default=None, help="分页(BCS 默认 0)")
    p_list.add_argument("--json", action="store_true", help="附带输出原始 items JSON")
    p_list.set_defaults(func=_cmd_list)

    p_read = sub.add_parser("read", help="GET /sessions/{sid}/files/{fid}/content 下载")
    _connect_args(p_read)
    p_read.add_argument("--file-id", required=True, help="list 拿到的 file_id")
    p_read.add_argument("--out", default=None,
                        help="落盘路径(默认响应 Content-Disposition 文件名/临时名)")
    p_read.add_argument("--show", action="store_true",
                        help="BCS 以 inline 输出(语义透传)")
    p_read.set_defaults(func=_cmd_read)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    hmac_token = args.hmac_token if args.hmac_token is not None \
        else os.environ.get("BCS_HMAC_TOKEN", "")
    hmac_secret = args.hmac_secret if args.hmac_secret is not None \
        else os.environ.get("BCS_HMAC_SECRET", "")
    client = BcsSessionFilesClient(_CliBcsToken(hmac_token, hmac_secret, args.base_url))
    try:
        return asyncio.run(args.func(client, args))
    except Exception as ex:  # noqa: BLE001 — CLI 边界统一落错误码
        print(f"失败: {type(ex).__name__}: {ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())