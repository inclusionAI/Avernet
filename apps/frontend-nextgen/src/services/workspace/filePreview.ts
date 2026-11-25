export const SESSION_FILE_TEXT_PREVIEW_MAX_BYTES = 512 * 1024;
export type SessionFilePreviewKind = 'image' | 'video' | 'pdf' | 'html' | 'text' | 'other';

export const PREVIEW_IMAGE_EXT = ['png', 'jpg', 'jpeg', 'gif', 'svg'] as const;

/** 视频类（内嵌播放器；上传白名单不含视频，仅预览来自其他渠道的文件）。 */
export const PREVIEW_VIDEO_EXT = ['mp4', 'webm', 'mov'] as const;

/** 文本预览识别清单；与上传白名单独立，不据此开放上传类型。 */
export const PREVIEW_TEXT_EXT = [
  // 文档、标记、结构化数据与日志。
  'txt',
  'text',
  'md',
  'markdown',
  'mdown',
  'mkd',
  'mkdn',
  'mdwn',
  'mdx',
  'rst',
  'rest',
  'adoc',
  'asciidoc',
  'org',
  'tex',
  'bib',
  'sty',
  'cls',
  'csv',
  'tsv',
  'json',
  'jsonc',
  'json5',
  'jsonl',
  'ndjson',
  'ipynb',
  'geojson',
  'topojson',
  'har',
  'map',
  'yaml',
  'yml',
  'toml',
  'ini',
  'cfg',
  'cnf',
  'conf',
  'config',
  'properties',
  'props',
  'env',
  'log',
  'err',
  'diff',
  'patch',
  'lock',
  'xml',
  'xsd',
  'xsl',
  'xslt',
  'dtd',
  'rng',
  'wsdl',
  'xaml',
  'plist',
  'resx',
  // 网页样式、脚本与源码（仅预览原文，不执行）。
  'css',
  'scss',
  'sass',
  'less',
  'styl',
  'stylus',
  'js',
  'jsx',
  'ts',
  'tsx',
  'mjs',
  'cjs',
  'mts',
  'cts',
  'vue',
  'svelte',
  'astro',
  'py',
  'pyi',
  'pyw',
  'java',
  'kt',
  'kts',
  'scala',
  'sc',
  'groovy',
  'gradle',
  'go',
  'mod',
  'sum',
  'work',
  'rs',
  'c',
  'cc',
  'cxx',
  'cpp',
  'h',
  'hh',
  'hxx',
  'hpp',
  'm',
  'mm',
  'cs',
  'fs',
  'fsx',
  'fsi',
  'vb',
  'vbs',
  'rb',
  'rake',
  'gemspec',
  'ru',
  'erb',
  'php',
  'phtml',
  'pl',
  'pm',
  'r',
  'rmd',
  'jl',
  'lua',
  'ex',
  'exs',
  'erl',
  'hrl',
  'hs',
  'lhs',
  'clj',
  'cljs',
  'cljc',
  'edn',
  'lisp',
  'lsp',
  'scm',
  'ss',
  'el',
  // Shell、查询、接口定义与构建配置。
  'sh',
  'bash',
  'zsh',
  'fish',
  'ksh',
  'csh',
  'tcsh',
  'sql',
  'bat',
  'cmd',
  'ps1',
  'psm1',
  'psd1',
  'graphql',
  'gql',
  'proto',
  'prisma',
  'thrift',
  'avsc',
  'make',
  'mk',
  'cmake',
  'bzl',
  'bazel',
  'tf',
  'tfvars',
  'hcl',
  'nix',
  'cue',
  'dhall',
  'just',
  // 常见点配置文件（.gitignore 等由 getFileExt 取点后名称）。
  'gitignore',
  'gitattributes',
  'gitmodules',
  'gitkeep',
  'dockerignore',
  'npmrc',
  'nvmrc',
  'yarnrc',
  'editorconfig',
  'babelrc',
  'browserslistrc',
  'eslintrc',
  'eslintignore',
  'prettierrc',
  'prettierignore',
  'stylelintrc',
  'pylintrc',
  'flake8',
  'coveragerc',
] as const;

