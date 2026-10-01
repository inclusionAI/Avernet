"""Backend Plugin APIs (capability contracts).

Per the Microkernel Architecture Constitution (Rules 3, 5, 20), each
module here declares ONE Plugin API: a capability the backend depends
on, expressed as a ``typing.Protocol``. Concrete Plugins live in
``agentclaw.community.plugins.local`` (singlebox / dev) and
``agentclaw.corp.plugins.prod`` (production / company-network).

These are placeholders for the migration; the existing concrete code under
``agentclaw.infrastructure`` remains authoritative until each Plugin is
moved.
"""
