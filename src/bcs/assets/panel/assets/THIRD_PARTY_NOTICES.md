# Third-party asset notices

## Kenney RPG Urban Pack 1.0

- Source: https://kenney.nl/assets/rpg-urban-pack
- Author/distributor: Kenney
- License: Creative Commons Zero (CC0 1.0)
- Used files: character tiles `0024`, `0051`, `0078`, `0105`, `0132`, `0159`, `0186`, and `0213`; chair tile `0274`. Unused wall, floor and table source references have been removed.
- Changes: embedded into curated room and character atlases; character pixels are integer-scaled, chair directions use 90° rotations, and fixed presentation overlays do not depend on secret game state.
- Retained license: `assets/source/kenney-rpg-urban/License.txt`

## Kenney Roguelike/RPG Pack

- Source: https://kenney.nl/assets/roguelike-rpg-pack
- Author/distributor: Kenney (with Lynn Evers)
- License: Creative Commons Zero (CC0 1.0)
- Used cells: `roguelikeSheet_transparent.png` row 5/column 24 and row 9/column 31, using the included 16×16 grid with 1px margin and spacing.
- Changes: selected cells were extracted, embedded into the room atlas, and surrounded by original room bridge art.
- Retained license: `assets/source/kenney-roguelike-rpg/License.txt`

Only the listed source pixels and license evidence are retained. The downloaded ZIP archives and unused bulk sheets are not part of the repository or published package.

## Original club artwork

The paneled wall, parquet floor, brass-trimmed felt table, night window, room rug, sconces, table cards, club sign, and finale trophy are original SVG/CSS geometry under the package Apache-2.0 license. Atlas geometry is reproducible from `scripts/generate-atlases.mjs`; scene decorations live in `src/undercover-game/RoomScene.tsx` and the trophy in `src/undercover-game/GameOverDialog.tsx`. No additional third-party asset pack was needed.

The original `src/undercover-game/assets/speech-frame.svg` and `speech-tail.svg` provide the stretchable pixel speech frame and directional tail, under the package Apache-2.0 license. They are embedded into the UMD with no runtime downloads.

The wall-side host uses the original pixel knight sculpture in `src/undercover-game/assets/knight-statue.svg`: stone armor, shield, sword and pedestal, licensed under the package Apache-2.0 license. The unused crowned host and wall-frame source pixels and atlas cells have been removed.
