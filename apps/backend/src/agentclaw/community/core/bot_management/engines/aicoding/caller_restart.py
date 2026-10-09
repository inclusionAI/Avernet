"""Coding Caller admission and durable backup/upgrade, not BaaS policy.

Caller-instance writes are serialized with the existing restart-lock repository.
No table or repository changes; only this policy uses the namespaced lock.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from copy import deepcopy
import hashlib
import json
import time
import uuid
from typing import TYPE_CHECKING, Callable

from agentclaw.community.core.task_queue.types import Complete, Fail, Reschedule, TERMINAL_STATUSES
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope, get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.bot_management.engines.provisioning import (
    BotProvisioningContext, CallerConnectionLifecycle, instance_restart_policy,
)
from agentclaw.community.log import get_logger
from .restart_task import _failure_message
from .restart_state import BACKUP_TIMEOUT, BUSINESS_TIMEOUT, TASK_DEADLINE, supports
from .restart_backup import caller_backup_deadline, RestartBackupError

if TYPE_CHECKING:
    from agentclaw.community.core.task_queue.services.registry import HandlerRegistry

logger = get_logger()

KEY = 'coding_caller_restart'
TASK_TYPE = 'aicoding.caller.restart'
ACTIVE = {'QUEUED', 'EXECUTING', 'SUBMITTING', 'WAITING_READY'}


@dataclass(frozen=True)
class CallerRestartSubmission:
    """One worker invocation's validation, never mutable strategy-singleton state."""

    bot_id: str
    owner_id: str
    engine: str
    device_id: str
    validate_and_mark: Callable[[], None]

    def check_context(self, ctx: BotProvisioningContext, device_id: str | None) -> None:
        if (ctx.bot_id != self.bot_id or ctx.owner_id != self.owner_id
                or ctx.active_engine != self.engine or device_id != self.device_id):
            raise RuntimeError('Caller restart engine or target changed')


current_caller_submission: ContextVar[CallerRestartSubmission | None] = ContextVar(
    'current_caller_submission', default=None,
)


def _key(ids: dict) -> str:
    return hashlib.sha256(json.dumps([
        get_current_avernet_tenant(), ids['owner_id'], ids['bot_id'], ids['user_id'],
    ]).encode()).hexdigest()


def _journal(instance: dict) -> dict:
    return (instance.get('ext') or {}).get(KEY) or {}


def _response(instance: dict) -> dict:
    return {'instance': instance, 'connection': None, 'need_poll': instance['status'] != 'failed'}


class CallerRestartState:
    def __init__(self, service: CallerConnectionLifecycle, ids: dict):
        self.service, self.ids = service, ids
        self.lock_args = dict(env=get_current_env(), entity_id='coding-caller', bot_id=_key(ids))

    def read(self) -> dict:
        instance = self.service._instance_repo.get_instance(**self.ids)
        if instance is None:
            raise RuntimeError('Caller instance disappeared')
        return instance

    def save(self, instance: dict, *, status: str | None = None, **changes) -> dict:
        ext = deepcopy(instance.get('ext') or {})
        ext[KEY] = {**_journal(instance), **changes}
        if changes.get('phase') == 'QUEUED':
            ext.pop('error', None)
        if changes.get('phase') == 'FAILED':
            ext['error'] = {'message': changes['message']}
            status = 'failed'
        if not self.service._instance_repo.update_instance(**self.ids, status=status, ext=ext):
            actual = self.read()
            if actual.get('ext') != ext or (status is not None and actual.get('status') != status):
                raise RuntimeError('Caller restart state write failed')
        return self.read()

    @contextmanager
    def locked(self):
        locks = self.service._restart_locks
        stale = locks.get_if_stale(**self.lock_args, ttl_seconds=BUSINESS_TIMEOUT)
        if stale is not None:
            locks.release(**self.lock_args, lock_token=stale.lock_token)
        lock = locks.acquire(**self.lock_args, holder_user_id=self.ids['user_id'])
        try:
            yield lock
        finally:
            if lock is not None:
                locks.release(**self.lock_args, lock_token=lock.lock_token)

    def owns(self, lock, operation_id: str) -> bool:
        current = self.service._restart_locks.get(**self.lock_args)
        return (current is not None and current.lock_token == lock.lock_token
                and _journal(self.read()).get('operation_id') == operation_id)


