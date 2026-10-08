"""The code that actually writes each construct.

One module per construct, each holding the three stages and nothing about
ordering, aborting or reporting — those belong to the orchestrator, once, for
every category.

Six ship today. What each keys its entries by, and what its ``Intent.value``
carries:

=================  =================  ====================================
Module             ``identity`` from  ``Intent.value``
=================  =================  ====================================
``script``         fixed ``"script"`` the substituted body ``str``, or
                                      ``None`` for a declared-empty section
``mcp``            ``server_code``    the server code again
``identity``       ``type``           the decoded text body, a ``str``
``skills``         ``name``           a ``_SkillPackage``
``resources``      ``path``           the member's ``bytes``, or the
                                      ``_DECLARED_TREE`` marker
``cli_tools``      ``name``           a ``CliToolDecl``
=================  =================  ====================================

``script`` and ``mcp`` fetch nothing. ``identity``, ``skills`` and
``resources`` fetch in ``resolve``, through the one ``DeclaredSourceResolver`` funnel.
``cli_tools`` is the exception: it fetches in ``write``, because the service
it translates for owns fetching for both of its callers.

``engine_config`` has no module: a document declaring it takes the
orchestrator's no-materialiser path.
"""
