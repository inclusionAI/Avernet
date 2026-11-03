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
- `ports.py` — the store-backed write targets the `TeclawPlatformDelivery` strategy
  hands the materialisers.

Boundary metadata lives in the parent package's `README.md`
(`core/bot_config_manifest`), which this package is part of.

All configurable identity files, including `IDENTITY.md`, share the same rules:
an absent identity category does nothing, a declared category replaces the file
set, and empty writes delete stored objects. Only `MEMORY.md` is protected from
Manifest declarations and cleanup.

A live Apply composes a complete snapshot from the current platform entities,
including identity files retained by the materialiser. The snapshot does not
depend on which files the latest Apply selected. First provisioning carries
the prepared files, and runtime composes retain engine ownership.