class AicodingCallerRestartMixin:
    async def prepare_restart_async(self, ctx: BotProvisioningContext, **kwargs):
        submission = current_caller_submission.get()
        if submission is not None:
            submission.check_context(ctx, kwargs.get('device_id'))
        await super().prepare_restart_async(ctx, **kwargs)
        if submission is not None:
            # Includes legacy/not_mounted: skipping backup is not permission to
            # skip task ownership, target, deadline or submission-state checks.
            submission.validate_and_mark()

    async def execute_caller_connection(self, ctx, *, service: CallerConnectionLifecycle, **kwargs):
        ids = {key: kwargs[key] for key in ('user_id', 'bot_id', 'owner_id')}
        state = CallerRestartState(service, ids)
        # First allocation is unchanged. Only existing Caller upgrades are deferred.
        if not (kwargs['instance'].get('ext') or {}).get('bot_uuid'):
            return await service._continue_caller_connection(**kwargs)
        with state.locked() as lock:
            instance = state.read()
            if lock is None:
                return _response({**instance, 'status': 'init'})
            record = _journal(instance)
            force = kwargs['force_upgrade']
            if record.get('phase') in ACTIVE:
                if record['phase'] != 'WAITING_READY':
                    return _response(instance)
                # The worker has persisted the new publish id; use the old polling,
                # caller-token exchange and connection generation under the same lock.
                if instance['status'] == 'failed':
                    state.save(instance, phase='FAILED', message='Caller 容器部署失败')
                    return _response(state.read())
                try:
                    # Identity exchange must refer to the artifact actually
                    # deployed, even if the owner has since published a new one.
                    publish = kwargs['publish_record']
                    if publish.id != record['publish_record_id']:
                        publish = service._publish_repo.get_by_id(record['publish_record_id'])
                        if publish is None:
                            raise RuntimeError('Caller publish record disappeared')
                    result = await service._continue_caller_connection(
                        **{**kwargs, 'instance': instance, 'publish_record': publish,
                           'force_upgrade': False},
                    )
                except Exception:
                    state.save(state.read(), phase='FAILED', message='Caller 部署进度或身份初始化失败')
                    raise
                current = state.read()
                if current['status'] in {'success', 'failed'}:
                    state.save(current, phase='SUCCEEDED' if current['status'] == 'success' else 'FAILED',
                               message='Caller 容器部署失败' if current['status'] == 'failed' else '')
                    result['instance'] = state.read()
                    result['need_poll'] = False
                return result
            if record.get('phase') == 'FAILED' and not force:
                return _response(instance)
            ext = instance.get('ext') or {}
            version = kwargs['publish_record'].version or 1
            if not force and (
                (instance['status'] == 'success' and version <= (ext.get('version') or 0))
                or (instance['status'] == 'init' and ext.get('baas_publish_id'))
            ):
                return await service._continue_caller_connection(**{**kwargs, 'instance': instance})
            queue = service._task_queue
            existing = queue.find_by_idempotency_key(TASK_TYPE, _key(ids))
            if existing is not None and existing.status not in TERMINAL_STATUSES:
                return _response(instance)
            payload = {**ids, 'tenant': get_current_avernet_tenant(),
                       'operation_id': uuid.uuid4().hex, 'started_at': time.time(),
                       'bot_uuid': ext['bot_uuid'], 'publish_record_id': kwargs['publish_record'].id,
                       'engine': ctx.active_engine}
            # One init write, before enqueue. Worker never re-initializes status.
            try:
                state.save(instance, status='init', phase='QUEUED', **payload)
                queue.enqueue(TASK_TYPE, payload, deadline_seconds=TASK_DEADLINE,
                              idempotency_key=_key(ids))
            except Exception:
                state.save(state.read(), phase='FAILED', message='Caller 重启初始化或任务提交失败')
                raise
            return _response(state.read())


