# Third-party asset notices

## Night-banquet undercover art (original in-project artwork)

- Origin: Avernet issue **#2608** night-banquet JRPG UI delivery, dated
  **2026-11-04**.
- Author/distributor: delivered with the Avernet #2608 UI work for this project
  (not a third-party asset pack).
- License: treated as in-project artwork under the package **Apache-2.0**; the
  #2608 owner should confirm the licensing statement before publishing the art
  outside the project.
- Used files (`assets/source/night-banquet/`): `bg-hall.png` (clean 1672×941
  background), `char-1.png` … `char-6.png`, `char-1-eliminated.png` …
  `char-6-eliminated.png`, and `char-judge.png`. `char-2-speaking.png` is not
  used; the speaking state is CSS-only.
- Changes: the background is downscaled 2:1 to 836×470 with `Image.LANCZOS` and
  encoded as WebP quality 72; each portrait is cropped square, resized to 80×80
  with `Image.NEAREST`, and palette-quantized to at most 128 colors. Both are
  embedded as base64 `data:` URIs in `night-hall.svg` and
  `night-portraits.svg`; there is no runtime image request.
- Provenance and exact crop boxes: `assets/source/night-banquet/README.md`.

## Kenney RPG Urban Pack 1.0 (retired)

- Source: https://kenney.nl/assets/rpg-urban-pack
- Author/distributor: Kenney
- License: Creative Commons Zero (CC0 1.0)
- Retained source files: character tiles `0024`, `0051`, `0078`, `0105`,
  `0132`, `0159`, `0186`, and `0213`; chair tile `0274`.
- Status: **retired**. The Kenney room and character atlases were replaced by
  the night-banquet hall and portraits, and no Kenney pixel is embedded in the
  production UMD after this integration. The source tiles and license remain
  under `assets/source/kenney-rpg-urban/` as provenance evidence.
- Retained license: `assets/source/kenney-rpg-urban/License.txt`

## Kenney Roguelike/RPG Pack (retired)

- Source: https://kenney.nl/assets/roguelike-rpg-pack
- Author/distributor: Kenney (with Lynn Evers)
- License: Creative Commons Zero (CC0 1.0)
- Retained source cells: `roguelikeSheet_transparent.png` row 5/column 24 and
  row 9/column 31, using the included 16×16 grid with 1px margin and spacing,
  extracted as `cabinet-r5c24.png` and `plant-r9c31.png`.
- Status: **retired**. These cells were only used by the retired room atlas and
  are no longer embedded; the source cells and license remain as provenance
  evidence.
- Retained license: `assets/source/kenney-roguelike-rpg/License.txt`

Only the listed source pixels and license evidence are retained. The downloaded
ZIP archives and unused bulk sheets are not part of the repository or published
package.

## Original panel artwork

The small lifecycle and action sprites in `src/undercover-game/assets/ui-atlas.svg`
(viewer, action, speaking, completed, voted, eliminated, retrying, error, bubble,
secret card, vote card) are original SVG geometry under the package Apache-2.0
license. The original stretchable pixel speech frame and directional tail in
`src/undercover-game/assets/speech-frame.svg` and `speech-tail.svg` are likewise
under the package Apache-2.0 license.

The night-banquet hall and portraits are original in-project artwork under the
same license (see the first section). The retired Kenney room/character atlases
and the retired knight sculpture have been removed; no third-party asset pack is
needed by the scene. All atlases are reproducible from
`scripts/generate-atlases.mjs` and, for the night-banquet rasters, the derivation
documented in `assets/source/night-banquet/README.md`.
