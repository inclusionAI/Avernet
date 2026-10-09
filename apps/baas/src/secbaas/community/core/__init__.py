"""Core domain layer — business logic, services, repositories, and cron jobs.

Implements Service API Protocols (``secbaas.community.api``).  Must be transport-agnostic:
no adapters, no web frameworks.  Depends on ``spi`` for pluggable infrastructure
and ``domain`` for domain models and state machines.
"""
