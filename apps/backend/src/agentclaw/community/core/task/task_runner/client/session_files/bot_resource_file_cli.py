"""bot_resource_file_cli:读单 bot 工作区里某个资源文件(text preview)的 e2e 调试 CLI。

对接 agentclaw backend ``GET /api/resources/files/preview``
(file_router.py:preview_file——text-only、默认 1MB 上限,响应
``{"success", "data": {"content", "size"}, "message"}``)。

    python .../bot_resource_file_cli.py \\
        --ctoken w-fyUpHSLA_uEZ9D --bot-id 20260920_hzzaxnzf \\
        --path ai-news-2026-10-20.md --owner-id 35983 --user-id 35983

参数规约:
* ``--base-url``:backend 网络地址;缺省取 ``AGENTCLAW_BASE_URL`` 环境变量,再缺省
  ``http://127.0.0.1:8000``
* ``ctoken`` / ``user_id``:网关侧 opaque 凭证/路由参数——backend middleware 在鉴权
  之前剥掉 ctoken(middleware.py:_remove_compatibility_ctoken),业务身份走 cookie;
  所以这两项只在「经公网网关调用」时必带,本地直连后端可不传
* ``bot_id`` + ``owner_id``:目标 bot 及其 owner(collaborator 场景必传;bot 归属者
  可省 owner_id)
* ``--cookie-file``:办公网身份 Cookie 文件(整行作 Cookie 头),凭据不打日志
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path


def _bootstrap_import_path() -> None:
    """直跑脚本时把 apps/backend/src 挂进 sys.path(须在 agentclaw 导入之前)。"""
    if __package__:
        return  # 作为包模块导入(import path 已就绪)
    src_root = Path(__file__).resolve().parents[7]  # .../client/session_files → src
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))


_bootstrap_import_path()

import httpx  # noqa: E402

from agentclaw.community.core.task.task_runner.client.session_files.\
    bcs_session_files_client_cli import (  # noqa: E402
        _read_secret_file,
)


logger = logging.getLogger("bot_resource_file_cli")


class ResourceFileError(Exception):
    """preview 失败:非 200、或 200 但 success=false / 响应不合形。"""


async def fetch_preview(
    client: httpx.AsyncClient,
    path: str,
    *,
    bot_id: str,
    owner_id: str | None = None,
    ctoken: str | None = None,
    user_id: str | None = None,
    engine_type: str | None = None,
    max_size: int | None = None,
    publish_id: str | None = None,
    device_uuid: str | None = None,
    headers: dict[str, str] | None = None,
) -> str:
    """读 bot 工作区文件内容(text preview)→ 返回解码后的文本。

    ``ctoken``/``user_id`` 仅网关需要,backend 侧忽略/剥离;未传不下发。
    ``headers`` 原样并入(如 ``{"Cookie": ...}``)。
    """
    params: dict[str, str] = {"path": path, "bot_id": bot_id}
    for key, value in (("owner_id", owner_id), ("ctoken", ctoken), ("user_id", user_id),
                       ("engine_type", engine_type), ("max_size", max_size),
                       ("publish_id", publish_id), ("device_uuid", device_uuid)):
        if value is not None and value != "":
            params[key] = str(value)
    base = str(getattr(client, "base_url", "") or "")  # 日志只出 host,query 含 ctoken 不落
    logger.info("[bot_resource_file] >>> preview GET base=%s path=%s bot_id=%s params_keys=%s",
                base, path, bot_id, sorted(params))
    _t0 = time.monotonic()
    r = await client.request("GET", "/api/resources/files/preview",
                             params=params, headers=headers)
    logger.info("[bot_resource_file] <<< preview GET status=%s elapsed_ms=%s bytes=%s",
                r.status_code, int((time.monotonic() - _t0) * 1000), len(r.content))
    if r.status_code != 200:
        raise ResourceFileError(f"{r.status_code} {r.text[:500]}")
    try:
        data = r.json()
    except ValueError as ex:
        raise ResourceFileError(f"preview 响应非 JSON: {r.text[:500]}") from ex
    if not isinstance(data, dict) or not data.get("success"):
        message = data.get("message") if isinstance(data, dict) else None
        raise ResourceFileError(f"preview success=false: {message or r.text[:500]}")
    content = (data.get("data") or {}).get("content")
    if not isinstance(content, str):
        raise ResourceFileError(f"preview 响应缺 data.content: {r.text[:500]}")
    return content


def _pick_ctoken(args) -> str | None:
    """ctoken 取值序:命令行 > 凭据文件 > ``AGENTCLAW_CTOKEN`` 环境变量。"""
    if args.ctoken:
        return args.ctoken
    if args.ctoken_file:
        return _read_secret_file(args.ctoken_file)
    return os.environ.get("AGENTCLAW_CTOKEN") or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bot_resource_file_cli",
        description="读单 bot 工作区资源文件(GET /api/resources/files/preview)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--base-url", default=os.environ.get("AGENTCLAW_BASE_URL")
                        or "http://127.0.0.1:8000",
                        help="backend 地址(默认 $AGENTCLAW_BASE_URL)")
    parser.add_argument("--bot-id", required=True, help="目标 bot id")
    parser.add_argument("--path", required=True,
                        help="工作区相对路径(如 ai-news-2026-10-20.md)")
    parser.add_argument("--owner-id", default=None,
                        help="bot owner id(collaborator 场景必传)")
    parser.add_argument("--user-id", default=None, help="网关/展示用 user_id(可选)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--ctoken", help="网关 opaque ctoken(直传)")
    group.add_argument("--ctoken-file", help="ctoken 凭据文件路径(单行)")
    parser.add_argument("--cookie-file", help="办公网 Cookie 文件路径(整行作 Cookie 头)")
    parser.add_argument("--header", action="append", default=None,
                        metavar="K:V", help="附加请求头(可重复,如 'X-A:1')")
    parser.add_argument("--engine-type", default=None, help="引擎覆盖(默认 bot 活跃引擎)")
    parser.add_argument("--max-size", type=int, default=None,
                        help="preview 大小上限(服务端默认 1MB)")
    parser.add_argument("--publish-id", default=None, help="读发布实例(publish bot 场景)")
    parser.add_argument("--device-uuid", default=None, help="多实例定向(缺省→活跃实例)")
    parser.add_argument("--out", default=None,
                        help="落盘路径(缺省正文直出 stdout——preview 本身就是文本)")
    parser.add_argument("--quiet", action="store_true", help="只打印结果,掩盖请求日志")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO,
                        format="%(levelname)s %(message)s")
    headers: dict[str, str] = {}
    if args.cookie_file:
        headers["Cookie"] = _read_secret_file(args.cookie_file)
    for raw in args.header or []:
        if ":" not in raw:
            print(f"--header 参数需 K:V 形态: {raw!r}", file=sys.stderr)
            return 2
        k, v = raw.split(":", 1)
        headers[k.strip()] = v.strip()

    async def _go() -> str:
        async with httpx.AsyncClient(base_url=args.base_url.rstrip("/"),
                                     timeout=30.0) as client:
            return await fetch_preview(
                client, args.path,
                bot_id=args.bot_id, owner_id=args.owner_id, user_id=args.user_id,
                engine_type=args.engine_type, max_size=args.max_size,
                publish_id=args.publish_id, device_uuid=args.device_uuid,
                headers=headers, ctoken=_pick_ctoken(args),
            )

    try:
        content = asyncio.run(_go())
    except Exception as ex:  # noqa: BLE001 — CLI 边界统一落错误码
        print(f"失败: {type(ex).__name__}: {ex}", file=sys.stderr)
        return 1
    if args.out:
        Path(args.out).expanduser().write_text(content, encoding="utf-8")
        print(f"bytes={len(content.encode('utf-8'))} → {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(content)
        if not content.endswith("\n"):
            sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())