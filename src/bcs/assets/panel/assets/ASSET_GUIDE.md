# Undercover room asset guide

- **Source grid:** Kenney characters are 16×16 pixels in 32×32 atlas slots; room source shapes use a 32px grid.
- **Palette:** slate walls `#343e4c`, walnut floor `#564437`, green felt `#304c49`, brass `#d2a66c`, paper `#e0caa1`, viewer teal `#5fb7a6`.
- **Room:** paneled walls, paired night windows and sconces, original parquet, a stepped geometric rug, and a broad felt table. Furniture is grouped at the edges so the six stable seats remain readable.
- **Lighting:** static warm sconce glows and a subtle room vignette. These layers ignore pointer events and never obscure labels.
- **Characters:** retained CC0 Kenney people render at integer source-pixel scales. A fixed frame marks the current viewer; no appearance depends on secret roles.
- **Furniture:** original stepped table geometry; the retained chair uses exact quarter-turn rotations. Table surface cards are decorative and contain no game secrets.
- **Rendering:** sprite crops explicitly fill their viewport, preventing adjacent atlas cells from appearing when the table is stretched into a wide oval. `image-rendering: pixelated` preserves hard edges. All art is embedded locally.
- **Compact mode:** narrow panels keep the room vignette and player roster; while an action is pending the vignette shrinks. The full table and decorations stay in medium/wide layouts.
