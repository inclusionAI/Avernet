# Managed files — the platform's own copy of a teclaw bot's manifest-delivered files (W8)

On teclaw the artifact is the delivery, so what a manifest applies has to live
somewhere the composer can read. This package is that place: bytes in the
bot-data object store under the promotion key layout with a `_manifest`
segment, and one door (`ManagedFilesStore`) over them. There is no index
table: the key layout is the record, and the store lists the bot's prefix and
reads each file's category and name off its path.

- `store.py` — `ManagedFilesStore`: put / delete / list / purge / read, and the
  key layout (`category_of` / `name_of` are the layout's two rules).
- `reader.py` — `ManagedFilesComposeReader`: what the teclaw composer asks —
  which categories the platform asserts for a bot (its manifest's declared file
  categories, when the switch is on), and the collector-shaped refs the store
  holds for them.
- `ports.py` — the store-backed write targets the `TeclawDelivery` strategy
  hands the materialisers.

Boundary metadata lives in the parent package's `README.md`
(`core/bot_config_manifest`), which this package is part of.
