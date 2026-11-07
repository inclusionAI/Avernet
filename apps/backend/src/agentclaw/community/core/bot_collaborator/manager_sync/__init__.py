"""Bot manager sync — projection of collaborator facts onto BCS's manager set.

Pure value types live in ``agentclaw.community.kernel.bot_manager_sync``;
this package owns the projector that turns read-only ``SyncFacts`` into
the desired ``ManagerSnapshot``.
"""