"""Bot config manifest service (issue #1469, W1).

Owns the rules the repository deliberately does not: document validation,
the engine capability gate, the limits, and the "stored but not yet applied"
frame. This service implements no apply, no fetch and no lifecycle wiring —
those are W2+/W4+ on purpose, so a stored document is a *declaration*, and
the phase-1 capability table says exactly which declared parts nothing could
apply yet.

The service inherits ``ManifestServiceProtocol`` (same core-implements-api
shape as ``BotStartupScriptService``); ``test_service_api_conformance.py``
checks full signatures on top of that inheritance.
"""
from __future__ import annotations

from typing import Optional

from injector import inject

from agentclaw.community.core.bot_config_manifest.capabilities import (
    RESERVED_IDENTITY_FILES,
    CategorySupport,
    identity_file_whitelist,
    supported_categories,
)
from agentclaw.community.core.bot_config_manifest.manifest_schema import (
    MAX_DOCUMENT_BYTES,
    EMPTY_DOCUMENT,
    ManifestDocument,
    ManifestInvalidError,
    Violation,
    parse_document,
    validate_document,
)
from agentclaw.community.core.bot_config_manifest.manifest_service_protocol import (
    MAX_MODIFIER_CHARS,
    ManifestServiceProtocol,
)
from agentclaw.community.core.bot_config_manifest.repository.models import (
    BotConfigManifestRecord,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotConfigManifestRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env


logger = get_logger()

#: 类目「已声明」的判定:列表非空;engine_config 单对象,声明即非 None。
_LIST_CATEGORIES = ("mcp", "resources", "skills", "identity", "cli_tools")


class ManifestService(ManifestServiceProtocol):
    """读、整体替换、清除一份 bot 的配置清单文档（存储与校验的 owner）。"""

    @inject
    def __init__(self, repository: BotConfigManifestRepositoryProtocol) -> None:
        self._repository = repository

    # --- read side ----------------------------------------------------------

    def get(
        self, *, entity_id: str, bot_id: str
    ) -> ManifestDocument:
        """Return the stored document or the empty document — never an error.

        No ownership check on top of the key: ``(env, entity_id, bot_id)``
        names one bot for the life of the data (same reasoning as the
        startup-script row), and the public layer's own-bot check ran already.
        """
        record = self.get_record(entity_id=entity_id, bot_id=bot_id)
        if record is None:
            return EMPTY_DOCUMENT
        return parse_document(record.document)

    def get_record(
        self, *, entity_id: str, bot_id: str
    ) -> Optional[BotConfigManifestRecord]:
        return self._repository.get(
            env=get_current_env(), entity_id=entity_id, bot_id=bot_id
        )

    # --- capability ---------------------------------------------------------

    def capabilities(
        self,
        *,
        engine_type: str,
        bot_type: str,
        script_supported: Optional[bool] = None,
    ) -> CategorySupport:
        """The one resolver both read and write paths consult (#1469).

        The pure table answers the engine half; ``script_supported`` — the
        #935 form-factor judgment — only ever *narrows*: a ``False`` from the
        bot's own support state cannot turn a refused engine into a supported
        one, but a supported engine whose bot cannot run scripts must refuse
        (LOCAL/singlebox, ARCA-direct legacy). ``None`` keeps the table value.
        """
        support = supported_categories(engine_type, bot_type)
        if script_supported is False and support.categories.get(
            "script"
        ):
            support = CategorySupport(
                categories={**support.categories, "script": False},
                reasons={
                    **support.reasons,
                    "script": "script is not supported for this bot (#935 support judgment)",
                },
            )
        return support

    # --- write side ---------------------------------------------------------

    def put(
        self,
        *,
        entity_id: str,
        bot_id: str,
        engine_type: str,
        bot_type: str,
        document: object,
        modifier: str,
        script_supported: Optional[bool] = None,
    ) -> dict:
        """Validate and store-or-replace the whole document — all-or-nothing."""
        # 审计宽度:与 startup-script 同款兜底——组合出的 actor(``app:…:on-…`` 前缀)
        # 可能超过列宽,构造期截断而不是让一次合法写入死于 DB 报错。
        modifier = (modifier or "")[:MAX_MODIFIER_CHARS]
        doc = parse_document(document)
        violations = list(validate_document(doc))
        violations.extend(
            self._capability_violations(
                doc,
                engine_type=engine_type,
                bot_type=bot_type,
                script_supported=script_supported,
            )
        )
        # 序列化保留「显式声明空类目」:exclude_none 只剥值为 null 的字段
        # (subpath 等),而 exclude_defaults 会把 mcp=[] 连同 schema_version 一起
        # 剥掉——存下去就和「从未声明」无法区分,而这条区别是 D2 语义的承重墙
        # (空列表=清空该类目,缺省=无意见)。gap 见 round-trip 测试钉的
        # test_a_declared_empty_category_survives_storage。
        canonical = doc.model_dump_json(by_alias=True, exclude_none=True)
        if len(canonical.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            violations.append(
                Violation(
                    entry="<document>",
                    rule="limit-document-bytes",
                    message=f"serialized document is over the "
                    f"{MAX_DOCUMENT_BYTES}-byte limit",
                )
            )
        if violations:
            # 不写入任何东西:all-or-nothing 是#1469 的验收字面。
            raise ManifestInvalidError(violations)

        record = self._repository.upsert(
            env=get_current_env(),
            entity_id=entity_id,
            bot_id=bot_id,
            schema_version=doc.schema_version,
            document=canonical,
            size_bytes=len(canonical.encode("utf-8")),
            modifier=modifier,
        )
        return {
            "bot_id": bot_id,
            "schema_version": record.schema_version,
            "warnings": self._warnings(doc),
            "modifier": record.modifier,
            "gmt_modified": record.gmt_modified,
        }

    def delete(self, *, entity_id: str, bot_id: str) -> bool:
        """移除声明行;幂等。从不删除任何物化实体或 managed 标记。"""
        deleted = self._repository.delete(
            env=get_current_env(), entity_id=entity_id, bot_id=bot_id
        )
        if deleted:
            logger.info(
                "[config_manifest.delete] env=%s, entity_id=%s, bot_id=%s",
                get_current_env(),
                entity_id,
                bot_id,
            )
        return deleted

    # --- validation plugins -------------------------------------------------

    def _capability_violations(
        self,
        doc: ManifestDocument,
        *,
        engine_type: str,
        bot_type: str,
        script_supported: Optional[bool],
    ) -> list[Violation]:
        """PUT 时把「这引擎能装什么」钉死:fail closed,不是 apply 期静默跳过。"""
        out: list[Violation] = []
        support = self.capabilities(
            engine_type=engine_type,
            bot_type=bot_type,
            script_supported=script_supported,
        )
        section = doc.manifest
        declared_lists = {
            "mcp": section.mcp,
            "resources": section.resources,
            "skills": section.skills,
            "identity": section.identity,
            "cli_tools": section.cli_tools,
        }
        for category, entries in declared_lists.items():
            if entries and not support.categories.get(category):
                out.append(
                    Violation(
                        entry=f"manifest.{category}",
                        rule="category-unsupported",
                        message=support.reasons.get(
                            category, f"category {category} unsupported"
                        ),
                    )
                )
        if section.engine_config is not None and not support.categories.get(
            "engine_config"
        ):
            out.append(
                Violation(
                    entry="manifest.engine_config",
                    rule="category-unsupported",
                    message=support.reasons.get(
                        "engine_config", "engine_config unsupported"
                    ),
                )
            )
        if doc.script is not None and not support.categories.get("script"):
            out.append(
                Violation(
                    entry="script",
                    rule="script-unsupported",
                    message=support.reasons.get(
                        "script", "script unsupported for this engine"
                    ),
                )
            )
        # identity 的 type 必须在写入时就对着该引擎校验(#1473:不是 apply
        # 时静默跳过)。保留名不在这里硬拒——schema §3.5「如实警示,不做硬
        # 禁止」,真正的硬保护在 apply 侧(#1472 的 D2 例外)。
        whitelist = identity_file_whitelist(engine_type)
        for index, entry in enumerate(section.identity):
            if whitelist is not None and entry.type not in whitelist:
                out.append(
                    Violation(
                        entry=f"identity[{index}]",
                        rule="identity-type-engine",
                        message=f"engine {engine_type!r} does not accept identity "
                        f"type {entry.type!r}",
                    )
                )
        return out

    def _warnings(self, doc: ManifestDocument) -> list[str]:
        """非致命提示:声明了但无人引用的源(#1475:警告不是错误)、
        声明了 apply 永不写入的保留名(#1472 例外,schema §3.5 警示)。"""
        referenced = {
            entry.from_
            for category in ("resources", "skills", "identity")
            for entry in getattr(doc.manifest, category)
            if entry.from_ is not None
        }
        warnings = [
            f"sources.{name} is declared but never referenced by any entry"
            for name in doc.sources
            if name not in referenced
        ]
        warnings.extend(
            f"identity[{index}] declares {entry.type}: apply never writes or "
            f"removes it (reserved by the coverage semantics)"
            for index, entry in enumerate(doc.manifest.identity)
            if entry.type in RESERVED_IDENTITY_FILES
        )
        return warnings
