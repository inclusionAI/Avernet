"""Applying a manifest: the orchestrator, the materialisers, and the record.

Deliberately empty of imports, for the reason ``core/bot_config_surface``
records about its own package: :mod:`.outcomes` and :mod:`.order` are leaves
that the materialisers import, while :mod:`.registry` imports *from* those
materialisers — so re-exporting either here would make importing a leaf trigger
the registry, and the cycle that follows is not worth the saved keystrokes.
Import the submodules by name.
"""
