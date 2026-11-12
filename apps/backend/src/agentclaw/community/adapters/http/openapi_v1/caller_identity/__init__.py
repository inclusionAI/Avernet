"""Public Caller identity configuration endpoints and authorization policy.

The HTTP composition root imports the router explicitly so the policy table can
be loaded before route construction without initializing the HTTP stack.
"""
