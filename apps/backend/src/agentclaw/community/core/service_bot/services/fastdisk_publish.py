"""Fastdisk publish source selection and versioned container-script invocation.

No credentials cross exec. The uploader only replaces the file transfer step;
MCP generation, Skills checks and publication state transitions remain unchanged.
"""
from __future__ import annotations

import base64
import json
import shlex
import uuid
import zlib
import time
import math
from .artifact_command import run_artifact_command
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Protocol

from agentclaw.community.core.workspace.engines.aicoding import _AICODING_RSYNC_EXCLUDES
from agentclaw.community.core.workspace.engines.claude_code import _CLAUDE_CODE_RSYNC_EXCLUDES
from agentclaw.community.core.workspace.engine_sandbox import EngineBuildPlan
from agentclaw.community.kernel.device_dto import CommandResult
from agentclaw.community.log import get_logger

logger = get_logger()


class PublishCommandExecutor(Protocol):
    def exec_shell_new(self, device_id: str, shell_cmd: str, *, timeout_seconds: int | None = None) -> CommandResult:
        """Execute in the bound source container; None preserves provider default."""
        ...


@dataclass(frozen=True)
class FastdiskPublishConfig:
    detect_timeout_seconds: int = 30
    upload_timeout_seconds: int | None = None
    request_timeout_seconds: int = 30
    mist_secret_name: str = ""
    endpoint: str = ""

    def __post_init__(self):
        for value in (self.detect_timeout_seconds, self.request_timeout_seconds):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError('fastdisk publish timeouts must be positive integers')
        if self.upload_timeout_seconds is not None and (type(self.upload_timeout_seconds) is not int or self.upload_timeout_seconds <= 0):
            raise ValueError('upload timeout must be positive or None')
        if not isinstance(self.mist_secret_name, str) or self.mist_secret_name != self.mist_secret_name.strip():
            raise ValueError('invalid deployment credential name')


class FastdiskPublishError(RuntimeError):
    pass


# Only the detector is embedded in Backend. Business transfer scripts are
# installed by the container scripts repository and are never sent as fallback.
ENTRY = '/home/admin/bin/service_publish.py'
DETECTION = Path(__file__).parents[3] / 'resources' / 'fastdisk_detection'


def detection_command() -> str:
    gate = (DETECTION / 'persist_mount_gate.py').read_text()
    # Avoid the CLI entrypoint; run the same gate function with runtime paths.
    gate = gate.split("if __name__ == '__main__':", 1)[0]
    layout = (DETECTION / 'runtime_layout.py').read_text()
    source = gate + '\n' + layout + '\n' + (
        'layout = runtime_layout()\n'
        'status = check_mounts(layout["storage"], layout["home"], '
        'layout["storage"] + "/.fastdisk.ready", "/proc/self/mountinfo")\n'
        'print(json.dumps(dict(status=status, **layout)))\n'
    )
    encoded = base64.b64encode(zlib.compress(source.encode())).decode()
    command = 'python3 -c ' + shlex.quote(
        f'import base64,zlib; exec(zlib.decompress(base64.b64decode({encoded!r})))'
    )
    if len(command.encode()) > 8192:
        raise FastdiskPublishError('embedded detector exceeds command size limit')
    return command


