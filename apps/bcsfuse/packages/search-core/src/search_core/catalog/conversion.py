"""A small default projection; applications may supply their own converter."""

from search_core.catalog.cards import business_card
from search_core.plugin_api.card_documents import CardProjection
from search_core.plugin_api.index_documents import DocumentText


class DefaultCardDocumentConverter:
    version = "default-card-v1"

    def __init__(self, *, filter_fields=frozenset()):
        self.filter_fields = frozenset(filter_fields)

    def convert(self, card):
        source = business_card(card)
        parts = []
        for name in ("name", "description"):
            value = source.get(name)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        skills = source.get("skills")
        for skill in skills if isinstance(skills, list) else ():
            if not isinstance(skill, dict):
                continue
            for name in ("name", "description"):
                value = skill.get(name)
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
            tags = skill.get("tags")
            if isinstance(tags, list):
                parts.extend(
                    v.strip() for v in tags if isinstance(v, str) and v.strip()
                )
        text = "\n".join(parts)
        if not text:
            raise ValueError(
                "default converter requires name, description or skill text"
            )
        labels = {}
        for key in sorted(self.filter_fields):
            value = card.resource_type if key == "resource_type" else source.get(key)
            if value is None:
                continue
            values = [value] if isinstance(value, str) else value
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(v, str) or not v.strip() for v in values)
            ):
                raise ValueError("filter labels must be nonempty strings")
            labels[key] = tuple(dict.fromkeys(v.strip() for v in values))
        return CardProjection([DocumentText("main", text, text)], labels)
