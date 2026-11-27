"""Bot team product facade over versioned Todo template management."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from injector import inject
import yaml

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    SwarmStagesPolicy,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    get_profile,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    MethodCompositionTemplate,
    MethodTemplate,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.contracts import (
    ProductProfileServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol
from agentclaw.community.core.task.todos.bot_scope import resolve_todo_bot
from agentclaw.community.core.task.todos.bot_team_models import (
    BotTeamDefinition,
    BotTeamDetail,
    BotTeamNode,
    BotTeamPage,
    BotTeamRecord,
    BotTeamRevisionRecord,
    BotTeamStatus,
)
from agentclaw.community.core.task.todos.bot_team_service_protocol import (
    TodoBotTeamServiceProtocol,
)
from agentclaw.community.core.task.todos.errors import (
    TodoTemplateStateConflictError,
    TodoTemplateValidationError,
)
from agentclaw.community.core.task.todos.template_management_models import (
    TodoTemplateDetail,
    TodoTemplateRecord,
    TodoTemplateStatus,
    TodoTemplateVersionRecord,
    TodoTemplateVersionStatus,
)
from agentclaw.community.core.task.todos.template_management_protocol import (
    TodoTemplateManagementServiceProtocol,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.task.todos.template_models import (
    TodoCompletionGate,
    TodoParticipantDefinition,
    TodoParticipantReference,
    TodoParticipantRefSource,
    TodoParticipantRequiredAt,
    TodoParticipantSubjectType,
    TodoStageDefinition,
    TodoStageKind,
    TodoTemplateDefinition,
)

_OWNER_KEY = "todo_owner"
_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class BotTeamProductProfileSpec:
    """Protocol-ready ProductProfile input derived from one BotTeam revision."""

    name: str
    composition: MethodCompositionTemplate
    stages: str


class TodoBotTeamProductCompositionFactory:
    """Own the product-layer selection of the protocol composition."""

    def build(self) -> MethodCompositionTemplate:
        return MethodCompositionTemplate(
            (
                MethodTemplate(
                    get_profile("Swarm"),
                    SwarmStagesPolicy(
                        "openapi-bot/v1",
                        "trusted-evidence/v1",
                        "stages",
                    ),
                ),
            )
        )


def build_profile_request_id(tenant: str, env: str, revision_id: str) -> str:
    """Build the stable idempotency key for one revision publication."""

    return f"todo:profile:{tenant}:{env}:{revision_id}:v1"


def build_bot_team_product_profile_spec(
    definition: BotTeamDefinition,
    composition: MethodCompositionTemplate,
) -> BotTeamProductProfileSpec:
    """Translate product nodes into strict linear ProductProfile stage YAML."""

    stages: list[dict[str, object]] = []
    previous: str | None = None
    for node in definition.nodes:
        conditions = [node.completion_gate or "stage output must be complete"]
        stages.append(
            {
                "name": node.code,
                "candidates": [
                    {"type": "bot", "uuid": bot_id}
                    for bot_id in node.bot_ids
                ],
                "depends_on": [] if previous is None else [previous],
                "acceptance": [
                    {"condition": condition} for condition in conditions
                ],
            }
        )
        previous = node.code
    return BotTeamProductProfileSpec(
        name=definition.name,
        composition=composition,
        stages=yaml.safe_dump(
            {"stages": stages},
            sort_keys=False,
            allow_unicode=True,
        ),
    )


class TodoBotTeamService(TodoBotTeamServiceProtocol):
    """Expose Bot team language while delegating storage to Template services."""

    @inject
    def __init__(
        self,
        templates: TodoTemplateManagementServiceProtocol,
        space_access: SpaceAccessServiceProtocol,
        bot_repository: BotRepository,
        product_profiles: ProductProfileServiceProtocol,
        composition_factory: TodoBotTeamProductCompositionFactory,
    ) -> None:
        self._templates = templates
        self._space_access = space_access
        self._bot_repository = bot_repository
        self._product_profiles = product_profiles
        self._composition_factory = composition_factory

    async def create_bot_team(
        self,
        *,
        space_id: int,
        actor_id: str,
        definition: BotTeamDefinition,
        env: str | None = None,
    ) -> BotTeamDetail:
        template_definition = self._to_template_definition(definition)
        await asyncio.to_thread(
            self._require_space_and_bots,
            space_id=space_id,
            actor_id=actor_id,
            nodes=definition.nodes,
            env=env,
        )
        created = await asyncio.to_thread(
            self._templates.create_template,
            space_id=space_id,
            actor_id=actor_id,
            definition=template_definition,
            env=env,
        )
        drafts = tuple(
            version
            for version in created.versions
            if version.status is TodoTemplateVersionStatus.DRAFT
        )
        if len(drafts) != 1:
            raise TodoTemplateStateConflictError(
                "new Bot team must contain exactly one initial draft"
            )
        await self._publish_product_revision(
            template_id=created.template.template_id,
            version=drafts[0],
            actor_id=actor_id,
            definition=definition,
            env=env,
        )
        return self._to_detail(
            await asyncio.to_thread(
                self._templates.get_template,
                template_id=created.template.template_id,
                actor_id=actor_id,
                env=env,
            )
        )

    def list_bot_teams(
        self,
        *,
        space_id: int,
        actor_id: str,
        status: BotTeamStatus | str | None,
        page: int,
        page_size: int,
        env: str | None = None,
    ) -> BotTeamPage:
        templates = self._templates.list_templates(
            space_id=space_id,
            actor_id=actor_id,
            status=self._to_template_status(status),
            page=page,
            page_size=page_size,
            env=env,
        )
        return BotTeamPage(
            total=templates.total,
            items=tuple(self._to_record(item) for item in templates.items),
        )

    def get_bot_team(
        self,
        *,
        bot_team_id: str,
        actor_id: str,
        env: str | None = None,
    ) -> BotTeamDetail:
        return self._to_detail(
            self._templates.get_template(
                template_id=bot_team_id,
                actor_id=actor_id,
                env=env,
            )
        )

    async def update_bot_team(
        self,
        *,
        bot_team_id: str,
        actor_id: str,
        definition: BotTeamDefinition,
        env: str | None = None,
    ) -> BotTeamDetail:
        template_definition = self._to_template_definition(definition)
        current = await asyncio.to_thread(
            self._templates.get_template,
            template_id=bot_team_id,
            actor_id=actor_id,
            env=env,
        )
        await asyncio.to_thread(
            self._require_space_and_bots,
            space_id=current.template.space_id,
            actor_id=actor_id,
            nodes=definition.nodes,
            env=env,
        )
        drafts = tuple(
            version
            for version in current.versions
            if version.status is TodoTemplateVersionStatus.DRAFT
        )
        if len(drafts) > 1:
            raise TodoTemplateStateConflictError(
                "Bot team must not contain multiple mutable drafts"
            )
        draft = drafts[0] if drafts else await asyncio.to_thread(
            self._templates.create_draft,
            template_id=bot_team_id,
            actor_id=actor_id,
            env=env,
        )
        draft = await asyncio.to_thread(
            self._templates.update_draft,
            template_id=bot_team_id,
            template_version_id=draft.template_version_id,
            actor_id=actor_id,
            definition=template_definition,
            env=env,
        )
        await self._publish_product_revision(
            template_id=bot_team_id,
            version=draft,
            actor_id=actor_id,
            definition=definition,
            env=env,
        )
        return self._to_detail(
            await asyncio.to_thread(
                self._templates.get_template,
                template_id=bot_team_id,
                actor_id=actor_id,
                env=env,
            )
        )

    def archive_bot_team(
        self,
        *,
        bot_team_id: str,
        actor_id: str,
        env: str | None = None,
    ) -> BotTeamRecord:
        return self._to_record(
            self._templates.archive_template(
                template_id=bot_team_id,
                actor_id=actor_id,
                env=env,
            )
        )

    def _require_space_and_bots(
        self,
        *,
        space_id: int,
        actor_id: str,
        nodes: tuple[BotTeamNode, ...],
        env: str | None,
    ) -> None:
        space, _member = self._space_access.require_space_member(
            space_id=space_id,
            user_id=actor_id,
            env=env,
        )
        self._require_available_bots(
            space=space,
            actor_id=actor_id,
            nodes=nodes,
        )

    async def _publish_product_revision(
        self,
        *,
        template_id: str,
        version: TodoTemplateVersionRecord,
        actor_id: str,
        definition: BotTeamDefinition,
        env: str | None,
    ) -> TodoTemplateVersionRecord:
        composition = self._composition_factory.build()
        spec = build_bot_team_product_profile_spec(definition, composition)
        profile = await self._product_profiles.create_product_profile(
            spec.name,
            spec.composition,
            spec.stages,
            context=ActorContext(
                PrincipalRef("human", actor_id),
                "todo_product",
            ),
            request_id=build_profile_request_id(
                get_current_avernet_tenant(),
                get_current_env(),
                version.template_version_id,
            ),
        )
        return await asyncio.to_thread(
            self._templates.publish_product_version,
            template_id=template_id,
            template_version_id=version.template_version_id,
            product_profile_id=profile.id,
            actor_id=actor_id,
            env=env,
        )

    def _require_available_bots(
        self,
        *,
        space,
        actor_id: str,
        nodes: tuple[BotTeamNode, ...],
    ) -> None:
        for node in nodes:
            for bot_id in node.bot_ids:
                resolve_todo_bot(
                    bot_repository=self._bot_repository,
                    space=space,
                    actor_id=actor_id,
                    bot_id=bot_id,
                )

    @classmethod
    def _to_template_definition(
        cls,
        definition: BotTeamDefinition,
    ) -> TodoTemplateDefinition:
        cls._validate_definition(definition)
        owner = TodoParticipantReference(
            source=TodoParticipantRefSource.BINDING,
            key=_OWNER_KEY,
        )
        stages: list[TodoStageDefinition] = []
        for node in definition.nodes:
            candidates = tuple(
                TodoParticipantReference(
                    source=TodoParticipantRefSource.FIXED,
                    subject_type=TodoParticipantSubjectType.BOT,
                    subject_id=bot_id,
                )
                for bot_id in node.bot_ids
            )
            stages.append(
                TodoStageDefinition(
                    key=node.code,
                    name=node.name,
                    purpose=node.name,
                    stage_kind=TodoStageKind.BOT,
                    executor_ref=candidates[0],
                    owner_ref=owner,
                    completion_gate=TodoCompletionGate(),
                    executor_candidates=candidates,
                    completion_condition=node.completion_gate,
                )
            )
            if node.approval_required:
                stages.append(
                    TodoStageDefinition(
                        key=f"{node.code}__approval",
                        name=f"{node.name}审批",
                        purpose=f"确认{node.name}结果",
                        stage_kind=TodoStageKind.HUMAN,
                        executor_ref=owner,
                        owner_ref=owner,
                        completion_gate=TodoCompletionGate(),
                    )
                )
        return TodoTemplateDefinition(
            schema_version=_SCHEMA_VERSION,
            name=definition.name,
            description=definition.description,
            binding_schema=(
                TodoParticipantDefinition(
                    key=_OWNER_KEY,
                    label="待办负责人",
                    subject_type=TodoParticipantSubjectType.HUMAN,
                    required_at=TodoParticipantRequiredAt.TODO_CREATE,
                ),
            ),
            stages=tuple(stages),
        )

    @staticmethod
    def _validate_definition(definition: BotTeamDefinition) -> None:
        if not isinstance(definition.name, str) or not definition.name.strip():
            raise TodoTemplateValidationError("Bot team name must not be empty")
        if not isinstance(definition.description, str):
            raise TodoTemplateValidationError("Bot team description must be text")
        if not definition.nodes:
            raise TodoTemplateValidationError("Bot team must define at least one node")
        seen: set[str] = set()
        for node in definition.nodes:
            if not isinstance(node.code, str) or not node.code.strip():
                raise TodoTemplateValidationError("Bot team node code must not be empty")
            if node.code.endswith("__approval"):
                raise TodoTemplateValidationError(
                    "Bot team node code must not use the reserved __approval suffix"
                )
            if node.code in seen:
                raise TodoTemplateValidationError(
                    f"duplicate Bot team node code {node.code!r}"
                )
            seen.add(node.code)
            if not isinstance(node.name, str) or not node.name.strip():
                raise TodoTemplateValidationError(
                    f"Bot team node {node.code!r} name must not be empty"
                )
            if not isinstance(node.bot_ids, tuple) or not node.bot_ids:
                raise TodoTemplateValidationError(
                    f"Bot team node {node.code!r} must select at least one Bot"
                )
            normalized_bot_ids = tuple(
                bot_id.strip()
                for bot_id in node.bot_ids
                if isinstance(bot_id, str) and bot_id.strip()
            )
            if len(normalized_bot_ids) != len(node.bot_ids):
                raise TodoTemplateValidationError(
                    f"Bot team node {node.code!r} bot_ids must be non-empty text"
                )
            if len(set(normalized_bot_ids)) != len(normalized_bot_ids):
                raise TodoTemplateValidationError(
                    f"Bot team node {node.code!r} bot_ids must be unique"
                )
            if not isinstance(node.completion_gate, str):
                raise TodoTemplateValidationError(
                    f"Bot team node {node.code!r} completion_gate must be text"
                )
            if not isinstance(node.approval_required, bool):
                raise TodoTemplateValidationError(
                    f"Bot team node {node.code!r} approval_required must be boolean"
                )

    @classmethod
    def _to_detail(cls, detail: TodoTemplateDetail) -> BotTeamDetail:
        revisions = tuple(
            cls._to_revision(version)
            for version in detail.versions
            if version.status is TodoTemplateVersionStatus.PUBLISHED
        )
        return BotTeamDetail(
            bot_team=cls._to_record(detail.template),
            revisions=revisions,
        )

    @classmethod
    def _to_revision(
        cls,
        version: TodoTemplateVersionRecord,
    ) -> BotTeamRevisionRecord:
        nodes = cls._to_nodes(version.definition)
        if version.published_by is None or version.published_at is None:
            raise TodoTemplateValidationError(
                "Bot team revision must be a published template version"
            )
        return BotTeamRevisionRecord(
            bot_team_revision_id=version.template_version_id,
            bot_team_id=version.template_id,
            revision_no=version.version_no,
            name=version.definition.name,
            description=version.definition.description,
            nodes=nodes,
            definition_hash=version.definition_hash,
            created_by=version.created_by,
            published_by=version.published_by,
            published_at=version.published_at,
            created_at=version.created_at,
            updated_at=version.updated_at,
            product_profile_id=version.product_profile_id,
        )

    @classmethod
    def _to_nodes(
        cls,
        definition: TodoTemplateDefinition,
    ) -> tuple[BotTeamNode, ...]:
        if definition.schema_version != _SCHEMA_VERSION or definition.binding_schema != (
            TodoParticipantDefinition(
                key=_OWNER_KEY,
                label="待办负责人",
                subject_type=TodoParticipantSubjectType.HUMAN,
                required_at=TodoParticipantRequiredAt.TODO_CREATE,
            ),
        ):
            cls._not_representable()
        owner = TodoParticipantReference(
            source=TodoParticipantRefSource.BINDING,
            key=_OWNER_KEY,
        )
        nodes: list[BotTeamNode] = []
        index = 0
        while index < len(definition.stages):
            stage = definition.stages[index]
            if (
                stage.stage_kind is not TodoStageKind.BOT
                or stage.purpose != stage.name
                or stage.owner_ref != owner
                or stage.executor_ref.source is not TodoParticipantRefSource.FIXED
                or stage.executor_ref.subject_type
                is not TodoParticipantSubjectType.BOT
                or stage.executor_ref.key is not None
                or not stage.executor_ref.subject_id
            ):
                cls._not_representable()
            approval_required = False
            if index + 1 < len(definition.stages):
                next_stage = definition.stages[index + 1]
                if next_stage.key == f"{stage.key}__approval":
                    if not cls._is_approval_stage(next_stage, stage, owner):
                        cls._not_representable()
                    approval_required = True
                    index += 1
            candidate_refs = stage.executor_candidates or (stage.executor_ref,)
            bot_ids = tuple(
                candidate.subject_id
                for candidate in candidate_refs
                if candidate.source is TodoParticipantRefSource.FIXED
                and candidate.subject_type is TodoParticipantSubjectType.BOT
                and candidate.subject_id
            )
            if len(bot_ids) != len(candidate_refs):
                cls._not_representable()
            completion_gate = stage.completion_condition
            if not completion_gate and stage.completion_gate.required_artifacts:
                completion_gate = "; ".join(
                    f"artifact {gate.artifact_type} must reach state "
                    f"{gate.artifact_state} with min_count {gate.min_count}"
                    for gate in stage.completion_gate.required_artifacts
                )
            nodes.append(
                BotTeamNode(
                    code=stage.key,
                    name=stage.name,
                    bot_ids=bot_ids,
                    approval_required=approval_required,
                    completion_gate=completion_gate,
                )
            )
            index += 1
        if not nodes:
            cls._not_representable()
        return tuple(nodes)

    @staticmethod
    def _is_approval_stage(
        stage: TodoStageDefinition,
        bot_stage: TodoStageDefinition,
        owner: TodoParticipantReference,
    ) -> bool:
        return (
            stage.key == f"{bot_stage.key}__approval"
            and stage.name == f"{bot_stage.name}审批"
            and stage.purpose == f"确认{bot_stage.name}结果"
            and stage.stage_kind is TodoStageKind.HUMAN
            and stage.executor_ref == owner
            and stage.owner_ref == owner
            and stage.completion_gate == TodoCompletionGate()
        )

    @staticmethod
    def _not_representable() -> None:
        raise TodoTemplateValidationError(
            "Todo template cannot be represented as a Bot team"
        )

    @staticmethod
    def _to_record(template: TodoTemplateRecord) -> BotTeamRecord:
        return BotTeamRecord(
            bot_team_id=template.template_id,
            space_id=template.space_id,
            name=template.name,
            description=template.description,
            status=BotTeamStatus(template.status.value),
            published_revision_id=template.published_version_id,
            created_by=template.created_by,
            updated_by=template.updated_by,
            created_at=template.created_at,
            updated_at=template.updated_at,
        )

    @staticmethod
    def _to_template_status(
        status: BotTeamStatus | str | None,
    ) -> TodoTemplateStatus | str | None:
        if status is None:
            return None
        if isinstance(status, BotTeamStatus):
            return TodoTemplateStatus(status.value)
        try:
            return TodoTemplateStatus(BotTeamStatus(status).value)
        except ValueError as exc:
            raise TodoTemplateStateConflictError(
                f"unsupported Bot team status {status!r}"
            ) from exc