class FastdiskPublisher:
    def __init__(self, executor: PublishCommandExecutor, config: FastdiskPublishConfig,
                 *, bucket: str, endpoint: str, oss_mount_root: str):
        self.executor = executor
        self.config = config
        self.bucket = bucket
        endpoint = config.endpoint or endpoint
        self.endpoint = endpoint if not endpoint or '://' in endpoint else 'https://' + endpoint
        self.oss_mount_root = Path(oss_mount_root)

    def _exec(self, device_id: str, command: str, timeout: int) -> CommandResult:
        return self.executor.exec_shell_new(device_id=device_id, shell_cmd=command, timeout_seconds=timeout)

    @staticmethod
    def _layout(result: CommandResult) -> dict:
        if result.exit_code != 0:
            raise FastdiskPublishError('fastdisk detection failed')
        try:
            data = json.loads(result.stdout)
            if data['status'] not in ('run', 'skip_no_fastdisk'):
                raise ValueError('invalid gate result')
            for key in ('storage', 'home'):
                path = PurePosixPath(data[key])
                if not path.is_absolute() or '..' in path.parts:
                    raise ValueError('invalid runtime path')
        except (ValueError, TypeError, KeyError) as error:
            raise FastdiskPublishError('invalid fastdisk detection result') from error
        return data

    def detect(self, device_id: str, *, timeout_seconds: float | None = None) -> dict:
        budget = self.config.detect_timeout_seconds
        if timeout_seconds is not None:
            if timeout_seconds <= 0:
                raise FastdiskPublishError('fastdisk detection deadline exceeded')
            budget = min(budget, max(1, math.ceil(timeout_seconds)))
        try:
            return self._layout(self._exec(device_id, detection_command(), budget))
        except Exception as error:
            logger.error("fastdisk.detect.failed device_id=%s error_type=%s",
                         device_id, type(error).__name__)
            raise FastdiskPublishError('fastdisk detection failed; NAS fallback is forbidden') from error

    def try_publish(self, *, device_id: str, plan: EngineBuildPlan,
                    source_root: str, target_dir: Path, ignore_paths: tuple[str, ...],
                    normalize_extra: Any) -> bool:
        if plan.engine_type not in ('aicoding', 'claude_code'):
            return False
        layout = self.detect(device_id)
        if layout['status'] == 'skip_no_fastdisk':
            return False
        try:
            object_prefix = PurePosixPath('aidesktop') / target_dir.relative_to(self.oss_mount_root).as_posix()
        except ValueError as error:
            raise FastdiskPublishError('build target is outside the configured OSS mount') from error
        if not self.bucket or not self.endpoint:
            raise FastdiskPublishError('fastdisk publish requires object storage configuration')
        request = build_request(
            plan=plan, source_root=source_root, object_prefix=str(object_prefix),
            ignore_paths=ignore_paths, normalize_extra=normalize_extra,
            layout=layout, config=self.config, bucket=self.bucket, endpoint=self.endpoint,
        )
        self._invoke(device_id, 'upload', request, self.config.upload_timeout_seconds)
        return True

    def _invoke(self, device_id: str, action: str, request: dict, timeout: float | None) -> None:
        started = time.monotonic()
        # Inspect the version WITHOUT running upload. Old v1 scripts use the
        # old credential files and lack restore; they cannot accept this contract.
        probe_code = (
            "import ast,pathlib,sys; p=pathlib.Path(" + repr(ENTRY) + "); "
            "missing=not p.exists() and not p.is_symlink(); "
            "sys.exit(44) if missing else None; "
            "tree=ast.parse(p.read_text()); "
            "supported=any(isinstance(n,ast.Assign) and "
            "any(isinstance(t,ast.Name) and t.id=='SERVICE_PUBLISH_SCHEMA' for t in n.targets) "
            "and isinstance(n.value,ast.Constant) and n.value.value==5 for n in tree.body); "
            "sys.exit(0 if supported else 45)"
        )
        probe_budget = self.config.detect_timeout_seconds
        if timeout is not None:
            if timeout <= 0:
                raise FastdiskPublishError('artifact command deadline exceeded')
            probe_budget = min(probe_budget, max(1, math.ceil(timeout)))
        probe = self._exec(device_id, 'python3 -c ' + shlex.quote(probe_code), probe_budget)
        remaining = None if timeout is None else timeout - (time.monotonic() - started)
        if remaining is not None and remaining <= 0:
            raise FastdiskPublishError('artifact command deadline exceeded')
        request = dict(request, timeout_seconds=remaining)
        encoded = base64.b64encode(json.dumps(request).encode()).decode()
        if probe.exit_code != 0:
            reason = {44: 'container artifact script is missing',
                      45: 'container artifact script version is incompatible'}.get(
                          probe.exit_code, 'cannot probe container artifact script')
            logger.error("fastdisk.%s.failed device_id=%s reason=%s", action, device_id, reason)
            raise FastdiskPublishError(reason)
        command = f'python3 {shlex.quote(ENTRY)} {action} --request-b64 {shlex.quote(encoded)}'

        try:
            run_artifact_command(self.executor, device_id, command, timeout_seconds=remaining)
        except Exception as error:
            logger.error('fastdisk.%s.failed device_id=%s error_type=%s',
                         action, device_id, type(error).__name__)
            raise FastdiskPublishError(f'fastdisk {action} failed') from error

    def try_restore(self, *, device_id: str, plan: EngineBuildPlan,
                    source_root: str, artifact_path: str, artifact_dir: Path, remaining_timeout: Callable[[], float], normalize_extra: Any) -> bool:
        if plan.engine_type not in ('aicoding', 'claude_code'):
            return False
        layout = self.detect(device_id, timeout_seconds=remaining_timeout())
        if layout['status'] == 'skip_no_fastdisk':
            return False
        root = PurePosixPath(source_root)
        # Same identity/version-derived OSS prefix as publication, never inferred
        # from the container mount path or accepted as an arbitrary DB path.
        try:
            # artifact_path is the Bot-scoped container view; source identity is
            # supplied explicitly by the caller as the Backend artifact directory.
            relative = artifact_dir.relative_to(self.oss_mount_root)
        except ValueError as error:
            raise FastdiskPublishError('restore target is outside the configured OSS mount') from error
        if relative.name != plan.migration_subpath:
            raise FastdiskPublishError('restore artifact engine does not match plan')
        version_prefix = str(PurePosixPath('aidesktop') / relative.parent.as_posix())
        request = {
            'schema_version': 5, 'operation_id': uuid.uuid4().hex,
            'artifact_path': artifact_path, 'source_root': str(root),
            'version_prefix': version_prefix, 'engine_path': plan.migration_subpath,
            'bucket': self.bucket, 'endpoint': self.endpoint,
            'credential': {'provider': 'mist', 'secret_name': self.config.mist_secret_name, 'concat_env': True},
            'request_timeout_seconds': self.config.request_timeout_seconds,
            'fastdisk': {k: layout[k] for k in ('home', 'storage')},
            'additional_excludes': additional_excludes(plan),
            'chown': '1000:1000',
            'timeout_seconds': remaining_timeout(),
        }
        self._invoke(device_id, 'restore', request, request['timeout_seconds'])
        return True


