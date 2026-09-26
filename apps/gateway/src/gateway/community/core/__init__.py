"""Core domain layer — business logic, services, repositories.

Implements Service API protocols (``gateway.community.api``).  Must be
transport-agnostic: no adapters, no web frameworks.  Depends on ``spi``
for pluggable infrastructure.
"""
