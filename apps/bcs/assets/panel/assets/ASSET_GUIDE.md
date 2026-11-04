# Undercover night-banquet asset guide

- **Source:** Avernet #2608 night-banquet JRPG UI delivery, 2026-11-04, Apache-2.0
  with the package. Exact source files, selection, and the one-off Pillow
  derivation live in [`source/night-banquet/README.md`](source/night-banquet/README.md).
- **Background:** `bg-hall.png` (clean 1672×941 hall: windows, banners, candlelight,
  round table, no baked-in word, speech, characters, name plates, status bar,
  countdown, or action panel) is derived to **836×470 WebP quality 72**
  (`Image.LANCZOS` 2:1 downscale) and embedded as one `data:image/webp;base64`
  image in `src/undercover-game/assets/night-hall.svg` (836×470).
- **Portraits:** the thirteen delivered character crops become **80×80** paletted
  PNGs (crop square → `Image.NEAREST` resize → `MEDIANCUT` quantize to 128
  colors, no dithering). They are arranged row-major in
  `src/undercover-game/assets/night-portraits.svg` (400×240, five columns of
  80×80): indices 0–5 normal variants 1–6, 6–11 eliminated variants 1–6, and
  index 12 the judge. The speaking state is CSS-only; `char-2-speaking.png` is
  not used.
- **Palette:** warm walnut and deep indigo hall, crimson banners and runner,
  brass candlelight, and the delivered portrait palette. Portraits are
  quantized to at most 128 colors each so the atlas stays compact.
- **Eliminated state:** the delivered eliminated variants already carry the
  crossed-out overlay; no extra runtime pixel overlay is required.
- **Replacements:** the retired Kenney room/character atlases
  (`room-atlas.svg`, `character-atlas.svg`) and the retired original knight
  sculpture (`knight-statue.svg`) are removed. The night-banquet hall and
  portraits replace those room and character variants; the retained small
  lifecycle/action sprites stay in `ui-atlas.svg`, and the original
  `speech-frame.svg` / `speech-tail.svg` are unchanged.
- **Rendering:** each atlas cell is addressed by a `viewBox` crop so only that
  cell is drawn; `image-rendering="pixelated"` keeps hard pixel edges. All art
  is embedded locally; the panel makes no runtime image request.
- **Compact mode:** the single-file UMD budget is the reason for the compact
  half-resolution background and portrait medallions instead of a full-size
  scene plus full-body sprites. Narrow panels keep the hall backdrop plus the
  portrait roster; medium and wide layouts reuse the same embedded atlases.

Regenerate both night-banquet atlases from the committed derived rasters:

```bash
node scripts/generate-atlases.mjs
```
