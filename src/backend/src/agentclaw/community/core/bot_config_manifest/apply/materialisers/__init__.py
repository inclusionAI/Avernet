"""The code that actually writes each construct.

One module per construct, each holding the three stages and nothing about
ordering, aborting or reporting — those belong to the orchestrator, once, for
every category.

Six ship today: ``script`` and ``mcp`` (W4, fetch-free — registry entries plus
a plain row write), ``skills`` and ``identity`` (W5, the two fetching
materialisers), ``resources`` (W6, the one write chain with tree-replacement
semantics) and ``cli_tools`` (W9, a translator over ``CliToolService``, which
the management API calls too). ``engine_config`` arrives when X2/T3 lets it
back in.
"""
