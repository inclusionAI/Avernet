"""Pure validation for the optional task-search viewer scope."""

from __future__ import annotations

from typing import Literal, TypedDict, cast

SearchViewerType = Literal["bot", "human"]


class SearchViewerOptions(TypedDict, total=False):
    viewer_actor_type: SearchViewerType
    viewer_actor_id: str


def normalize_search_viewer(
    viewer_actor_type: SearchViewerType | None = None,
    viewer_actor_id: str | None = None,
) -> SearchViewerOptions:
    """Return no added kwargs for old callers; never infer a viewer identity."""
    if viewer_actor_type is None and viewer_actor_id is None:
        return {}
    if (
        not isinstance(viewer_actor_type, str)
        or viewer_actor_type not in {"bot", "human"}
        or not isinstance(viewer_actor_id, str)
        or not viewer_actor_id.strip()
    ):
        raise ValueError(
            "viewer_actor_type (bot|human) and non-empty viewer_actor_id are required together"
        )
    return {
        "viewer_actor_type": cast(SearchViewerType, viewer_actor_type),
        "viewer_actor_id": viewer_actor_id.strip(),
    }
