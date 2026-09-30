/**
 * Post-build script: fix tshy-generated exports wildcards.
 *
 * tshy generates:
 *   "./*": { "import": { "types": "./dist/esm/*.d.ts", "default": "./dist/esm/*.js" } }
 *
 * The "default" field with "*.js" suffix causes double-extension when consumers
 * import with .js suffix (e.g. "@avernet/taskguard/db/api-client.js" resolves to
 * "./dist/esm/db/api-client.js.js" — file not found).
 *
 * This script patches the "default" to remove the trailing ".js" so that:
 *   "./*": { "import": { "types": "./dist/esm/*.d.ts", "default": "./dist/esm/*" } }
 *
 * Now "@avernet/taskguard/db/api-client.js" → "./dist/esm/db/api-client.js" ✓
 */
import { readFileSync, writeFileSync } from 'fs';

const pkgPath = new URL('../../package.json', import.meta.url);
const pkg = JSON.parse(readFileSync(pkgPath, 'utf8'));

let changed = false;

// Fix ./* wildcard
if (pkg.exports?.['./*']?.import?.default === './dist/esm/*.js') {
  pkg.exports['./*'].import.default = './dist/esm/*';
  changed = true;
}

// Fix ./*/* wildcard
if (pkg.exports?.['./*/*']?.import?.default === './dist/esm/*/*.js') {
  pkg.exports['./*/*'].import.default = './dist/esm/*/*';
  changed = true;
}

if (changed) {
  writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + '\n');
  console.log('[fix-exports-wildcards] Patched exports wildcards: removed .js suffix from default fields');
} else {
  console.log('[fix-exports-wildcards] No changes needed — wildcards already correct');
}