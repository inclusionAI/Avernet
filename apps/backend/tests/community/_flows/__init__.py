"""Single-source business flows — data, not tests.

Each module gets a tests/_flows/<module>/ package of FlowCase constants. They
are imported by both the route-A in-process executor (tests/e2e/) and the
route-B live-backend executor (tests/acceptance/), so one flow definition is
exercised two ways. Nothing here imports pytest or runs assertions itself.
"""
