"""TC defaults around shared, isolated Card index builds and startup selection."""

import re
from contextlib import contextmanager

from search_core.api.index_builds import IndexBuildConflict
from search_core.catalog.cards import business_card
from search_core.catalog.index_builds import MappedCardIndexBuildService
from search_core.catalog.mapping import map_projection
from search_core.config.document_mapping import effective_mapping
from search_core.config.index_builds import IndexVersionConfig
from search_core.core.source_rebuild import rebuild_source
from search_core.providers.demo_embedding import DemoEmbedding
from search_core.providers.embedding import RealEmbeddingProvider
from search_core.providers.sql_index_builds import SqlIndexBuildRepository

from app.tc.card_profiles import TcCardUpdates
from app.tc.catalog import FILTER_FIELDS, TcDocumentConverter


def project(card, config, mapping, filter_fields):
    rule = effective_mapping(mapping, (config or {}).get("document_mapping"))
    return map_projection(
        business_card(card),
        rule,
        TcDocumentConverter().convert(card),
        protected_filter_fields=FILTER_FIELDS,
    )


def embedding_factory(current, embedding_key):
    @contextmanager
    def create(model, dimension):
        if f"{model}/{dimension}" == embedding_key:
            yield current
            return
        if isinstance(current, DemoEmbedding):
            yield DemoEmbedding(dimension)
            return
        if not isinstance(current, RealEmbeddingProvider):
            raise TypeError("this embedding provider does not support alternate models")
        provider = RealEmbeddingProvider(
            current._settings.model_copy(
                update={
                    "model": model,
                    "dimension": dimension,
                    "request_dimensions": True,
                }
            ),
            max_retries=0,
            request_interval_ms=0,
        )
        try:
            yield provider
        finally:
            provider.close()

    return create


class TcBuildSource:
    def __init__(self, repository, index, profile_gateway):
        self.repository, self.index, self.profile_gateway = (
            repository,
            index,
            profile_gateway,
        )

    def page(self, after, limit):
        return self.repository.page_states(after, limit)

    def prepared_revision(self, index_key, source_id):
        doc = self.repository.index_documents(index_key).load_by_id(source_id)
        return None if doc is None else doc.source_revision

    def rebuild(self, source_id, config, model, lease_seconds):
        # This builder only publishes SQL Docs for its own key. It never runs
        # the index apply loop against the live Qdrant instance.
        updates = TcCardUpdates(
            self.repository,
            model,
            self.index,
            embedding_key=config.embedding_key,
            instance_id="tc-index-build",
            index_key=config.index_key,
            filter_fields=frozenset(config.filter_fields),
            document_converter=TcDocumentConverter(),
            document_mapping=config.document_mapping,
            protected_filter_fields=FILTER_FIELDS,
            profile_gateway=self.profile_gateway,
        )
        store = self.repository.index_documents(config.index_key)

        def prepare(row):
            previous = store.load_vector_candidates(
                row.card.agent_id, config.embedding_key
            )
            return updates._prepare_documents(row, previous)

        rebuild_source(
            self.repository,
            source_id,
            index_key=config.index_key,
            read_source=self.repository.read_card,
            prepare_documents=prepare,
            lease_seconds=lease_seconds,
        )


class TcIndexBuildService(MappedCardIndexBuildService):
    def __init__(
        self, repository, index, embedding, embedding_key, *, profile_gateway=None
    ):
        super().__init__(
            repository,
            TcBuildSource(repository, index, profile_gateway),
            SqlIndexBuildRepository(
                repository.connect,
                environment=repository.environment,
                placeholder=repository.placeholder,
            ),
            embedding_factory(embedding, embedding_key),
            project=project,
            mandatory_filter_fields=FILTER_FIELDS,
            filter_fields=FILTER_FIELDS,
            conversion_version=TcDocumentConverter.version,
        )

    def create(self, build_id, model, dimension, **options):
        if build_id == "default" or re.fullmatch(r"[0-9a-f]{64}", build_id):
            raise IndexBuildConflict(
                "the existing default index cannot be rebuilt in place"
            )
        return super().create(build_id, model, dimension, **options)


def requested_version(context):
    drm = context.registry.get("drm_config")
    value = drm.get("index_version") if drm is not None else None
    if value is None:
        value = context.config.get("index_version")
    if value is None or value == "":
        return IndexVersionConfig()
    return (
        IndexVersionConfig.model_validate_json(value)
        if isinstance(value, str)
        else IndexVersionConfig.model_validate(value)
    )
