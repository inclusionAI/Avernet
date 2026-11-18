"""Cross-cutting helpers shared by task_experience prompt builders.

Reuses the task-domain constants from
``community/core/task/domain/prompt_constants`` rather than re-declaring them:
when ``NO_WEB_SEARCH_CONSTRAINT`` / ``OUTPUT_LANGUAGE_CONSTRAINT`` evolve, the
task_experience prompts inherit those changes alongside ``task_runner``,
``forum`` and ``todos`` callers.
"""

from __future__ import annotations

from agentclaw.community.core.task.domain.prompt_constants import (
    NO_WEB_SEARCH_CONSTRAINT,
    OUTPUT_LANGUAGE_CONSTRAINT,
)

__all__ = ["JSON_OUTPUT_ONLY_SENTENCE", "append_constraints"]

#: Mandatory sentence placed just before the cross-cutting constraints in
#: every task_experience LLM prompt. Builders emit a more specific JSON shape
#: inside their body; this line is the canonical "JSON only, no explanation"
#: anchor that callers / reviewers can grep for.
JSON_OUTPUT_ONLY_SENTENCE = "只输出 JSON 字符串数组/对象, 不要解释。"


def append_constraints(prompt: str) -> str:
    """Append the task-domain cross-cutting constraints to a prompt.

    The returned string has shape::

        <builder body>\n
        \n
        <JSON_OUTPUT_ONLY_SENTENCE>\n
        \n
        <NO_WEB_SEARCH_CONSTRAINT>
        <OUTPUT_LANGUAGE_CONSTRAINT>

    Builders call this last; they should not concatenate the constraints
    themselves so the policy sentences always appear in the canonical order.
    """
    body = prompt.rstrip()
    return (
        f"{body}\n\n"
        f"{JSON_OUTPUT_ONLY_SENTENCE}\n\n"
        f"{NO_WEB_SEARCH_CONSTRAINT}"
        f"{OUTPUT_LANGUAGE_CONSTRAINT}"
    )
