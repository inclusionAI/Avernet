"""生成回答：优先调用 OpenAI-compatible 模型，失败时退化为规则兜底。

真实对局使用 LLM；没有模型凭证时（例如离线测试）自动只使用规则兜底，
保证协议链路仍可验证，但报告必须把这类对局标注为“规则兜底”，不能冒充模型对局。
"""

from __future__ import annotations

import asyncio
import json
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

from . import guard, persona
from .config import PlayerConfig
from .tasks import GameTask, TaskKind, vote_start_text


class BrainUnavailable(RuntimeError):
    """模型不可用或输出无法修复。"""


class SkipTask(RuntimeError):
    """该任务只需 ACK，不产生 final。"""


@dataclass(frozen=True)
class Answer:
    text: str
    source: str
    detail: str = ""


class Brain(Protocol):
    async def answer(self, task: GameTask, run_id: str) -> Answer: ...


def validate_answer(task: GameTask, text: str) -> str | None:
    if task.kind == TaskKind.VOTE_START:
        return None if text == vote_start_text() else "投票预备必须原样输出固定句"
    if task.kind == TaskKind.VOTE:
        return guard.vote_violation(
            text,
            legal_targets=task.legal_targets,
            self_seat=task.seat,
            allow_abstain=task.allow_abstain,
            max_chars=task.max_chars,
        )
    if task.kind == TaskKind.EULOGY:
        return guard.eulogy_violation(text, task.word or "", task.max_chars, task.candidate_seats)
    if task.kind == TaskKind.SPEECH:
        return guard.speech_violation(text, task.word or "", task.max_chars)
    return None


def _seed(run_id: str) -> int:
    return int(sha256(run_id.encode("utf-8")).hexdigest()[:8], 16)


SPEECH_TEMPLATES = (
    "平时不太动它，占地方。",
    "用起来得两只手，急不得。",
    "家里一般都会有一个。",
    "说不上来，反正挺常见的。",
    "一般放在顺手的地方。",
    "用久了会有点旧。",
    "第一次见的人也能认出来。",
    "不算贵，但少了不方便。",
    "什么时候会想到它，得看场合。",
    "大概就是这个样子。",
    "拿出来的时候不太起眼。",
    "不常提，但总能碰到。",
)

EULOGY_TEMPLATES = (
    "我最不放心{seat}号，前面那句和场上不太合。",
    "我怀疑{seat}号，他说的和大家对不上。",
    "{seat}号那句我听着别扭，和前面几位不是一个路子。",
)


def _fallback_speech(word: str, seed: int) -> str:
    ordered = list(SPEECH_TEMPLATES)
    offset = seed % len(ordered)
    ordered = ordered[offset:] + ordered[:offset]
    for candidate in ordered:
        if guard.speech_violation(candidate, word, 25) is None:
            return candidate
    for candidate in ("嗯，不太好说。", "这个嘛，说不好。", "就是那个，挺常见的。"):
        if guard.speech_violation(candidate, word, 25) is None:
            return candidate
    return "嗯。"


def _fallback_vote(task: GameTask, seed: int) -> str:
    # 常规投票缺少可靠模型判断时明确弃权，避免把随机票伪装成推理结果。
    if task.allow_abstain:
        return "我弃权"
    candidates = [seat for seat in task.legal_targets if seat != task.seat]
    if candidates:
        return f"我投{candidates[seed % len(candidates)]}号"
    # PK 正常一定有候选；真的没有时也不能弃权，给一个明确无效票会被主持人作废，
    # 但比空回复更能暴露数据问题。
    return "我弃权"


def _fallback_eulogy(task: GameTask, seed: int) -> str:
    seats = [seat for seat in task.candidate_seats if seat != task.seat]
    seat = seats[seed % len(seats)] if seats else 1
    template = EULOGY_TEMPLATES[seed % len(EULOGY_TEMPLATES)]
    text = template.format(seat=seat)
    if guard.eulogy_violation(text, task.word or "", task.max_chars, task.candidate_seats):
        text = f"我最不放心{seat}号。"
    return text


class RulesBrain:
    """无模型时的确定性兜底；只保证协议和硬约束，不保证发言质量。"""

    async def answer(self, task: GameTask, run_id: str) -> Answer:
        seed = _seed(run_id)
        if task.kind == TaskKind.VOTE_START:
            return Answer(vote_start_text(), "rules", "fixed vote-start phrase")
        if task.kind == TaskKind.VOTE:
            return Answer(_fallback_vote(task, seed), "rules", "explicit abstention or deterministic PK vote")
        if task.kind == TaskKind.EULOGY:
            return Answer(_fallback_eulogy(task, seed), "rules", "deterministic eulogy")
        if task.kind == TaskKind.SPEECH:
            return Answer(_fallback_speech(task.word or "", seed), "rules", "template speech")
        if task.kind == TaskKind.ONBOARDING:
            raise SkipTask
        return Answer(_fallback_speech(task.word or "", seed), "rules", "unknown task fallback")


