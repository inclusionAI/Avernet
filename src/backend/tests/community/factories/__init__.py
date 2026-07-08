"""Reusable seed-helper modules (Object-Mother pattern).

Each module corresponds to a domain area (``access``, ``devices``,
``bots``, …) and exposes ``make_*`` helpers that take a :class:`World`
and call real services internally. This keeps test seed code on the
same code path as production writes — domain invariants the services
enforce continue to apply.

Authors import helpers from here in their case files' ``seed``
callables. The framework does not require this layer; it's a
convention that pays off as repetition emerges.
"""
