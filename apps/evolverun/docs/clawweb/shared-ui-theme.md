# Shared UI theme integration

Every Tailwind host that renders public ClawWeb modules must load the shared
semantic color mappings in its own CSS entry, after Tailwind:

```css
@import "tailwindcss";
@import "@avernet/clawweb-shared/theme.css";
```

The shared package exports this stylesheet and copies it to `dist/web/theme.css`
during its build. Build public packages before building an embedding host. Import
through the package export; do not import the standalone app stylesheet.

`@source` scans consumer classes but does not define semantic colors. A host
without this import can produce a successful build while omitting `bg-primary`,
`bg-card`, `border-input`, and failure colors.

Hosts may override semantic HSL variables such as `--primary`, `--card`,
`--foreground`, `--input`, and `--destructive` on their theme scope. The shared
mappings provide public defaults when those variables are absent. Existing host
status colors remain separately owned by the host.

Both public and internal host regression tests compile their actual CSS entry
and assert generated semantic utilities. Browser acceptance must also use the
embedding host entry, including active steps, buttons, cards, failures, and theme
overrides.
