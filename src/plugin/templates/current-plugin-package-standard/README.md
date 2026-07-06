# Current Plugin Package Standard

This template captures the package shape currently needed for an installable OpenClaw plugin package in this repository.

## Included Conventions

- Build output uses `tshy` to publish both ESM and CommonJS entrypoints.
- `exports` maps `import` to `dist/esm/*` and `require` to `dist/commonjs/*`.
- `package.json` includes `openclaw.extensions` so `openclaw plugins install <npm-spec>` can install it.
- `openclaw.plugin.json` is present so OpenClaw can discover the plugin and validate config without executing code.
- Tests run through `egg-bin test`.
- TypeScript uses `NodeNext`.

## Files To Customize

- `package.json`
  - Replace package name, version, description, author, and `yuyanId`.
- `openclaw.plugin.json`
  - Replace `id`, `name`, and `description`.
- `src/index.ts`
  - Replace the sample API and the default `register(api)` function with your real plugin behavior.
- `test/index.test.ts`
  - Replace the sample tests with behavior-based coverage for your package.

## Expected Build Artifacts

Running `npm run build` should produce:

- `dist/esm/index.js`
- `dist/esm/index.d.ts`
- `dist/commonjs/index.js`
- `dist/commonjs/index.d.ts`
- `dist/esm/package.json`
- `dist/commonjs/package.json`
- `dist/package.json`
