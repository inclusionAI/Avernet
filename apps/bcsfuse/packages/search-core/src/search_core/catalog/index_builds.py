"""Shared mapped-build validation; hosts supply source conversion and defaults."""

from dataclasses import replace

from search_core.api.cards import UNSET_MAPPING
from search_core.config.document_mapping import DocumentMapping, MappingError
from search_core.core.index_builds import IndexBuildService


class MappedCardIndexBuildService(IndexBuildService):
    def __init__(
        self,
        repository,
        source,
        builds,
        embedding_factory,
        *,
        project,
        mandatory_filter_fields=(),
        **kwargs,
    ):
        self.repository, self.project = repository, project
        self.mandatory_filter_fields = frozenset(mandatory_filter_fields)
        super().__init__(source, builds, embedding_factory, **kwargs)

    def create(
        self,
        build_id,
        model,
        dimension,
        *,
        filter_fields=None,
        document_mapping=UNSET_MAPPING,
        validation_card_ids=(),
    ):
        previous = self.builds.get(build_id)
        if document_mapping is UNSET_MAPPING:
            mapping = previous.config.document_mapping if previous else None
        else:
            mapping = DocumentMapping.model_validate(document_mapping or {})
            if not mapping.stored():
                mapping = None
        if mapping is not None:
            protected = set(mapping.filter_fields) & self.mandatory_filter_fields
            if protected:
                raise MappingError(
                    ("filter_fields", min(protected)), "business filter is protected"
                )
            if filter_fields is not None:
                raise MappingError(
                    ("filter_fields",),
                    "use document_mapping.filter_fields alone for mapped builds",
                )
            filter_fields = set(mapping.filter_fields) | self.mandatory_filter_fields
        elif filter_fields is not None:
            filter_fields = set(filter_fields) | self.mandatory_filter_fields
        warnings = []
        if mapping is not None:
            for card_id in validation_card_ids:
                row = self.repository.read_card(card_id)
                if row is None or row.card.deleted:
                    raise MappingError(
                        (), f"validation Card {card_id!r} does not exist"
                    )
                try:
                    _, found = self.project(row.card, {}, mapping, filter_fields)
                except MappingError as error:
                    raise MappingError(
                        error.location,
                        f"Card {card_id}: {error}",
                        card_path=error.card_path,
                        code=error.code,
                    ) from error
                warnings.extend({**warning, "card_id": card_id} for warning in found)
            if not validation_card_ids:
                warnings.append(
                    {
                        "code": "NO_VALIDATION_SAMPLE",
                        "message": (
                            "syntax validated; field values have not been "
                            "checked against a Card"
                        ),
                    }
                )
        record = super().create(
            build_id,
            model,
            dimension,
            filter_fields=filter_fields,
            document_mapping=mapping,
        )
        return replace(record, mapping_warnings=tuple(warnings))
