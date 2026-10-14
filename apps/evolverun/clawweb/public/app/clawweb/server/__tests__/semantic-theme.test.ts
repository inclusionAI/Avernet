import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { build } from 'vite';
import tailwind from '@tailwindcss/vite';
import { expect, it } from 'vitest';

it('builds semantic colors through the shared package stylesheet export', async () => {
  // Embedding hosts consume this public export, without importing the public app.
  const sharedCss = createRequire(import.meta.url).resolve('@avernet/clawweb-shared/theme.css');
  expect(readFileSync(sharedCss, 'utf8')).toContain('@theme inline');
  const root = fileURLToPath(new URL('../../', import.meta.url));
  const fixture = mkdtempSync(join(root, '.theme-test-'));
  try {
    const entry = join(fixture, 'theme.css');
    const hostCss = readFileSync(join(root, 'web/style.css'), 'utf8');
    // Scan explicit consumer classes, independently of the repository layout.
    writeFileSync(entry, hostCss.replace(/^@source .*$/gm, '') + '\n'
      + '@source inline("bg-primary text-primary-foreground bg-card border-input text-destructive bg-primary/10");\n');
    const result = await build({
      configFile: false, root, logLevel: 'silent', plugins: [tailwind()],
      build: { write: false, rollupOptions: { input: entry } },
    });
    const css = (Array.isArray(result) ? result : [result]).flatMap(bundle => bundle.output)
      .filter(asset => asset.type === 'asset' && asset.fileName.endsWith('.css'))
      .map(asset => asset.type === 'asset' ? String(asset.source) : '').join('\n');
    for (const selector of ['.bg-primary', '.text-primary-foreground', '.bg-card', '.border-input', '.text-destructive']) {
      expect(css.includes(selector), `${selector} must have a generated rule`).toBe(true);
    }
    expect(css).toContain('var(--primary,');
    expect(css).toContain('var(--card,');
  } finally {
    rmSync(fixture, { recursive: true, force: true });
  }
}, 30_000);
