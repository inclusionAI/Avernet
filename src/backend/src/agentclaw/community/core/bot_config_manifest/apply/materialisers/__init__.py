"""The code that actually writes each construct.

One module per construct, each holding the three stages and nothing about
ordering, aborting or reporting — those belong to the orchestrator, once, for
every category.

This wave has two: ``script`` and ``mcp``, the only constructs whose
materialisation needs no fetched bytes. ``skills`` and ``identity`` arrive with
W5, ``resources`` with W6, ``engine_config`` when X2/T3 lets it back in, and
``cli_tools`` with W9.
"""
