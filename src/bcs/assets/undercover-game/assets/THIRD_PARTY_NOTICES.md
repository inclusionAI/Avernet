# Third-party asset notices

## Kenney RPG Urban Pack 1.0

- Source: https://kenney.nl/assets/rpg-urban-pack
- Author/distributor: Kenney
- License: Creative Commons Zero (CC0 1.0)
- Used files: character tiles `0024`, `0051`, `0078`, `0105`, `0132`, `0159`, `0186`, `0213`, and `0240`; chair tile `0274`. Earlier wall (`0117`), floor (`0180`) and table (`0275`) references remain in the source folder but are no longer embedded in the room.
- Changes: embedded into curated room and character atlases; character pixels are integer-scaled, chair directions use 90° rotations, and fixed presentation overlays do not depend on secret game state.
- Retained license: `assets/source/kenney-rpg-urban/License.txt`

## Kenney Roguelike/RPG Pack

- Source: https://kenney.nl/assets/roguelike-rpg-pack
- Author/distributor: Kenney (with Lynn Evers)
- License: Creative Commons Zero (CC0 1.0)
- Used cells: `roguelikeSheet_transparent.png` row 5/column 24, row 9/column 31, and row 15/column 47, using the included 16×16 grid with 1px margin and spacing.
- Changes: selected cells were extracted, embedded into the room atlas, and surrounded by original room bridge art.
- Retained license: `assets/source/kenney-roguelike-rpg/License.txt`

Only the listed source pixels and license evidence are retained. The downloaded ZIP archives and unused bulk sheets are not part of the repository or published package.

## Original club artwork

The paneled wall, parquet floor, brass-trimmed felt table, night window, room rug, sconces, table cards, club sign, and finale trophy are original SVG/CSS geometry under the package Apache-2.0 license. Atlas geometry is reproducible from `scripts/generate-atlases.mjs`; scene decorations live in `src/RoomScene.tsx` and the trophy in `src/GameOverDialog.tsx`. No additional third-party asset pack was needed.
