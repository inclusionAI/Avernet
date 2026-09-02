"""The code that actually writes each construct.

One module per construct, each holding the three stages and nothing about
ordering, aborting or reporting — those belong to the orchestrator, once, for
every category.

Four ship today: ``script`` and ``mcp`` (W4, fetch-free — registry entries
plus a plain row write) and ``skills`` and ``identity`` (W5, the two fetching
materialisers). ``resources`` arrives with W6, ``engine_config`` when X2/T3
lets it back in, and ``cli_tools`` with W9.
"""
