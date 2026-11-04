# Night-banquet source art

Source art for the undercover-game night-banquet scene, retained so the
committed derived rasters and atlases stay reproducible.

## Origin and license

- Origin: Avernet issue **#2608** night-banquet JRPG UI delivery, dated
  **2026-11-04**. Delivered as `who-is-spy-jrpg-night-banquet-clean-bg.zip`
  together with the clean-background handoff note `delivery-2608/01-底图清理.md`.
- Author/distributor: delivered with the Avernet #2608 UI work for this project.
- License: treated as in-project artwork under the package **Apache-2.0**; it is
  not a third-party asset pack and carries no separate license file. The #2608
  owner should confirm the licensing statement before publishing this art
  outside the project.

## Files and selection

| File | Size | Role |
| --- | --- | --- |
| `bg-hall.png` | 1672×941 | Clean night-banquet hall background. Retains windows, banners, candlelight, and the round table; the game word, sample speech, characters, name plates, top status bar, countdown, and bottom action panel were removed in the clean-bg pass. |
| `char-1.png` … `char-6.png` | 88×116 … 204×232 | Normal player portraits as delivered (scene crops). |
| `char-1-eliminated.png` … `char-6-eliminated.png` | same as normal | Eliminated variants with the crossed-out overlay. |
| `char-judge.png` | 200×240 | Judge/host portrait. |

`char-2-speaking.png` is intentionally not retained: the speaking state is
expressed with CSS frame styling, so no separate sprite is needed. The full
delivery ZIP and unused scene crops are not committed.

## Derived rasters

Derived from the sources above with a one-off **Pillow** pass
(Python 3, Pillow 12.3.0), then committed under `derived/`:

- `bg-hall-836x470.webp` — exactly **836×470** (a 2:1 downscale of the
  1672×941 source), resized with `Image.LANCZOS`, encoded as WebP
  `quality=72, method=6`. Size: 22,314 bytes (budget ≤ 24 KiB).
- `portrait-<n>-normal.png`, `portrait-<n>-eliminated.png`, `portrait-judge.png`
  — thirteen **80×80** paletted PNGs. Each source is cropped to a square
  sprite-local box, resized to 80×80 with `Image.NEAREST`, then quantized with
  `Image.Quantize.MEDIANCUT, colors=128, dither=Image.Dither.NONE`.
  Total: 13,513 bytes.

Final crop boxes, `(x, y, size)` in sprite-local source pixels, used for both
the normal and eliminated variant of each player:

| Cell | Source | Crop |
| --- | --- | --- |
| normal 1 / eliminated 1 | `char-1*.png` | `(24, 4, 64)` |
| normal 2 / eliminated 2 | `char-2*.png` | `(32, 6, 64)` |
| normal 3 / eliminated 3 | `char-3*.png` | `(110, 28, 72)` |
| normal 4 / eliminated 4 | `char-4*.png` | `(28, 0, 64)` |
| normal 5 / eliminated 5 | `char-5*.png` | `(30, 12, 64)` |
| normal 6 / eliminated 6 | `char-6*.png` | `(28, 18, 64)` |
| judge | `char-judge.png` | `(72, 0, 60)` |

The crop boxes were visually verified against the rendered 13-cell sheet. Boxes
for players 2, 3, 5, and 6 were shifted from the initial framing because it
clipped the right side of the face; the committed boxes keep the full face
(eyes, nose, mouth) inside every medallion.

### Reproduce the one-off derivation

```bash
python3 - <<'PY'
from PIL import Image
src, out = 'assets/source/night-banquet', 'assets/source/night-banquet/derived'
boxes = {1: (24, 4, 64), 2: (32, 6, 64), 3: (110, 28, 72),
         4: (28, 0, 64), 5: (30, 12, 64), 6: (28, 18, 64)}
bg = Image.open(f'{src}/bg-hall.png').convert('RGB')
bg.resize((836, 470), Image.LANCZOS).save(
    f'{out}/bg-hall-836x470.webp', 'WEBP', quality=72, method=6)
for k, (x, y, s) in boxes.items():
    for variant, path in (('normal', f'{src}/char-{k}.png'),
                          ('eliminated', f'{src}/char-{k}-eliminated.png')):
        c = Image.open(path).convert('RGB').crop((x, y, x + s, y + s))
        c.resize((80, 80), Image.NEAREST).quantize(
            colors=128, method=Image.Quantize.MEDIANCUT,
            dither=Image.Dither.NONE
        ).save(f'{out}/portrait-{k}-{variant}.png')
x, y, s = 72, 0, 60
c = Image.open(f'{src}/char-judge.png').convert('RGB').crop((x, y, x + s, y + s))
c.resize((80, 80), Image.NEAREST).quantize(
    colors=128, method=Image.Quantize.MEDIANCUT,
    dither=Image.Dither.NONE
).save(f'{out}/portrait-judge.png')
PY
```

The derived rasters are committed because the atlases must be deterministic
and buildable without Pillow. `node scripts/generate-atlases.mjs` only reads
them and embeds them as base64 data URIs; it does not re-run this derivation.

## Why compact rasters inside SVG atlases

The panel ships as a single UMD file that embeds every image; it must not make a
runtime image request. The full 1672×941 background is too large to embed, so
the runtime uses a half-resolution 836×470 WebP and 80×80 portrait medallions
instead of full-body sprites. `src/undercover-game/assets/night-hall.svg`
carries the WebP as one embedded `data:image/webp;base64` image, and
`src/undercover-game/assets/night-portraits.svg` carries the thirteen portraits
as a 400×240, five-column, 80×80-cell atlas. Both are regenerated from
`derived/` by the atlas script, keeping the combined production atlas budget
under 120 KiB.
