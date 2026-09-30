"""Deploy artifact producers — the swappable 'produce the build snapshot' strategy.

Selected by ``device_provider`` at the publish **build** phase (mirrors
``DeviceServiceRouter``). ARCA snapshots the container workspace via the existing
``bot_build_service.build()``; the external path composes from DB+NAS. Both feed
the unchanged versioned publish/binding data model — verify/online are untouched.

These are **core strategies**, not plugins (they orchestrate; boundary-crossing
bits go through plugins). No ``local/``/``prod/`` split.
"""
