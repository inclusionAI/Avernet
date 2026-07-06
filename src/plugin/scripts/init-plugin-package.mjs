#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, '..');
const templateDir = path.join(repoRoot, 'templates', 'current-plugin-package-standard');
const packagesDir = path.join(repoRoot, 'packages');

function fail(message) {
  console.error(message);
  process.exit(1);
}

function toKebabCase(value) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .replace(/-{2,}/g, '-');
}

function toTitleCase(value) {
  return value
    .split('-')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function parseArgs(argv) {
  const args = {
    name: '',
    scope: '',
    description: '',
    author: '',
  };

  for (let index = 0; index < argv.length; index += 1) {
    const current = argv[index];
    if (!args.name && !current.startsWith('--')) {
      args.name = current;
      continue;
    }
    if (current === '--scope') {
      args.scope = argv[index + 1] ?? '';
      index += 1;
      continue;
    }
    if (current === '--description') {
      args.description = argv[index + 1] ?? '';
      index += 1;
      continue;
    }
    if (current === '--author') {
      args.author = argv[index + 1] ?? '';
      index += 1;
      continue;
    }
    fail(`Unknown argument: ${current}`);
  }

  return args;
}

function replaceInFile(filePath, replacements) {
  const content = fs.readFileSync(filePath, 'utf8');
  let next = content;
  for (const [from, to] of replacements) {
    next = next.split(from).join(to);
  }
  fs.writeFileSync(filePath, next);
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const normalizedName = toKebabCase(args.name);
  if (!normalizedName) {
    fail('Usage: pnpm init:plugin <plugin-name> [--scope @scope] [--description "..."] [--author "..."]');
  }

  if (!fs.existsSync(templateDir)) {
    fail(`Template not found: ${templateDir}`);
  }

  const normalizedScope = args.scope.trim();
  const packageName = normalizedScope ? `${normalizedScope}/${normalizedName}` : normalizedName;
  const pluginDisplayName = toTitleCase(normalizedName);
  const description =
    args.description.trim() || `${pluginDisplayName} plugin package for OpenClaw.`;
  const author = args.author.trim() || 'your.name';
  const targetDir = path.join(packagesDir, normalizedName);

  if (fs.existsSync(targetDir)) {
    fail(`Target package already exists: ${targetDir}`);
  }

  fs.cpSync(templateDir, targetDir, { recursive: true });

  const replacements = [
    ['@scope/plugin-name', packageName],
    ['plugin-name', normalizedName],
    ['Plugin Name', pluginDisplayName],
    ['Installable OpenClaw plugin template.', description],
    ['Plugin package template aligned with the current repository output standard.', description],
    ['your.name', author],
  ];

  replaceInFile(path.join(targetDir, 'package.json'), replacements);
  replaceInFile(path.join(targetDir, 'openclaw.plugin.json'), replacements);
  replaceInFile(path.join(targetDir, 'README.md'), replacements);

  console.log(`Created plugin package at ${path.relative(repoRoot, targetDir)}`);
  console.log('Next steps:');
  console.log(`  1. Fill in yuyanId in ${path.relative(repoRoot, path.join(targetDir, 'package.json'))}`);
  console.log(`  2. Implement register(api) in ${path.relative(repoRoot, path.join(targetDir, 'src', 'index.ts'))}`);
  console.log('  3. Run pnpm install');
  console.log(`  4. Run pnpm --filter ${packageName} build`);
}

main();
