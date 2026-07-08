"""VM 内 skills-repo 同步模块（桌面版 agentbox 专用）。

职责：
  1. VM 启动时从 OSS 下载最新 skills-repo tar.gz 并解压。
  2. 启动后台定时同步线程，每隔固定周期重新拉取，保证 VM 运行期间
     skills 始终与 backend 最新打包保持一致。

运行环境识别：
  下载 / 后台同步只在 agentbox（桌面版）容器内运行，通过环境变量
  ``MAC_CONTAINER=true`` 识别。ARCA / 云端 bot 容器没有该变量，
  ``bootstrap_on_startup`` 和 ``start_background_sync`` 直接 no-op，
  不会拉 OSS、不会创建临时目录。

  但启动时的"清理残留临时目录"会**无差别执行**——ARCA 容器也会清理一次，
  用于清除历史发布残留的 ``.skills-repo-extract-*`` 目录。

元信息来源：
  通过环境变量 ``SKILLS_REPO_META_URL`` 显式配置元信息 JSON 地址；
  也可通过 ``SKILLS_REPO_META_URL_TEMPLATE`` 配置模板地址，并使用
  ``AGENTCLAW_ENV`` 填充 ``{env}``。内部 OCB 仓库可通过 GitHub 导出时
  排除的 ``engine.community.internal_defaults`` 提供兼容兜底；公开导出中该文件
  不存在，因此未配置时同步功能 no-op。

鲁棒性保证：
  - 网络抖动时自动重试，指数退避。
  - 原子替换：先解压到临时目录，再整体搬入目标位置。
  - 备份机制：替换前将现有 skills-repo 备份（仅保留最近 1 个，7 天过期）。
  - 启动时清理上次进程被 kill 残留的 ``.skills-repo-extract-*`` 临时目录。
  - 失败非致命：下载或解压失败只打日志，不阻断 engine。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tarfile
import tempfile
import threading
import time
from pathlib import Path

import requests

from engine.community.plugin_api.workspace_root import workspace_root

log = logging.getLogger("skills-repo-download")

# 开源版默认不内置任何内部 skills-repo 分发地址。
# 如需自动同步，部署侧显式设置 SKILLS_REPO_META_URL；如果希望保留
# 按环境拼接的内部行为，则由部署侧注入 SKILLS_REPO_META_URL_TEMPLATE，
# 例如 "https://example.com/skills-repo-meta-{env}.json"。内部 OCB 仓库
# 也可通过 engine.community.internal_defaults 提供兜底；该文件会从 GitHub export
# 物理排除。
META_URL_ENV = "SKILLS_REPO_META_URL"
META_URL_TEMPLATE_ENV = "SKILLS_REPO_META_URL_TEMPLATE"
AGENTCLAW_ENV_ENV = "AGENTCLAW_ENV"


def _get_internal_default_meta_url_template() -> str:
    """Return internal default template when present in the monorepo.

    ``engine.community.internal_defaults`` is excluded from the public GitHub export, so
    ImportError is the expected community behavior. The optional import keeps
    open-source code free of internal endpoints while preserving the historical
    internal default without requiring a deployment change.
    """
    try:
        from engine import internal_defaults
    except ImportError:
        return ""
    return (getattr(internal_defaults, "SKILLS_REPO_META_URL_TEMPLATE", "") or "").strip()


def _format_meta_url_template(template: str) -> str:
    env = (os.getenv(AGENTCLAW_ENV_ENV) or "dev").strip().lower() or "dev"
    try:
        return template.format(env=env).strip()
    except (KeyError, IndexError, ValueError) as exc:
        log.warning("Invalid skills-repo meta URL template %r: %s", template, exc)
        return ""


def _get_default_meta_url() -> str:
    """Return configured skills-repo metadata URL, or empty when disabled.

    Resolution order:
      1. ``SKILLS_REPO_META_URL``: full metadata URL.
      2. ``SKILLS_REPO_META_URL_TEMPLATE`` formatted with ``AGENTCLAW_ENV``
         as ``{env}``. The template itself is injected by deployment, so no
         internal OSS endpoint is embedded in open-source code.
      3. Optional ``engine.community.internal_defaults.SKILLS_REPO_META_URL_TEMPLATE``
         formatted with ``AGENTCLAW_ENV``. This monorepo-only compatibility shim
         is stripped from the public GitHub export.
      4. Empty string: sync disabled.
    """
    explicit = (os.getenv(META_URL_ENV) or "").strip()
    if explicit:
        return explicit

    template = (os.getenv(META_URL_TEMPLATE_ENV) or "").strip()
    if template:
        return _format_meta_url_template(template)

    template = _get_internal_default_meta_url_template()
    if template:
        return _format_meta_url_template(template)

    return ""


TARGET_DIR = workspace_root() / "skills" / "skills-repo"
BACKUP_DIR = workspace_root() / "skills" / ".skills-repo-backups"
ETAG_FILE = workspace_root() / "skills" / ".skills-repo-etag"
EXTRACT_TMP_PREFIX = ".skills-repo-extract-"
BACKUP_RETENTION_DAYS = 7
MAX_BACKUP_COUNT = 1
MAX_RETRIES = 3
BASE_DELAY_SECONDS = 2
SYNC_INTERVAL_SECONDS = 300  # 5 分钟同步一次

# agentbox 容器内置该环境变量；ARCA / 云端 bot 容器没有。
AGENTBOX_ENV_VAR = "MAC_CONTAINER"


def _is_agentbox_env() -> bool:
    """识别当前是否运行在 agentbox（桌面版）容器内。

    agentbox 镜像启动时会注入 ``MAC_CONTAINER=true``；ARCA 等线上容器没有
    该环境变量。只在 agentbox 内才需要拉取 skills-repo，避免线上容器产
    生临时目录与备份目录的小文件污染。
    """
    return os.getenv(AGENTBOX_ENV_VAR, "").strip().lower() in ("true", "1")


def _cleanup_stale_extract_dirs() -> None:
    """清理上次进程被 kill 残留的 ``.skills-repo-extract-*`` 目录。

    正常路径下 ``_extract_atomic`` 的 finally 会 rmtree 临时目录，但如果
    解压过程中进程被 OOM/重启/手动 kill，临时目录就会残留。启动时统一
    清理一次，避免线上观察到大量小文件。
    """
    parent = TARGET_DIR.parent
    if not parent.exists():
        return
    for item in parent.iterdir():
        if item.is_dir() and item.name.startswith(EXTRACT_TMP_PREFIX):
            try:
                shutil.rmtree(item)
                log.info("Cleaned up stale extract dir: %s", item)
            except OSError as exc:
                log.warning("Failed to remove stale extract dir %s: %s", item, exc)


def _cleanup_old_backups() -> None:
    """清理旧备份：超过 BACKUP_RETENTION_DAYS 天，或数量超过 MAX_BACKUP_COUNT 时删除最旧的。"""
    if not BACKUP_DIR.exists():
        return

    cutoff = time.time() - BACKUP_RETENTION_DAYS * 24 * 60 * 60
    backups: list[tuple[Path, float]] = []

    for item in BACKUP_DIR.iterdir():
        if item.is_dir() and item.name.startswith("skills-repo-"):
            try:
                mtime = item.stat().st_mtime
                if mtime < cutoff:
                    shutil.rmtree(item)
                    log.info("Cleaned up expired backup: %s", item)
                else:
                    backups.append((item, mtime))
            except OSError as exc:
                log.warning("Failed to inspect/remove old backup %s: %s", item, exc)

    # 如果数量仍超过上限，按 mtime 排序后删除最旧的
    if len(backups) > MAX_BACKUP_COUNT:
        backups.sort(key=lambda x: x[1])
        to_remove = len(backups) - MAX_BACKUP_COUNT
        for item, _ in backups[:to_remove]:
            try:
                shutil.rmtree(item)
                log.info("Cleaned up excess backup: %s", item)
            except OSError as exc:
                log.warning("Failed to remove excess backup %s: %s", item, exc)


def _backup_existing_repo() -> Path | None:
    """将现有 TARGET_DIR 移动到带时间戳的备份目录。

    成功时返回备份路径；如果没有内容需要备份则返回 None。
    """
    if not TARGET_DIR.exists() or not any(TARGET_DIR.iterdir()):
        return None

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_old_backups()

    timestamp = int(time.time())
    backup_path = BACKUP_DIR / f"skills-repo-{timestamp}"

    # 处理时间戳冲突（极罕见，但做安全兜底）
    suffix = 1
    original_backup_path = backup_path
    while backup_path.exists() and suffix <= 1000:
        backup_path = Path(f"{original_backup_path}-{suffix}")
        suffix += 1

    try:
        shutil.move(str(TARGET_DIR), str(backup_path))
        log.info("Backed up existing skills-repo to %s", backup_path)
        return backup_path
    except OSError as exc:
        log.warning("Failed to backup existing repo: %s", exc)
        return None


def _extract_atomic(tar_path: Path) -> bool:
    """将 tar.gz 解压到临时目录，再原子替换到 TARGET_DIR。

    临时目录创建在 TARGET_DIR 的同级路径下，确保 shutil.move 属于
    同文件系统 rename，真正做到原子替换，避免运行时进程读到中间状态。

    步骤：
      1. 在 TARGET_DIR 同级创建临时解压目录。
      2. 将 tar.gz 解压到该目录。
      3. 备份现有的 TARGET_DIR（如有）。
      4. 原子搬移：将临时目录整体 rename 为 TARGET_DIR。
    """
    # 临时目录放在 TARGET_DIR 同级，保证与目标目录同文件系统，
    # shutil.move 才能是原子 rename。
    TARGET_DIR.parent.mkdir(parents=True, exist_ok=True)
    extract_tmp = tempfile.mkdtemp(
        prefix=EXTRACT_TMP_PREFIX,
        dir=TARGET_DIR.parent,
    )
    try:
        with tarfile.open(tar_path, "r:gz") as tf:
            tf.extractall(path=extract_tmp)
    except Exception as exc:
        log.error("Tar extraction failed: %s", exc)
        shutil.rmtree(extract_tmp, ignore_errors=True)
        return False

    # 确定解压后的内容根目录。OSS 上传的 tar.gz 可能把所有内容包在
    # 一个顶层目录里（如 skills-repo/...）。如果只有一个顶层目录，
    # 就把它作为替换源，避免多套一层目录。
    top_items = list(Path(extract_tmp).iterdir())
    if len(top_items) == 1 and top_items[0].is_dir():
        source_dir = top_items[0]
    else:
        source_dir = Path(extract_tmp)

    # 替换前备份现有仓库
    _backup_existing_repo()

    try:
        # 如果备份失败导致 TARGET_DIR 仍存在，先删掉再搬入新内容
        if TARGET_DIR.exists():
            shutil.rmtree(TARGET_DIR)
        shutil.move(str(source_dir), str(TARGET_DIR))
        log.info("Atomically swapped skills-repo into %s", TARGET_DIR)
        return True
    except OSError as exc:
        log.error("Failed to swap extracted repo into place: %s", exc)
        return False
    finally:
        # 清理临时解压目录（如果我们从里面搬出了一个子目录，
        # 可能还残留一些文件）。
        shutil.rmtree(extract_tmp, ignore_errors=True)


def download_and_extract(url: str, timeout: int = 300) -> bool:
    """从 ``url`` 下载 tar.gz 并解压到 ``TARGET_DIR``，支持重试。

    Args:
        url: 带签名的 OSS 临时下载地址（HTTP GET）。
        timeout: 单次下载超时（秒）。

    Returns:
        成功返回 True，失败返回 False。
    """
    log.info("Downloading skills-repo from %s...", url[:120])

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as f:
            tmp_path = Path(f.name)

        # 针对网络抖动的重试循环
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = requests.get(url, timeout=timeout, stream=True)
                resp.raise_for_status()
                with tmp_path.open("wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                break  # 成功，跳出重试循环
            except requests.RequestException as exc:
                log.warning("Download attempt %d/%d failed: %s", attempt, MAX_RETRIES, exc)
                if attempt >= MAX_RETRIES:
                    log.error("All %d download attempts exhausted", MAX_RETRIES)
                    return False
                delay = BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                log.info("Retrying in %d seconds...", delay)
                time.sleep(delay)

        file_size = tmp_path.stat().st_size
        log.info("Downloaded %d bytes to %s", file_size, tmp_path)

        return _extract_atomic(tmp_path)

    except Exception as exc:
        log.error("Skills-repo download/extract failed: %s", exc)
        return False

    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _load_last_etag() -> str | None:
    """读取本地缓存的上一次成功下载的 ETag。"""
    if ETAG_FILE.exists():
        return ETAG_FILE.read_text().strip() or None
    return None


def _save_last_etag(etag: str) -> None:
    """将成功下载的 ETag 写入本地缓存文件。"""
    ETAG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ETAG_FILE.write_text(etag)


def _should_download(url: str, etag: str | None) -> bool:
    """比对 ETag，判断是否需要重新下载。

    如果 backend 未返回 etag，或本地没有缓存 etag，直接返回 True。
    优先本地比对：meta 中的 remote etag 与本地缓存一致时直接跳过下载，
    避免对仅支持 GET 的 presigned URL 发送 HEAD（可能返回 403）。
    本地不一致时才发 HEAD 请求并带上 ``If-None-Match``，若服务端返回
    304 则说明文件未变化，可跳过下载。

    HEAD 失败时降级为"需要下载"，避免漏同步。
    """
    if not TARGET_DIR.exists():
        log.info("TARGET_DIR does not exist, will download")
        return True
    if not etag:
        log.info("No remote ETag provided, will download")
        return True
    last_etag = _load_last_etag()
    if not last_etag:
        log.info("No local ETag cached, will download")
        return True
    log.info("ETag check: local=%s remote=%s", last_etag, etag)
    if last_etag == etag:
        log.info("ETags match locally, skipping download")
        return False
    try:
        resp = requests.head(url, timeout=30, headers={"If-None-Match": last_etag})
        if resp.status_code == 304:
            log.info("Skills-repo unchanged (ETag match), skipping download")
            return False
        log.info("ETag mismatch or no 304 (status=%d), will download", resp.status_code)
    except requests.RequestException as exc:
        log.warning("HEAD check failed: %s, falling back to download", exc)
    return True


def _get_meta_url() -> str:
    """获取元信息文件的下载 URL。

    URL 优先由部署侧通过 ``SKILLS_REPO_META_URL`` 或
    ``SKILLS_REPO_META_URL_TEMPLATE`` 显式配置；内部 OCB 仓库可通过
    GitHub 导出时排除的 ``engine.community.internal_defaults`` 提供兼容兜底。
    公开导出中未配置时返回空字符串，表示 skills-repo 自动同步禁用。
    """
    url = _get_default_meta_url()
    log.info("Using skills-repo meta URL: %s", url or "<disabled>")
    return url


def _fetch_meta_info() -> dict | None:
    """从固定的公开 URL 下载元信息 JSON。

    元信息格式示例：
      {"url": "https://oss.example.com/.../skills-repo.tar.gz",
       "etag": "\"abc123\"",
       "oss_path": "skills-repo/skills-repo.tar.gz",
       "available": true}

    返回解析后的 dict；任何网络/解析错误都返回 None 并打日志。
    """
    url = _get_meta_url()
    if not url:
        log.info("Skills-repo meta URL is not configured — sync disabled")
        return None

    try:
        log.info("Fetching meta info from %s", url)
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        log.info("Fetched meta info raw: %s", json.dumps(data, ensure_ascii=False))
        log.info(
            "Parsed meta fields: url=%s etag=%s available=%s oss_path=%s",
            data.get("url"),
            data.get("etag"),
            data.get("available"),
            data.get("oss_path"),
        )
        return data
    except requests.RequestException as exc:
        log.warning("Failed to fetch skills-repo meta info from %s: %s", url, exc)
    except ValueError as exc:
        log.warning("Failed to parse meta info JSON from %s: %s", url, exc)
    return None


def _get_download_info() -> tuple[str | None, str | None]:
    """解析元信息，返回 (tar.gz_url, etag) 二元组；任一为 None 时表示不可用。"""
    meta = _fetch_meta_info()
    if not meta:
        log.info("No meta info available — skipping download")
        return None, None

    available = meta.get("available")
    url = meta.get("url")
    etag = meta.get("etag")
    oss_path = meta.get("oss_path")

    log.info(
        "Parsed meta info: available=%s url=%s etag=%s oss_path=%s",
        available,
        url,
        etag,
        oss_path,
    )

    if not available:
        log.warning("Meta info indicates skills-repo is not available (available=%s)", available)
        return None, None

    if not url:
        log.warning("Meta info missing 'url' field")
        return None, None

    log.info("Resolved download URL: %s, ETag: %s", url[:120], etag)
    return url, etag


def _download_and_save(url: str, etag: str | None) -> bool:
    """下载并解压 skills-repo，成功后保存 ETag。"""
    ok = download_and_extract(url)
    if ok and etag:
        _save_last_etag(etag)
        log.info("Saved ETag: %s", etag)
    return ok


def bootstrap_on_startup() -> None:
    """VM 启动时的入口。

    无论 agentbox 还是 ARCA 容器，启动时都先清理一次残留的临时解压目录
    （兼容 ARCA 历史遗留 + agentbox 上次进程被 kill 的情况）。

    下载/同步逻辑只在 agentbox 容器内（``MAC_CONTAINER=true``）执行；
    ARCA / 云端 bot 容器在清理完后直接 no-op。

    从固定公开 URL 下载元信息 JSON，解析出 tar.gz 地址后下载并解压。
    下载失败非致命，engine 正常启动。
    """
    # 先清理残留临时目录：ARCA 也要执行，以清除历史遗留小文件。
    _cleanup_stale_extract_dirs()

    if not _is_agentbox_env():
        log.info(
            "Skills-repo bootstrap skipped: not running in agentbox "
            "(%s != true)",
            AGENTBOX_ENV_VAR,
        )
        return

    log.info("Skills-repo bootstrap starting...")
    url, etag = _get_download_info()
    if not url:
        log.info("No skills-repo URL available — skipping bootstrap download")
        return

    if not _should_download(url, etag):
        log.info("Skills-repo unchanged on startup, skipping download")
        return

    log.info("Skills-repo bootstrap: starting download")
    ok = _download_and_save(url, etag)
    if ok:
        log.info("Skills-repo bootstrap completed successfully")
    else:
        log.warning(
            "Skills-repo bootstrap failed. "
            "Engine will start without pre-populated skills."
        )


def start_background_sync(interval_seconds: int = SYNC_INTERVAL_SECONDS) -> None:
    """启动后台定时同步线程，每隔 interval_seconds 秒从 OSS 拉取更新。

    仅在 agentbox 容器内（``MAC_CONTAINER=true``）启动后台线程；ARCA /
    云端 bot 容器直接 no-op。

    backend 定时将最新 skills-repo 打包上传 OSS，VM 内通过本线程
    定时拉取并解压，确保运行期间 skills 始终与 backend 保持一致。

    使用守护线程，不影响 engine 主线程；异常被捕获，同步不会中断。
    """
    if not _is_agentbox_env():
        log.info(
            "Skills-repo background sync skipped: not running in agentbox "
            "(%s != true)",
            AGENTBOX_ENV_VAR,
        )
        return

    def _sync_loop() -> None:
        while True:
            log.info("Background sync: sleeping %d seconds", interval_seconds)
            time.sleep(interval_seconds)
            log.info("Background sync: wake up, checking for updates")
            url, etag = _get_download_info()
            if not url:
                log.info("Background sync: no URL available, skipping")
                continue
            if not _should_download(url, etag):
                log.info("Background sync: no changes (ETag match), skipping")
                continue
            log.info("Background sync: downloading latest skills-repo...")
            ok = _download_and_save(url, etag)
            if ok:
                log.info("Background sync: completed successfully")
            else:
                log.warning("Background sync: download failed, will retry next cycle")

    t = threading.Thread(target=_sync_loop, daemon=True)
    t.start()
    log.info(
        "Started background skills-repo sync (interval: %ds, meta_url: %s)",
        interval_seconds,
        _get_meta_url(),
    )
