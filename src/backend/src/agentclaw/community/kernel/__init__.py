"""Kernel — the lowest layer of the backend.

Houses cross-cutting contracts that any other layer (``api/``, ``core/``,
``plugin_api/``, ``plugins/``) may import. The kernel itself imports
nothing from ``agentclaw.*`` — arch-tested in
:mod:`tests.architecture.test_kernel_no_imports`.

Today's content is the :class:`Lifecycle` Protocol (Rule 11). Future
kernel-level contracts can land here as they're identified.
"""
