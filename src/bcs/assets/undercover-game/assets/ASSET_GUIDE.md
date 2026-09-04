# Undercover room asset guide

- **Source grid:** 16×16 pixels; atlas slots are 32×32 and render at integer multiples.
- **Palette:** ink `#201c30`, deep plum `#39324b`, muted red `#a94f5c`, wood `#8b5260`, amber `#f2b36c`, paper `#f9edcf`, Human teal `#5fb7a6`, neutral gray `#8b8ca3`.
- **Outline:** one source pixel, dark-plum/ink, with no semitransparent edge pixels.
- **Lighting:** upper-left highlight, lower-right shadow.
- **Characters:** selected 16×16 Kenney silhouettes in 32×32 atlas slots; Human and host identity is supplied by dedicated surrounding frames rather than secret-dependent recoloring.
- **Furniture projection:** top-down/dollhouse hybrid. The table is a shallow oval normalized from the Urban furniture palette; chairs use the Urban furniture sprite in exact front/rear/left/right rotations.
- **Rendering:** `image-rendering: pixelated`; no antialiasing, rotation, arbitrary image URLs, or fractional sprite coordinates.