const TEXT_FILE_NAMES = new Set([
  'readme',
  'license',
  'licence',
  'license-mit',
  'license-apache',
  'copying',
  'notice',
  'authors',
  'changelog',
  'changes',
  'contributing',
  'codeowners',
  'security',
  'install',
  'todo',
  'dockerfile',
  'containerfile',
  'makefile',
  'gnumakefile',
  'jenkinsfile',
  'procfile',
  'vagrantfile',
  'gemfile',
  'rakefile',
  'brewfile',
  'justfile',
  'podfile',
  'build',
  'workspace',
]);

// .env.production / Dockerfile.dev 等按文件名兜底，但不把其压缩包或二进制产物当作文本。
const BINARY_NAME_SUFFIXES = new Set([
  'zip',
  'tar',
  'tar.gz',
  'gz',
  'bz2',
  'xz',
  '7z',
  'rar',
  'zst',
  'doc',
  'docx',
  'xls',
  'xlsx',
  'ppt',
  'pptx',
  'odt',
  'ods',
  'odp',
  'rtf',
  'bin',
  'dat',
  'exe',
  'dll',
  'so',
  'dylib',
  'wasm',
  'jar',
  'class',
  'pyc',
  'db',
  'sqlite',
  'sqlite3',
]);

// application/* 不可一概当文本；仅接受明确的文本格式及下方的 +json/+xml 结构化后缀。
const TEXT_MIME_TYPES = new Set([
  'application/json',
  'application/json5',
  'application/jsonc',
  'application/jsonl',
  'application/x-ndjson',
  'application/ndjson',
  'application/yaml',
  'application/x-yaml',
  'application/toml',
  'application/xml',
  'application/javascript',
  'application/x-javascript',
  'application/ecmascript',
  'application/sql',
  'application/graphql',
  'application/x-sh',
  'application/x-shellscript',
  'application/x-python',
  'application/x-httpd-php',
]);

function isNamedTextFile(name: string, ext: string): boolean {
  const baseName = name.trim().split(/[\\/]/).pop()?.toLowerCase() ?? '';
  return (
    TEXT_FILE_NAMES.has(baseName) ||
    (!BINARY_NAME_SUFFIXES.has(ext) && /^(?:\.env|dockerfile|containerfile)(?:\.[\w-]+)+$/.test(baseName))
  );
}

export function formatFileSize(bytes: number): string {
  if (!bytes || bytes <= 0) return '--';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let index = 0;
  let size = bytes;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index++;
  }
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

export function getFileExt(name: string): string {
  if (!name) return '';
  const lower = name.toLowerCase();
  if (/\.tar\.gz$/.test(lower)) return 'tar.gz';
  const idx = lower.lastIndexOf('.');
  return idx >= 0 ? lower.slice(idx + 1) : '';
}

export function prettifyJsonText(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

export function getPreviewKind(name: string, mimeType?: string): SessionFilePreviewKind {
  const ext = getFileExt(name.trim());
  const mime = (mimeType || '').split(';', 1)[0].trim().toLowerCase();
  if ((PREVIEW_IMAGE_EXT as readonly string[]).includes(ext) || mime.startsWith('image/')) return 'image';
  if ((PREVIEW_VIDEO_EXT as readonly string[]).includes(ext) || mime.startsWith('video/')) return 'video';
  if (ext === 'pdf' || mime === 'application/pdf') return 'pdf';
  if (['html', 'htm', 'xhtml'].includes(ext) || mime === 'text/html' || mime === 'application/xhtml+xml') return 'html';
  // 已知二进制扩展名不可仅凭误标的 text/* 或 +json/+xml MIME 当作文本。
  if (BINARY_NAME_SUFFIXES.has(ext)) return 'other';
  const structuredTextMime = mime.startsWith('application/') && (mime.endsWith('+json') || mime.endsWith('+xml'));
  if (
    (PREVIEW_TEXT_EXT as readonly string[]).includes(ext) ||
    isNamedTextFile(name, ext) ||
    mime.startsWith('text/') ||
    TEXT_MIME_TYPES.has(mime) ||
    structuredTextMime
  )
    return 'text';
  return 'other';
}
