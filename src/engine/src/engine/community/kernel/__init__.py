"""Kernel — primitives shared by ``core`` internals and ``plugins``.

Because ``plugins`` must not import ``core`` (the ACL leaf rule),
anything both sides need lives here — below both, importing nothing
internal. The first inhabitant (F2) is the protocol ``frames``
(``RequestFrame`` / ``ResponseFrame`` / ``EventFrame`` / ``ErrorShape`` / …) at
``engine.community.kernel.frames``: it is produced by engine transport (in ``plugins``)
and consumed by ``core`` internals, so it cannot live in ``core``.
"""
