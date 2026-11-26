"""Exact scoped Profile persistence. Only inserts; never rewrite published history."""

from datetime import datetime, timezone
from hashlib import sha256
import json

from injector import inject
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    ProductCapabilities,
    LegacyProductConfiguration,
    VersionedProfileRef,
)
from agentclaw.community.core.task.context_collab.l6_products.profile_codec import (
    SCHEMA_VERSION,
    encode_profile,
    decode_profile,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.profile_models import (
    ProductProfileModel as Row,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    StoreAxes,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

from agentclaw.community.core.task.context_collab.l6_products.profile_publication import (
    PublishedProductProfile,
)


def profile_axes() -> StoreAxes:
    return StoreAxes(avernet_tenant=get_current_avernet_tenant(), env=get_current_env())


def _read(row) -> PublishedProductProfile:
    definition = decode_profile(row.definition_json)
    canonical = encode_profile(definition)
    if (
        row.definition_schema_version != SCHEMA_VERSION
        or definition.ref != VersionedProfileRef(row.profile_id, row.profile_version)
        or canonical != row.definition_json
        or sha256(canonical.encode()).hexdigest() != row.definition_digest
    ):
        raise ContextCollabValidationError(
            "ProductProfile row identity/digest/schema mismatch"
        )
    actor = json.loads(row.published_by)
    if type(actor) is not dict or set(actor) != {"principal_type", "principal_id"}:
        raise ContextCollabValidationError("invalid ProductProfile publication audit")
    return PublishedProductProfile(
        definition, row.definition_digest, PrincipalRef.from_dict(actor), row.gmt_create
    )


class SqlProductProfileRepository:
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self.db = db

    @staticmethod
    def _query(session, axes):
        return session.query(Row).filter(
            Row.avernet_tenant == axes.avernet_tenant, Row.env == axes.env
        )

    def get(self, product: str, version: int) -> LegacyProductConfiguration:
        ref, axes = VersionedProfileRef(product, version), profile_axes()
        with self.db.orm_session() as session:
            row = (
                self._query(session, axes)
                .filter(
                    Row.profile_id == ref.profile_id, Row.profile_version == ref.version
                )
                .one_or_none()
            )
            if row is None:
                raise ContextCollabValidationError(
                    f"unknown published ProductProfile: {product!r}@{version}"
                )
            return _read(row).definition

    def capabilities(self) -> ProductCapabilities:
        axes = profile_axes()
        with self.db.orm_session() as session:
            profiles = tuple(
                _read(row).definition
                for row in self._query(session, axes).order_by(Row.id).all()
            )
        return ProductCatalog(profiles).capabilities()

    def publish_many(
        self,
        definitions: tuple[LegacyProductConfiguration, ...],
        publisher: PrincipalRef,
        *,
        verify_history: bool = False,
    ) -> tuple[PublishedProductProfile, ...]:
        """Single real transaction. Authorization is the publication service's job.

        Unique-key races are reconciled in a fresh transaction; DB errors other
        than an actual matching/conflicting version are not hidden.
        """
        axes = profile_axes()
        PrincipalRef.from_dict(publisher.to_dict())
        documents = tuple((d, encode_profile(d)) for d in definitions)
        if len({d.ref for d in definitions}) != len(definitions):
            raise ContextCollabValidationError(
                "duplicate profile refs in publication batch"
            )
        try:
            with self.db.transactional_orm_session() as session:
                result = tuple(
                    self._publish(session, axes, d, text, publisher)
                    for d, text in documents
                )
                if verify_history:
                    self._verify_history(session, axes)
                return result
        except IntegrityError:
            with self.db.orm_session() as session:
                result = []
                for definition, text in documents:
                    row = (
                        self._query(session, axes)
                        .filter(
                            Row.profile_id == definition.ref.profile_id,
                            Row.profile_version == definition.ref.version,
                        )
                        .one_or_none()
                    )
                    if row is None:
                        raise  # Not a replayable uniqueness race: preserve the DB error.
                    stored = _read(row)
                    if row.definition_json != text:
                        raise ContextCollabVersionConflict(
                            "published ProductProfile version is immutable"
                        ) from None
                    result.append(stored)
                if verify_history:
                    self._verify_history(session, axes)
                return tuple(result)

    def _verify_history(self, session, axes):
        from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.models import (
            ContextCollabColletModel,
        )
        from agentclaw.community.core.task.context_collab.l6_products.creation_facts import (
            creation_profile_ref,
        )

        available = {
            _read(row).definition.ref for row in self._query(session, axes).all()
        }
        for row in (
            session.query(ContextCollabColletModel)
            .filter(ContextCollabColletModel.env == axes.env)
            .all()
        ):
            facts = json.loads(row.creation_facts)
            ref = creation_profile_ref(facts)
            if ref is not None and ref not in available:
                raise ContextCollabValidationError(
                    f"historical ProductProfile missing from import: {ref}"
                )

    def _publish(self, session, axes, definition, text, publisher):
        row = (
            self._query(session, axes)
            .filter(
                Row.profile_id == definition.ref.profile_id,
                Row.profile_version == definition.ref.version,
            )
            .one_or_none()
        )
        if row is not None:
            stored = _read(row)
            if row.definition_json != text:
                raise ContextCollabVersionConflict(
                    "published ProductProfile version is immutable"
                )
            return stored
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        row = Row(
            profile_id=definition.ref.profile_id,
            profile_version=definition.ref.version,
            definition_schema_version=SCHEMA_VERSION,
            definition_json=text,
            definition_digest=sha256(text.encode()).hexdigest(),
            published_by=json.dumps(publisher.to_dict(), sort_keys=True),
            avernet_tenant=axes.avernet_tenant,
            env=axes.env,
            gmt_create=now,
            gmt_modified=now,
        )
        session.add(row)
        session.flush()
        return _read(row)
