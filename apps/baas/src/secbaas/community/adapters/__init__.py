"""Delivery adapters — HTTP, WebSocket, and RPC transport for external consumers.

Translates external protocol requests into core service calls.  Thin layer:
no domain policy, no persistence logic.  Wires framework-specific routing
to Service API protocols defined in ``secbaas.community.api``.
"""