def build_request(*, plan, source_root, object_prefix, ignore_paths, normalize_extra,
                  layout, config, bucket, endpoint):
    root = PurePosixPath(source_root)
    target = PurePosixPath(object_prefix)
    if target.name != plan.migration_subpath:
        raise FastdiskPublishError('artifact prefix does not match engine plan')
    return {
        'schema_version': 5, 'operation_id': uuid.uuid4().hex,
        'bucket': bucket, 'endpoint': endpoint,
        'credential': {'provider': 'mist', 'secret_name': config.mist_secret_name, 'concat_env': True},
        'version_prefix': str(target.parent), 'engine_path': target.name,
        'source_root': str(root), 'fastdisk': {k: layout[k] for k in ('home', 'storage')},
        'additional_excludes': additional_excludes(plan),
        'build_ignore_paths': list(ignore_paths),
        'timeout_seconds': config.upload_timeout_seconds,
        'request_timeout_seconds': config.request_timeout_seconds,
    }


def additional_excludes(plan):
    """Only Bot-specific additions; fixed file policy lives in the container."""
    defaults = (_AICODING_RSYNC_EXCLUDES if plan.engine_type == 'aicoding'
                else _CLAUDE_CODE_RSYNC_EXCLUDES)
    return [rule for rule in plan.rsync_excludes if rule not in defaults]
