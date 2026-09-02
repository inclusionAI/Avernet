# Managed files — the platform's own copy of a teclaw bot's manifest-delivered files (W8)

On teclaw the artifact is the delivery, so what a manifest applies has to live
somewhere the composer can read. This package is that place: bytes in the
bot-data object store under the promotion key layout with a `_manifest`
segment, one index row per file in `ac_bot_config_managed_files`, and one door
(`ManagedFilesStore`) that writes the object before the row.

- `store.py` — `ManagedFilesStore`: put / delete / list / purge / read, and the
  key layout.
- `reader.py` — `ManagedFilesComposeReader`: what the teclaw composer asks —
  which categories the platform asserts for a bot (its manifest's declared file
  categories, when the switch is on), and the collector-shaped refs the index
  holds for them.
- `ports.py` — the store-backed write targets the `TeclawDelivery` strategy
  hands the materialisers.

Boundary metadata lives in the parent package's `README.md`
(`core/bot_config_manifest`), which this package is part of.
