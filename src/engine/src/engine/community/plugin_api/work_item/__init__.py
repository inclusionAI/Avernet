"""Vendor-neutral work-item domain.

Defines the abstract ``WorkItemService`` contract and neutral DTOs shared by
API and engines. Vendor specifics (e.g. the internal product integration) live
only behind ``plugins/prod/`` implementations; this package never imports them.
"""
