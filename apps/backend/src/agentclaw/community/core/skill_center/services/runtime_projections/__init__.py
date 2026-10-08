"""Per-engine implementations of ``EngineRuntimeProjection``.

One module per runtime contract, plus the registry that picks between them.
The projector holds the registry and nothing else: which engine behaves how is
recorded here, not tested at the call site.
"""
