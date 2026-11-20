"""task_experience — generate task plan / acceptance expansion from KGBase experience policies.

Sibling to task_center / task_runner / task_clarification / task_discovery / task_harness.
Public Protocols and value types live in :mod:`task_experience.protocols` and
:mod:`task_experience.types`; concrete services ship under
:mod:`task_experience.services` and the OpenAI-compatible LLM adapter lives under
:mod:`task_experience.adapters`.
"""

from __future__ import annotations

__all__: list[str] = []
