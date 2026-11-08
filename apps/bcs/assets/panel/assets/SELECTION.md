# Pixel asset selection

Audited 2026-09-04 from the official Kenney downloads; updated **2026-11-04** for
the Avernet #2608 night-banquet UI delivery.

## Selected source pixels

| Production use | Pack | Exact source | Selection / transformation |
| --- | --- | --- | --- |
| Night-banquet hall background | Avernet #2608 in-project delivery | `bg-hall.png` (1672×941 clean background) | Downscaled 2:1 to 836×470 with Pillow `LANCZOS`, encoded WebP quality 72 (≤24 KiB), embedded as one `data:image/webp;base64` image in `night-hall.svg`. |
| Player portraits, normal and eliminated | Avernet #2608 in-project delivery | `char-1.png` … `char-6.png`; `char-1-eliminated.png` … `char-6-eliminated.png` | Sprite-local square crop → 80×80 `NEAREST` resize → `MEDIANCUT` quantize to ≤128 colors, no dither; embedded in `night-portraits.svg` at indices 0–11. |
| Judge/host portrait | Avernet #2608 in-project delivery | `char-judge.png` (200×240) | Crop `(72, 0, 60)` → 80×80 `NEAREST` → ≤128-color quantize; `night-portraits.svg` index 12. |
| Lifecycle/action sprites | Original geometry | `scripts/generate-atlases.mjs` | Viewer, action, speaking, completed, voted, eliminated, retrying, error, bubble, secret card, and vote card in the retained `ui-atlas.svg`. |
| Speech frame and tail | Original geometry | `speech-frame.svg`, `speech-tail.svg` | Retained nine-slice frame and directional tail, unchanged. |

Exact night-banquet source files, the final crop boxes `(x, y, size)`, and the
one-off Pillow derivation command are documented in
`assets/source/night-banquet/README.md`. The derived rasters are committed under
`assets/source/night-banquet/derived/` so the atlases are deterministic and do
not need Pillow at build time.

## Replaced or retired references

- **Kenney RPG Urban Pack 1.0** — character tiles `0024`, `0051`, `0078`, `0105`,
  `0132`, `0159`, `0186`, `0213` and chair tile `0274` previously formed the room
  and character atlases. Both atlases are retired and replaced by the
  night-banquet hall and portraits; the source tiles and CC0 license remain
  under `assets/source/kenney-rpg-urban/` as provenance evidence.
- **Kenney Roguelike/RPG Pack** — the cabinet and plant cells previously used in
  the room atlas are retired with it. The extracted source cells and CC0 license
  remain under `assets/source/kenney-roguelike-rpg/` as provenance evidence.
- **Original knight sculpture** — `knight-statue.svg` is retired and removed with
  the room/character atlases.
- **`char-2-speaking.png`** — not selected; the speaking state is CSS-only.

## Rejected or deferred references

- **Kenney Tiny Town** — outdoor town construction; deferred because it does not supply the indoor dollhouse projection.
- **Kenney Isometric Tiles City** — isometric projection; deferred because it would require a different scene and interaction geometry.
- **Kenney Fantasy Town Kit** — 3D/low-poly; rejected for the pixel-room direction.
- **OpenGameArt Open World Tileset** — outdoor terrain and a different palette/outline; reference only.
- **OpenGameArt RPG character sheets** — inconsistent dimensions and animation conventions; fallback only.

No file from a rejected or deferred source is present under `assets/source` or
`src/undercover-game/assets`.

## Why compact rasters

The panel ships as one UMD file that embeds every image and makes no runtime
image request. The full 1672×941 background is too large to embed, so runtime
uses the 836×470 WebP (22,314 bytes) and 80×80 portrait medallions (13,513 bytes
for all thirteen) instead of a full-size scene plus full-body sprites. All five
production atlases together stay within the 120 KiB budget enforced by
`scripts/asset-scan.mjs`.
