# Pixel asset selection

Audited 2026-09-04 from the official Kenney downloads. Both archives were staged outside the repository, passed `unzip -t`, and included their original `License.txt` CC0 statements.

## Selected source pixels

| Production use | Pack | Exact source | Selection / transformation |
| --- | --- | --- | --- |
| Bot character variants | RPG Urban Pack 1.0 | `Tiles/tile_0024.png`, `tile_0051.png`, `tile_0078.png`, `tile_0105.png`, `tile_0132.png`, `tile_0159.png`, `tile_0186.png`, `tile_0213.png` | Eight verified front-facing 16×16 people; scaled to 32×32 with nearest-neighbor pixel rendering. |
| Dedicated host character | RPG Urban Pack 1.0 | `Tiles/tile_0240.png` | Verified person sprite with an original crown overlay; never role-dependent. |
| Interior wall | Original geometry | `scripts/generate-atlases.mjs` | Slate panels; replaces the retained Urban wall reference. |
| Interior floor | Original geometry | `scripts/generate-atlases.mjs` | Walnut parquet tile with staggered joins. |
| Table and directional chairs | RPG Urban Pack 1.0 + local bridge geometry | `Tiles/tile_0275.png` palette/reference and `tile_0274.png` | The original stepped brass/felt table replaces the narrow source reference; chair directions use exact 90° rotations. |
| Shelf/cabinet reference | Roguelike Pack | `Spritesheet/roguelikeSheet_transparent.png`, cell row 5 column 24 (16×16, 1px margin/spacing) | Selected cell retained as `cabinet-r5c24.png`; warm wood colors inform the room shelf. |
| Plant reference | Roguelike Pack | sheet cell row 9 column 31 | Selected cell retained as `plant-r9c31.png`; used as a small indoor decoration. |
| Wall frame reference | Roguelike Pack | sheet cell row 15 column 47 | Selected cell retained as `frame-r15c47.png`; retained in the atlas for compatibility; the club composition uses paired windows. |

The podium, rug, window, lamp, clock, shadows, bubbles, secret card, vote card, and lifecycle symbols are small original bridge sprites drawn in `scripts/generate-atlases.mjs` on the same 16px grid. Pixel outlines use hard edges; the original table includes a thin inset decorative stroke.

## Rejected or deferred references

- **Kenney Tiny Town** — outdoor town construction; deferred because it does not supply the indoor dollhouse projection.
- **Kenney Isometric Tiles City** — isometric projection; deferred because it would require a different scene and interaction geometry.
- **Kenney Fantasy Town Kit** — 3D/low-poly; rejected for the pixel-room direction.
- **OpenGameArt Open World Tileset** — outdoor terrain and a different palette/outline; reference only.
- **OpenGameArt RPG character sheets** — inconsistent dimensions and animation conventions; fallback only.

No file from a rejected or deferred source is present under `assets/source` or `src/assets`.
