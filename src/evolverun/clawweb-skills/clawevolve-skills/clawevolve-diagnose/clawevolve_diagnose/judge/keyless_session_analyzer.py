from __future__ import annotations

import time
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .. import logger
from ..constants import DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS
from ..models import CasePreference, Diagnosis, JudgeRuntimeConfig, SessionRow
from ..utils import redact_secrets
from .native_session_analysis import (
    build_native_session_analysis_input,
    compact_hint,
    failed_native_session_analysis_diagnosis,
    map_native_session_analysis_result,
)
from .agent_session_contract import (
    validate_agent_session_result,
)
from .openclaw_subagent_client import OpenClawJsonSubagentClient, validate_subagent_runtime
from .keyless_session_prompt import build_session_analysis_prompt


@dataclass(frozen=True)
class KeylessSessionJudgeConfig:
    """Configuration for the diagnose-native keyless subagent analyzer."""

    runtime: JudgeRuntimeConfig
    preference: CasePreference | None = None
    timeout_seconds: int = DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS
    artifact_dir: Path | None = None


class KeylessSubagentSessionAnalyzer:
    """Analyze one local session at a time through an OpenClaw subagent.

    This class is now only the keyless transport adapter.  The single-session
    diagnose-native contract, input schema, output schema, and Diagnosis mapper
    live in ``native_session_analysis`` and are shared with the API-key direct
    LLM adapter.
    """

    SOURCE_NOTE = "agent_perspective_subagent"

    def __init__(self, config: KeylessSessionJudgeConfig):
        validate_subagent_runtime(config.runtime.subagent)
        self.config = config
        self.client = OpenClawJsonSubagentClient(config.runtime.subagent)

    def analyze(self, rows: Iterable[SessionRow], bot_id: str = "") -> list[Diagnosis]:
        rows_list = list(rows)
        started_at = time.time()
        logger.info(
            "keyless subagent chunk analysis start",
            session_count=len(rows_list),
            bot_id=bot_id,
            agent_id=self.config.runtime.subagent.agent_id,
            timeout_seconds=self.config.timeout_seconds
            or self.config.runtime.subagent.timeout_seconds,
            sequential=True,
            shared_native_contract=True,
        )
        diagnoses: list[Diagnosis] = []
        rejected_count = 0
        failed_count = 0
        for index, row in enumerate(rows_list, start=1):
            session_started_at = time.time()
            artifact_dir = self._artifact_dir(row)
            logger.info(
                "keyless subagent session analysis start",
                index=f"{index}/{len(rows_list)}",
                session_id=row.session_id,
                created_at=row.created_at,
                path=row.path,
                first_question_preview=compact_hint(row.first_question or row.user_text, 240),
            )
            try:
                subagent_input = build_native_session_analysis_input(
                    row,
                    self.config.preference,
                    include_session_content=False,
                )
                prompt = build_session_analysis_prompt(subagent_input)
                self._write_json(
                    artifact_dir / "request.json",
                    {
                        "session_id": row.session_id,
                        "session_path": row.path,
                        "input": subagent_input,
                        "prompt": prompt,
                    },
                )
                logger.info(
                    "keyless subagent prompt prepared",
                    session_id=row.session_id,
                    prompt_chars=len(prompt),
                    input_schema=subagent_input.get("schema_version"),
                    output_schema=subagent_input.get("output_schema"),
                    user_request_preview=compact_hint(
                        subagent_input.get("requirements", {}).get("user_request", ""), 240
                    ),
                    includes_session_content=False,
                )
                captured = self.client.run_json_prompt_captured(
                    prompt,
                    timeout=self.config.timeout_seconds
                    or self.config.runtime.subagent.timeout_seconds,
                )
                (artifact_dir / "response.txt").write_text(
                    captured.raw_text, encoding="utf-8"
                )
                self._write_json(artifact_dir / "response.json", captured.parsed)
                if captured.parsed is None:
                    validation = {
                        "valid": False,
                        "errors": [captured.parse_error or "response is not valid JSON"],
                        "accepted": False,
                        "decision": "judge_output_invalid_json",
                    }
                    self._write_json(artifact_dir / "validation.json", validation)
                    raise ValueError(validation["errors"][0])
                subagent_output = captured.parsed
                validation = validate_agent_session_result(row, subagent_output)
                validation["accepted"] = False
                validation["decision"] = (
                    "pending_business_mapping" if validation["valid"] else "judge_output_contract_invalid"
                )
                self._write_json(artifact_dir / "validation.json", validation)
                if not validation["valid"]:
                    raise ValueError("; ".join(validation["errors"]))
                logger.info(
                    "keyless subagent raw output parsed",
                    session_id=row.session_id,
                    output_schema=subagent_output.get("schema_version"),
                    is_evaluable=subagent_output.get("is_evaluable"),
                    case_type=subagent_output.get("case_type"),
                    root_cause_class=subagent_output.get("root_cause_class"),
                    quality_score=subagent_output.get("quality_score"),
                    confidence=subagent_output.get("confidence"),
                    reject_reason=str(subagent_output.get("reject_reason") or "")[:300],
                    reject_category=str(subagent_output.get("reject_category") or "")[:120],
                    reject_detail=str(subagent_output.get("reject_detail") or "")[:500],
                )
                diagnosis = map_native_session_analysis_result(
                    row, subagent_output, source_note=self.SOURCE_NOTE
                )
                if diagnosis is not None:
                    validation.update(accepted=True, decision="accepted")
                    self._write_json(artifact_dir / "validation.json", validation)
                    diagnoses.append(diagnosis)
                    logger.info(
                        "keyless subagent session accepted",
                        session_id=row.session_id,
                        case_type=diagnosis.case_type,
                        root_cause_class=diagnosis.root_cause_class,
                        evolution_failure_mode=diagnosis.evolution_failure_mode,
                        quality_score=f"{diagnosis.quality_score:.3f}",
                        confidence=f"{diagnosis.confidence:.3f}",
                        evidence_count=len(diagnosis.evidence or []),
                        query_preview=compact_hint(diagnosis.query, 240),
                        elapsed_seconds=f"{time.time() - session_started_at:.2f}",
                    )
                else:
                    validation.update(accepted=False, decision="filtered_after_contract_validation")
                    self._write_json(artifact_dir / "validation.json", validation)
                    rejected_count += 1
                    logger.info(
                        "keyless subagent session rejected",
                        session_id=row.session_id,
                        elapsed_seconds=f"{time.time() - session_started_at:.2f}",
                    )
            except Exception as exc:  # noqa: BLE001 - preserve structured pipeline failure per session.
                response_txt = artifact_dir / "response.txt"
                response_json = artifact_dir / "response.json"
                validation_json = artifact_dir / "validation.json"
                if not response_txt.exists():
                    response_txt.write_text("", encoding="utf-8")
                if not response_json.exists():
                    self._write_json(response_json, None)
                if not validation_json.exists():
                    self._write_json(
                        validation_json,
                        {
                            "valid": False,
                            "accepted": False,
                            "decision": "judge_execution_failed",
                            "errors": [f"{type(exc).__name__}: {exc}"],
                        },
                    )
                failed_count += 1
                logger.warning(
                    "keyless subagent session analysis failed",
                    session_id=row.session_id,
                    error=f"{type(exc).__name__}: {redact_secrets(str(exc), [self.config.runtime.api.api_key])}"[:800],
                    elapsed_seconds=f"{time.time() - session_started_at:.2f}",
                )
                diagnoses.append(
                    failed_native_session_analysis_diagnosis(
                        row,
                        exc,
                        source_label="openclaw_subagent",
                        source_note=self.SOURCE_NOTE,
                        secrets=[self.config.runtime.api.api_key],
                    )
                )
        logger.info(
            "keyless subagent chunk analysis done",
            session_count=len(rows_list),
            diagnosis_count=len(diagnoses),
            rejected_count=rejected_count,
            failed_count=failed_count,
            elapsed_seconds=f"{time.time() - started_at:.2f}",
        )
        return sorted(diagnoses, key=lambda d: (d.quality_score, d.confidence), reverse=True)

    def _artifact_dir(self, row: SessionRow) -> Path:
        base = self.config.artifact_dir or Path.cwd() / "diagnose" / "output" / "judge"
        safe_session_id = "".join(
            char if char.isalnum() or char in {"-", "_", "."} else "_"
            for char in str(row.session_id or "unknown-session")
        )[:180]
        target = base / safe_session_id
        target.mkdir(parents=True, exist_ok=True)
        return target

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

    def close(self) -> None:
        """Release the task-scoped OpenClaw judge agent."""

        self.client.close()


# Backward-compatible public name used by older tests/imports.
def map_keyless_subagent_result(row: SessionRow, result: dict) -> Diagnosis | None:
    return map_native_session_analysis_result(
        row, result, source_note=KeylessSubagentSessionAnalyzer.SOURCE_NOTE
    )
