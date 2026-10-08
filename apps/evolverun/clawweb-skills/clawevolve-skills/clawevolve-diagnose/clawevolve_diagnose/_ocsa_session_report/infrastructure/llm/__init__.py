"""API-only judge transport bridge for copied session_report logic.

The evaluator code is copied from OpenclawSessionAnalysis. Diagnose keeps this
copy on the original OpenAI-compatible API path; keyless runs use the separate
diagnose-native subagent analyzer and never route through this module.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from .... import logger as diag_logger
from ....constants import DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS
from ....judge.openai_chat_client import chat_json
from ....models import JudgeRuntimeConfig, LlmRuntimeConfig, normalize_judge_backend

logger = logging.getLogger(__name__)
_runtime: JudgeRuntimeConfig | None = None


@dataclass
class LLMCallTask:
    name: str
    user_prompt: str
    system_prompt: str
    timeout: int = DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS
    # Observability metadata.  Keep it outside the prompt so logs can identify
    # the owning session and pipeline phase without changing OCSA contracts.
    session_id: str = ""
    phase: str = ""


@dataclass
class LLMCallResult:
    name: str
    status: str
    result: dict[str, Any] | None = None
    error: Exception | None = None
    elapsed: float = 0.0
    model_used: str = ""


def configure_runtime(runtime: JudgeRuntimeConfig | LlmRuntimeConfig) -> None:
    """Configure the judge runtime used by copied session_report evaluators.

    Args:
        runtime: New transport-agnostic judge runtime. A legacy
            ``LlmRuntimeConfig`` is accepted for compatibility and treated as an
            API backend.
    """

    global _runtime
    if isinstance(runtime, LlmRuntimeConfig):
        runtime = JudgeRuntimeConfig(backend="api", api=runtime)
    if normalize_judge_backend(runtime.backend) != "api":
        raise RuntimeError(
            "Copied OCSA session_report requires the API judge backend; "
            "keyless diagnose runs must use KeylessSubagentSessionAnalyzer."
        )
    _runtime = runtime
    diag_logger.info(
        "ocsa llm runtime configured",
        backend=normalize_judge_backend(runtime.backend),
        base_url=runtime.api.base_url,
        model=runtime.api.model,
        api_key_configured=bool(runtime.api.api_key),
        api_key_persisted=False,
    )


def _call_judge_llm_internal(
    user_prompt: str,
    system_prompt: str,
    timeout: int = DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS,
    call_name: str = "",
) -> tuple[dict, str]:
    if _runtime is None:
        raise RuntimeError("Judge runtime is not configured")
    start_time = time.time()
    backend = normalize_judge_backend(_runtime.backend)
    if backend != "api":
        raise RuntimeError(
            "Copied OCSA session_report requires the API judge backend; "
            f"got {backend!r}."
        )
    diag_logger.info(
        "llm execution start",
        step="ocsa_judge",
        phase=_phase_from_call_name(call_name),
        session_id=_session_id_from_call_name(call_name),
        call_name=call_name or "unspecified",
        backend=backend,
        base_url=_runtime.api.base_url,
        model=_runtime.api.model,
        system_chars=len(str(system_prompt or "")),
        user_prompt_chars=len(str(user_prompt or "")),
        timeout_seconds=timeout,
    )
    result = chat_json(
        _runtime.api.api_key,
        _runtime.api.base_url,
        _runtime.api.model,
        system_prompt,
        user_prompt,
        timeout=timeout,
        call_name=call_name or "ocsa_session_report",
    )
    model_used = _runtime.api.model
    elapsed = time.time() - start_time
    result_str = str(result)
    logger.info(
        "[LLM Output] backend=%s model=%s response(%d chars, %.2fs)",
        backend,
        model_used,
        len(result_str),
        elapsed,
    )
    diag_logger.info(
        "llm execution done",
        step="ocsa_judge",
        phase=_phase_from_call_name(call_name),
        session_id=_session_id_from_call_name(call_name),
        call_name=call_name or "unspecified",
        backend=backend,
        model=model_used,
        elapsed_seconds=f"{elapsed:.2f}",
        result_chars=len(result_str),
        result_keys=sorted(result.keys()) if isinstance(result, dict) else [],
    )
    return result, model_used


def call_judge_llm_batch(
    tasks: list[LLMCallTask],
    max_concurrent_tasks: int = 3,
    fail_fast: bool = False,
) -> dict[str, LLMCallResult]:
    if not tasks:
        return {}
    max_workers = _effective_max_workers(tasks, max_concurrent_tasks)
    diag_logger.info(
        "ocsa llm batch start",
        task_count=len(tasks),
        task_names=[task.name for task in tasks],
        max_concurrent_tasks=max_concurrent_tasks,
        effective_workers=max_workers,
        fail_fast=fail_fast,
        prompt_chars_by_task={task.name: len(str(task.user_prompt or "")) for task in tasks},
        session_ids_by_task={task.name: task.session_id for task in tasks},
        phases_by_task={task.name: task.phase for task in tasks},
    )
    if len(tasks) == 1 or max_workers == 1:
        return _call_tasks_sequentially(tasks, fail_fast=fail_fast)

    results: dict[str, LLMCallResult] = {}
    inflight_sem = threading.Semaphore(max_workers)
    first_error: Exception | None = None

    def _call_single(task: LLMCallTask) -> LLMCallResult:
        start = time.time()
        try:
            diag_logger.info(
                "llm task start",
                step="ocsa_judge",
                phase=task.phase or _phase_from_call_name(task.name),
                session_id=task.session_id or _session_id_from_call_name(task.name),
                call_name=task.name,
                prompt_chars=len(str(task.user_prompt or "")),
            )
            result, model_used = _call_judge_llm_internal(
                task.user_prompt,
                task.system_prompt,
                timeout=task.timeout,
                call_name=task.name,
            )
            diag_logger.info(
                "llm task done",
                step="ocsa_judge",
                phase=task.phase or _phase_from_call_name(task.name),
                session_id=task.session_id or _session_id_from_call_name(task.name),
                call_name=task.name,
            )
            return LLMCallResult(
                name=task.name,
                status="success",
                result=result,
                elapsed=time.time() - start,
                model_used=model_used,
            )
        except Exception as e:  # noqa: BLE001 - batch records all judge transport failures uniformly.
            logger.error(
                "[LLM Batch] 任务 %s 异常 (%.2fs): %s: %s",
                task.name,
                time.time() - start,
                type(e).__name__,
                e,
            )
            diag_logger.warning(
                "ocsa llm batch task failed",
                task_name=task.name,
                phase=task.phase or _phase_from_call_name(task.name),
                session_id=task.session_id or _session_id_from_call_name(task.name),
                elapsed_seconds=f"{time.time() - start:.2f}",
                error_class=type(e).__name__,
                error=str(e)[:1000],
            )
            return LLMCallResult(
                name=task.name,
                status="error",
                error=e,
                elapsed=time.time() - start,
            )

    def _call_with_sem(task: LLMCallTask) -> LLMCallResult:
        inflight_sem.acquire()
        try:
            return _call_single(task)
        finally:
            inflight_sem.release()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_call_with_sem, task): task for task in tasks}
        for future in as_completed(futures):
            task = futures[future]
            try:
                call_result = future.result(timeout=task.timeout)
                results[task.name] = call_result
                if call_result.status == "error" and first_error is None:
                    first_error = call_result.error
                    if fail_fast:
                        for f in futures:
                            f.cancel()
                        break
            except TimeoutError as e:
                results[task.name] = LLMCallResult(
                    name=task.name,
                    status="error",
                    error=TimeoutError(f"LLM 调用超时: {e}"),
                )
            except Exception as e:  # noqa: BLE001
                results[task.name] = LLMCallResult(name=task.name, status="error", error=e)
    logger.info(
        "[LLM Batch] 完成, 成功: %d/%d",
        sum(1 for r in results.values() if r.status == "success"),
        len(results),
    )
    diag_logger.info(
        "ocsa llm batch done",
        task_count=len(tasks),
        result_count=len(results),
        success_count=sum(1 for r in results.values() if r.status == "success"),
        error_count=sum(1 for r in results.values() if r.status != "success"),
        results={
            name: {
                "status": result.status,
                "elapsed": f"{result.elapsed:.2f}",
                "model": result.model_used,
                "error": f"{type(result.error).__name__}: {result.error}"[:500]
                if result.error
                else "",
            }
            for name, result in results.items()
        },
    )
    return results


def _call_tasks_sequentially(
    tasks: list[LLMCallTask], *, fail_fast: bool = False
) -> dict[str, LLMCallResult]:
    results: dict[str, LLMCallResult] = {}
    for task in tasks:
        start = time.time()
        try:
            diag_logger.info(
                "llm task start",
                step="ocsa_judge",
                phase=task.phase or _phase_from_call_name(task.name),
                session_id=task.session_id or _session_id_from_call_name(task.name),
                call_name=task.name,
                prompt_chars=len(str(task.user_prompt or "")),
            )
            result, model_used = _call_judge_llm_internal(
                task.user_prompt,
                task.system_prompt,
                timeout=task.timeout,
                call_name=task.name,
            )
            diag_logger.info(
                "llm task done",
                step="ocsa_judge",
                phase=task.phase or _phase_from_call_name(task.name),
                session_id=task.session_id or _session_id_from_call_name(task.name),
                call_name=task.name,
            )
            results[task.name] = LLMCallResult(
                name=task.name,
                status="success",
                result=result,
                elapsed=time.time() - start,
                model_used=model_used,
            )
        except Exception as e:  # noqa: BLE001
            logger.error(
                "[LLM Batch] 顺序任务 %s 异常: %s: %s",
                task.name,
                type(e).__name__,
                e,
            )
            diag_logger.warning(
                "ocsa llm sequential task failed",
                task_name=task.name,
                phase=task.phase or _phase_from_call_name(task.name),
                session_id=task.session_id or _session_id_from_call_name(task.name),
                elapsed_seconds=f"{time.time() - start:.2f}",
                error_class=type(e).__name__,
                error=str(e)[:1000],
            )
            results[task.name] = LLMCallResult(
                name=task.name,
                status="error",
                error=e,
                elapsed=time.time() - start,
            )
            if fail_fast:
                break
    logger.info(
        "[LLM Batch] 顺序完成, 成功: %d/%d",
        sum(1 for r in results.values() if r.status == "success"),
        len(results),
    )
    diag_logger.info(
        "ocsa llm sequential batch done",
        task_count=len(tasks),
        result_count=len(results),
        success_count=sum(1 for r in results.values() if r.status == "success"),
        error_count=sum(1 for r in results.values() if r.status != "success"),
        results={
            name: {
                "status": result.status,
                "elapsed": f"{result.elapsed:.2f}",
                "model": result.model_used,
                "error": f"{type(result.error).__name__}: {result.error}"[:500]
                if result.error
                else "",
            }
            for name, result in results.items()
        },
    )
    return results


def _phase_from_call_name(call_name: str) -> str:
    name = str(call_name or "")
    if name.startswith("request_match"):
        return "request_match"
    if name.startswith("task_"):
        return name.rsplit("_", 1)[-1]
    if name.startswith("direct_native_session"):
        return "native_session_analysis"
    if name.startswith("query_rewriter"):
        return "eval_query_rewrite"
    return name.split(":", 1)[0] or "unspecified"


def _session_id_from_call_name(call_name: str) -> str:
    name = str(call_name or "")
    if ":" in name:
        return name.split(":", 1)[1]
    return ""


def _effective_max_workers(tasks: list[LLMCallTask], requested: int) -> int:
    return max(1, min(max(1, requested), len(tasks)))
