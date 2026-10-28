"""Deterministic local-dev app seeding (DEV / SINGLE-BOX ONLY).

The single-box dev stack runs the gateway on an in-memory app registry:
nothing persists, and a local service that must authenticate as a trusted
App (the singlebox Backend's manager-sync sender) needs a *reproducible*
credential to present. The admin ``POST /apps`` endpoint cannot provide
one — the registrar mints a random key a caller must then hand to another
process — so the seed writes the row directly at boot from configuration
(``user_config.local_dev_apps``), storing the same PBKDF2 hash + prefix
the registrar stores, so verification runs the identical ``_by_api_key``
path a production credential takes.

Discipline, so this can never become a production backdoor:

- The section is opt-in by *absence*: an empty list (the default, and
  every production configuration) seeds nothing and this module is a
  no-op.
- Every seeded entry logs loudly at WARNING — a seeded dev app is never
  silent.
- The ``api_key`` value is configuration's responsibility; a value that
  fails the API-key format check is an ERROR and a skip, not a quiet
  legacy-JWT fallback — the deprecated plaintext-token path is never
  seeded.
- Name collisions are idempotent skips (the row a previous boot wrote
  into a persistent database may still exist), but a collision whose
  prefix disagrees with the configured key is an ERROR: silently
  keeping the old row would make the configured credential not work
  with no visible reason.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from gateway.community.logger import get_logger

from ._key_gen import APIKeyGenerator
from ._orm import API_KEY_PREFIX_LEN, AppRow

logger = get_logger("core-app-dev-seed")

#: Audit attribution for seeded rows — never empty, always synthetic.
_DEV_SEED_CREATOR = "local-dev-seed"


def seed_local_dev_apps(db, user_config) -> int:
    """Seed the configured local dev apps into ``avernet_application``.

    Synchronous by design: ``bootstrap_app()`` runs before any event loop,
    and the seed is a bounded startup step (one PBKDF2 hash per entry).
    Returns the number of rows actually inserted (0 for an empty or fully
    skipped configuration).

    Never fatal to the boot: a dev-seed configuration problem is an
    ERROR and a skip, never a crashed gateway. The seed owns its one
    table (``avernet_application``) — a bare local run may have no schema
    at all (``database.create_schema: false``), so the table is created
    first when absent; a deployment with real migrations keeps its
    migrated table (``checkfirst``) untouched.
    """
    scheduled = getattr(user_config, "local_dev_apps", None) or []
    if not scheduled:
        return 0
    logger.warning(
        "[DEV-ONLY] seeding %d local dev test app(s) from "
        "user_config.local_dev_apps — NOT FOR PRODUCTION: %s",
        len(scheduled),
        ", ".join(sorted({app.app_name for app in scheduled})),
    )
    inserted = 0
    for app in scheduled:
        if not APIKeyGenerator.validate_format(app.api_key):
            # Never fall back to the legacy plaintext-token path: a seed
            # that cannot be verified the way production keys are is a
            # configuration error, not a dev convenience.
            logger.error(
                "[DEV-ONLY] local_dev_apps entry %r has an api_key that is "
                "not the 32-char base62 API-key shape — skipping the seed",
                app.app_name,
            )
            continue
        if _seed_one(db, app):
            inserted += 1
    return inserted


def _seed_one(db, app) -> bool:
    """Insert one seeded app row (idempotent on a persistent database)."""
    prefix = app.api_key[:API_KEY_PREFIX_LEN]
    try:
        with db.orm_session() as session:
            # A bare local run may carry no schema at all
            # (``database.create_schema: false``); the seed owns exactly
            # this one table, so it creates it when absent and never
            # touches a migrated one (``checkfirst``).
            engine = session.get_bind()
            AppRow.__table__.create(engine, checkfirst=True)
            existing = session.scalar(
                select(AppRow).where(AppRow.app_name == app.app_name, AppRow.env == "")
            )
            if existing is not None:
                if existing.api_key_prefix == prefix:
                    logger.info(
                        "[DEV-ONLY] local dev app %r already seeded — skipping",
                        app.app_name,
                    )
                else:
                    logger.error(
                        "[DEV-ONLY] local dev app %r exists with a different "
                        "credential prefix — change app_name to re-seed, or "
                        "restore the api_key the row was seeded with",
                        app.app_name,
                    )
                return False
            row = AppRow(
                api_key_hash=APIKeyGenerator.hash_key(app.api_key),
                api_key_prefix=prefix,
                app_name=app.app_name,
                app_type=app.app_type,
                owners=app.owners,
                tenant=app.tenant,
                status="ACTIVE",
                env="",
                config={},
                creator=_DEV_SEED_CREATOR,
                modifier=_DEV_SEED_CREATOR,
            )
            session.add(row)
            session.flush()
    except SQLAlchemyError:
        # A concurrent seeder or a unique-key collision — the unique
        # indexes are the real guarantee; classify rather than crash the
        # boot. A dev-seed problem must never take the gateway down.
        logger.error(
            "[DEV-ONLY] seeding local dev app %r hit a database "
            "constraint — skipped; inspect the registry",
            app.app_name,
        )
        return False
    logger.warning(
        "[DEV-ONLY] local dev test app seeded (registry-backed identity on "
        "the app_token strategy): app_name=%s tenant=%s — NOT FOR PRODUCTION",
        app.app_name,
        app.tenant,
    )
    return True