class AicodingCallerRestartHandler:
    task_type = TASK_TYPE

    def __init__(self, service_provider: Callable[[], CallerConnectionLifecycle]):
        self.service_provider = service_provider

    def handle(self, payload):
        if not isinstance(payload, dict) or not all(payload.get(k) for k in (
            'tenant', 'user_id', 'bot_id', 'owner_id', 'operation_id', 'bot_uuid', 'started_at',
        )):
            return Fail('Invalid Caller restart payload')
        with avernet_tenant_scope(payload['tenant']):
            return asyncio.run(self._execute(payload))

    async def _execute(self, payload):
        service = self.service_provider()
        state = CallerRestartState(service, {k: payload[k] for k in ('user_id', 'bot_id', 'owner_id')})
        with state.locked() as lock:
            if lock is None:
                return Reschedule(3)
            instance = state.read()
            record = _journal(instance)
            operation = payload['operation_id']
            if record.get('operation_id') != operation:
                return Complete()
            if record.get('phase') == 'FAILED':
                return Fail(record.get('message') or 'Caller restart failed')
            if record.get('phase') == 'SUCCEEDED':
                return Complete()
            if time.time() - payload['started_at'] >= BUSINESS_TIMEOUT:
                state.save(instance, phase='FAILED', message='Caller 重启等待超时，不会自动重复提交')
                return Fail('Caller restart timed out')
            if record.get('phase') in {'EXECUTING', 'SUBMITTING'}:
                # Lost worker/response: never replay a potentially submitted upgrade.
                return Reschedule(3)
            try:
                if record.get('phase') == 'WAITING_READY':
                    progress = service._baas.get_publish_progress(
                        publish_id=int(instance['ext']['baas_publish_id']), include_devices=False,
                    )
                    if progress.get('status') in {'FAILED', 'REJECTED', 'REVOKED'}:
                        state.save(instance, phase='FAILED', message='Caller 容器部署失败，请检查部署日志')
                        return Fail('Caller deployment failed')
                    # HTTP polling still owns caller identity exchange. Successful
                    # deployment needs no more background observation.
                    return Complete() if progress.get('status') == 'SUCCESS' else Reschedule(3)
                bot = service._load_service_bot(payload['bot_id'], payload['owner_id'])
                if not supports(bot) or bot.get('active_engine') != payload['engine']:
                    raise RuntimeError('Caller engine changed')
                if instance['ext'].get('bot_uuid') != payload['bot_uuid']:
                    raise RuntimeError('Caller target changed')
                publish, migration = service._resolve_build_artifact(payload['bot_id'], payload['owner_id'])
                if publish.id != payload['publish_record_id']:
                    raise RuntimeError('Published artifact changed; restart again explicitly')
                image = service._resolve_publish_image_pin(
                    publish, bot_id=payload['bot_id'], owner_id=payload['owner_id'],
                )
                state.save(instance, phase='EXECUTING')

                def before_submit():
                    if not state.owns(lock, operation):
                        raise RuntimeError('Caller restart ownership changed')
                    if time.time() - payload['started_at'] >= BUSINESS_TIMEOUT:
                        raise RuntimeError('Caller restart deadline exceeded')
                    current_bot = service._load_service_bot(payload['bot_id'], payload['owner_id'])
                    if current_bot.get('active_engine') != payload['engine']:
                        raise RuntimeError('Caller engine changed')
                    current = state.read()
                    if current['ext'].get('bot_uuid') != payload['bot_uuid']:
                        raise RuntimeError('Caller target changed')
                    state.save(current, phase='SUBMITTING')

                # Pin the original policy, not the mutable source Bot's engine.
                # The registry only dispatches; all validation stays in this mixin.
                from ..registry import resolve_restart_strategy

                _, policy = resolve_restart_strategy(bot)
                policy_reset = instance_restart_policy.set(policy)
                submission_reset = current_caller_submission.set(CallerRestartSubmission(
                    bot_id=payload['bot_id'], owner_id=payload['owner_id'],
                    engine=payload['engine'], device_id=payload['bot_uuid'],
                    validate_and_mark=before_submit,
                ))
                reset = caller_backup_deadline.set(payload['started_at'] + BACKUP_TIMEOUT)
                try:
                    result = await service._upgrade_container(
                        bot_uuid=payload['bot_uuid'], bot_id=payload['bot_id'], owner_id=payload['owner_id'],
                        migration_path=migration, version=publish.version or 1,
                        docker_image=image.docker_image, publish_ext=publish.ext or {},
                    )
                finally:
                    caller_backup_deadline.reset(reset)
                    current_caller_submission.reset(submission_reset)
                    instance_restart_policy.reset(policy_reset)
                if not state.owns(lock, operation):
                    return Complete()
                if not result.get('publish_id'):
                    raise RuntimeError('BaaS did not return a publish id')
                current = state.read()
                current['ext'] = {**current['ext'], 'baas_publish_id': result['publish_id'],
                                  'service_bot_publish_id': publish.id, 'version': publish.version or 1}
                current['ext'].pop('error', None)
                current['ext'].pop('baas_publish', None)
                state.save(current, phase='WAITING_READY')
                return Reschedule(3)
            except Exception as error:
                if not state.owns(lock, operation):
                    return Complete()
                # Persist the established error field, not a new status endpoint.
                # Avoid putting command bodies/tokens from provider exceptions in ext.
                phase = _journal(state.read()).get('phase')
                if phase == 'WAITING_READY':
                    # A failed progress read is not a failed deployment. Queue
                    # retry repeats observation only; never backup or upgrade.
                    raise
                message = ('Caller 重启提交失败，请检查部署状态；不会自动重试'
                           if phase == 'SUBMITTING' else 'Caller 备份或重启准备失败，旧容器未执行替换')
                cause = error
                seen = set()
                while cause is not None and id(cause) not in seen:
                    seen.add(id(cause))
                    if isinstance(cause, RestartBackupError):
                        message = _failure_message(cause, fenced=False)
                        break
                    cause = cause.__cause__ or cause.__context__
                logger.warning(
                    'Caller restart failed: bot_id=%s operation_id=%s phase=%s error_type=%s',
                    payload['bot_id'], operation, phase, type(error).__name__,
                )
                state.save(state.read(), phase='FAILED', message=message)
                return Fail(message)


class AicodingCallerRestartLifecycle(LifecycleBase):
    def __init__(self, registry: HandlerRegistry, handler: AicodingCallerRestartHandler):
        self.registry, self.handler = registry, handler

    async def bootstrap(self):
        self.registry.register(self.handler, wake_on_enqueue=True)
