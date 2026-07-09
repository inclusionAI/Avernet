"""config_compose — the backend config composition layer.

Houses the single ``ConfigComposer`` (DB+NAS → ``BotConfigArtifact``), the
store-agnostic source resolver, and the mcporter composer.

Runtime delivery of a composed change is **not** a core strategy here — it is a
transport concern owned by ``device_provider``-keyed device plugins behind the
existing dispatcher (arca/baas/local use the existing device plugins unchanged;
the external/teclaw variant composes the full artifact via ``ConfigComposer`` and
relays it). See the external-container SDD (Task 13/15). ``ConfigComposer`` stays
in core and is consumed by both the build producer and the teclaw device plugin.
"""
