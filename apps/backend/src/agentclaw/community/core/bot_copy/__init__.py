"""Bot-copy: copy an existing openclaw bot to a new dst bot.

This package owns the operation ledger (`ac_bot_copy`), the `copy` state
producer (`CopyCompiler` — Stage B), the derive-on-read status helper, and the
operator retry service. The creation flow itself is reused from
`core/bot_config_manifest/create_job` via a threaded `state_ref`.
"""
