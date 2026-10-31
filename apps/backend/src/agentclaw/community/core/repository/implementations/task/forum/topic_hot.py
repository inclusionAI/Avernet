"""Shared serving-side Topic hot-list classification rules."""

from __future__ import annotations

from datetime import datetime, timezone


def classify_topic_hot(
    *,
    created_at: datetime,
    latest_reply_at: datetime | None,
    latest_favorite_at: datetime | None,
    latest_like_at: datetime | None,
    reply_participant_count: int,
    favorite_user_count: int,
    like_user_count: int,
) -> bool:
    """Classify a Topic with the serving-side hot-list gate.

    The three caller-behavior dimensions are replies, favorites, and likes. The
    gate measures whether at least two dimensions exist and whether the weighted
    score reaches 15 within 72 hours.
    """
    latest_hot_at = max(
        (
            created_at,
            latest_reply_at,
            latest_favorite_at,
            latest_like_at,
        ),
        key=lambda value: created_at if value is None else value,
    )
    age_hours = max(
        0.0,
        (
            datetime.now(timezone.utc)
            - latest_hot_at.replace(tzinfo=timezone.utc)
        ).total_seconds()
        / 3600,
    )
    interaction_kind_count = sum(
        count > 0
        for count in (
            reply_participant_count,
            favorite_user_count,
            like_user_count,
        )
    )
    heat_equivalent = (
        reply_participant_count
        + 3 * favorite_user_count
        + 2 * like_user_count
    )
    return (
        age_hours <= 72
        and interaction_kind_count >= 2
        and heat_equivalent >= 15
    )