class LlmBrain:
    def __init__(self, config: PlayerConfig) -> None:
        if not config.llm_base_url or not config.llm_api_key or not config.llm_model:
            raise ValueError("LLM configuration is incomplete")
        self._config = config

    async def answer(self, task: GameTask, run_id: str) -> Answer:
        if task.kind == TaskKind.VOTE_START:
            return Answer(vote_start_text(), "rules", "fixed vote-start phrase")
        if task.kind == TaskKind.ONBOARDING:
            raise SkipTask
        messages = self._messages(task)
        candidate = await self._complete(messages)
        candidate = _clean_candidate(task, candidate)
        issue = validate_answer(task, candidate)
        if issue is None:
            return Answer(candidate, "llm", self._config.llm_model or "")
        repair_messages = [
            *messages,
            {"role": "assistant", "content": candidate},
            {
                "role": "user",
                "content": f"上一条输出不合规：{issue}。请只重新输出符合任务格式的一句话，不要解释。",
            },
        ]
        candidate = await self._complete(repair_messages)
        candidate = _clean_candidate(task, candidate)
        issue = validate_answer(task, candidate)
        if issue is not None:
            raise BrainUnavailable(f"model output still invalid: {issue}")
        return Answer(candidate, "llm", self._config.llm_model or "")

    def _messages(self, task: GameTask) -> list[dict[str, str]]:
        if task.kind == TaskKind.SPEECH:
            return persona.speech_messages(task)
        if task.kind == TaskKind.VOTE:
            return persona.vote_messages(task)
        if task.kind == TaskKind.EULOGY:
            return persona.eulogy_messages(task)
        raise BrainUnavailable(f"no model prompt for task kind {task.kind}")

    async def _complete(self, messages: list[dict[str, str]]) -> str:
        """请求一次模型；推理模型可能先消耗 reasoning token，空内容要放大预算重试。"""

        budget = self._config.llm_max_tokens
        ceiling = max(budget, self._config.llm_max_tokens_ceiling)
        attempts = max(1, self._config.llm_max_attempts)
        last_reason = ""
        has_reasoning = False
        for attempt in range(attempts):
            request_messages = messages
            if attempt and last_reason != "length":
                request_messages = [
                    *messages,
                    {"role": "user", "content": "请直接输出任务要求的那一句话，不要输出思考过程。"},
                ]
            content, finish_reason, has_reasoning = await self._request(request_messages, budget)
            last_reason = finish_reason or ""
            text = content.strip()
            if text and finish_reason != "length":
                return text
            # 空 content 或 finish_reason=length：说明 max_tokens 被思考/截断吃掉。
            if attempt + 1 < attempts and budget < ceiling:
                budget = min(budget * 2, ceiling)
        raise BrainUnavailable(
            "model returned no usable content "
            f"(finish_reason={last_reason or 'unknown'}, reasoning_present={has_reasoning}, "
            f"attempts={attempts}, max_tokens={budget})"
        )

    async def _request(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
    ) -> tuple[str, str | None, bool]:
        endpoint = _chat_endpoint(self._config.llm_base_url or "")
        payload = {
            "model": self._config.llm_model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": max_tokens,
            "stream": False,
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._config.llm_api_key}",
            },
        )
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(_read_url, request, self._config.llm_timeout_seconds),
                timeout=self._config.llm_timeout_seconds,
            )
            data = json.loads(raw)
            choices = data.get("choices")
            if not isinstance(choices, list) or not choices:
                raise BrainUnavailable("model response has no choices")
            choice = choices[0] if isinstance(choices[0], dict) else {}
            message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
            content = message.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = "".join(
                    block.get("text", "") for block in content if isinstance(block, dict)
                )
            else:
                text = ""
            reasoning = message.get("reasoning_content") or message.get("reasoning")
            finish_reason = choice.get("finish_reason")
            return text, finish_reason if isinstance(finish_reason, str) else None, bool(reasoning)
        except TimeoutError as error:
            raise BrainUnavailable("model request timed out") from error
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as error:
            raise BrainUnavailable(f"model request failed: {type(error).__name__}{_tls_hint(error)}") from error


def _tls_hint(error: BaseException) -> str:
    """本机默认 CA 链对该服务可能报 CERTIFICATE_VERIFY_FAILED；只提示，不关闭校验。"""

    reason = getattr(error, "reason", None)
    text = f"{reason or ''} {error}"
    if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in text:
        return "; TLS CA verification failed, set SSL_CERT_FILE=/etc/ssl/cert.pem (never disable verification)"
    return ""


def _read_url(request: urllib.request.Request, timeout: float) -> str:
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - local/explicit endpoint
        return response.read().decode("utf-8")


def _chat_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _clean_candidate(task: GameTask, candidate: str) -> str:
    text = guard.normalize_sentence(candidate)
    if task.kind == TaskKind.VOTE:
        abstain = "我弃权" in text
        match = re.search(r"我投\s*(\d+)\s*号", text)
        if match:
            return f"我投{int(match.group(1))}号"
        if abstain:
            return "我弃权"
        return text
    # 发言/遗言只取第一句可见文本，避免模型附带解释。
    for line in text.splitlines():
        line = guard.normalize_sentence(line)
        if line:
            return line
    return text


class HybridBrain:
    """有模型用模型；模型不可用或输出不可修复时退化为规则兜底。"""

    def __init__(self, config: PlayerConfig) -> None:
        self._rules = RulesBrain()
        self._llm: LlmBrain | None = None
        if config.llm_enabled():
            self._llm = LlmBrain(config)

    async def answer(self, task: GameTask, run_id: str) -> Answer:
        if self._llm is None:
            return await self._rules.answer(task, run_id)
        try:
            return await self._llm.answer(task, run_id)
        except SkipTask:
            raise
        except BrainUnavailable as error:
            fallback = await self._rules.answer(task, run_id)
            return Answer(fallback.text, "rules", f"llm fallback: {error}")
        except Exception as error:
            fallback = await self._rules.answer(task, run_id)
            return Answer(fallback.text, "rules", f"llm fallback: {type(error).__name__}")


def build_brain(config: PlayerConfig) -> Brain:
    return HybridBrain(config)
