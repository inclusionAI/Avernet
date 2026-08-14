"""Authn strategy plugin implementations.

Each subpackage is one ``AuthStrategy`` (a named way to build a Principal).
Strategies are flavor-agnostic; provider differences live behind the SPIs a
strategy depends on (e.g. ``AuthPlugin``).
"""
