/** @jest-environment jsdom */
import { getPreviewKind, isAllowedFileExt, prettifyJsonText, PREVIEW_TEXT_EXT } from '@/services/workspace/sessionFileUtils';
import { describe, expect, it } from '@jest/globals';

describe('sessionFileUtils 预览类型判定（对齐会话文件预览类型清单）', () => {
  it('视频类型：mp4/webm/mov 判定为 video（内嵌播放器预览）', () => {
    expect(getPreviewKind('演示录屏.mp4')).toBe('video');
    expect(getPreviewKind('动画.webm')).toBe('video');
    expect(getPreviewKind('片段.mov')).toBe('video');
    // 无扩展名时按 MIME 兜底。
    expect(getPreviewKind('无扩展名视频', 'video/webm')).toBe('video');
  });

  it('网页类型：html 判定为 html（沙箱渲染），并从文本白名单拆出', () => {
    expect(getPreviewKind('落地页.html')).toBe('html');
    expect(getPreviewKind('无扩展名网页', 'text/html')).toBe('html');
    // html 不再属于 text（等宽原文展示）。
    expect(PREVIEW_TEXT_EXT).not.toContain('html');
  });

  it('既有类型不回归：图片/pdf/文本/不可预览', () => {
    expect(getPreviewKind('截图.png')).toBe('image');
    expect(getPreviewKind('报告.pdf')).toBe('pdf');
    expect(getPreviewKind('说明.md')).toBe('text');
    expect(getPreviewKind('配置.json')).toBe('text');
    expect(getPreviewKind('打包.zip')).toBe('other');
    expect(getPreviewKind('文档.docx')).toBe('other');
  });

  it('JSON 美化：合法 JSON 缩进重排，非法 JSON 原样返回', () => {
    expect(prettifyJsonText('{"a":1,"b":[2,3]}')).toBe(JSON.stringify({ a: 1, b: [2, 3] }, null, 2));
    // 非法 JSON（如截断后的半截内容）回退原文，不抛错。
    const broken = '{"a":1,"b":[2,3';
    expect(prettifyJsonText(broken)).toBe(broken);
    expect(prettifyJsonText('普通文本')).toBe('普通文本');
  });
});

describe('extended textual file recognition', () => {
  it.each([
    '说明.markdown',
    '说明.mdx',
    '手册.rst',
    '文档.adoc',
    '论文.tex',
    '配置.jsonc',
    '配置.json5',
    '记录.jsonl',
    '记录.ndjson',
    '分析.ipynb',
    '样式.css',
    '样式.scss',
    '样式.less',
    '页面.svelte',
    '页面.astro',
    '模块.mts',
    '模块.cts',
    '类型.pyi',
    '接口.proto',
    '查询.graphql',
    '代码.kt',
    '脚本.lua',
    '脚本.pl',
    '代码.hpp',
    '代码.fs',
    '构建.gradle',
    '构建.cmake',
    '基础设施.tf',
    '声明.hcl',
    '构建.bzl',
    '补丁.diff',
    '补丁.patch',
    '依赖.lock',
    '地图.geojson',
    '配置.properties',
    'README',
    'LICENSE',
    'CHANGELOG',
    'Makefile',
    'Dockerfile',
    'Containerfile',
    'Jenkinsfile',
    'Procfile',
    'Gemfile',
    'CODEOWNERS',
    'Justfile',
    '.gitignore',
    '.gitattributes',
    '.npmrc',
    '.editorconfig',
    '.env.production',
    '.env.local',
    '.env.production.local',
    'Dockerfile.dev',
  ])('recognizes %s as text without relying on MIME', (name) => {
    expect(getPreviewKind(name, 'application/octet-stream')).toBe('text');
  });

  it.each([
    'application/json; charset=utf-8',
    'application/vnd.api+json',
    'application/ld+json',
    'application/xml; charset=utf-8',
    'application/rss+xml',
    'application/yaml',
    'application/x-yaml',
    'application/toml',
    'application/x-ndjson',
    'application/javascript',
    'application/graphql',
    'application/sql',
    ' TEXT/PLAIN ; charset=UTF-8 ',
  ])('recognizes extensionless text with MIME %s', (mime) => {
    expect(getPreviewKind('无扩展名文件', mime)).toBe('text');
  });

  it.each(['htm', 'xhtml'])('keeps .%s in the restricted HTML frame branch', (ext) => {
    expect(getPreviewKind(`页面.${ext}`)).toBe('html');
  });

  it('normalizes MIME parameters while keeping binary/media branches ahead of structured XML', () => {
    expect(getPreviewKind('文档', 'application/pdf; charset=binary')).toBe('pdf');
    expect(getPreviewKind('图片', 'image/svg+xml; charset=utf-8')).toBe('image');
    expect(getPreviewKind('页面', 'application/xhtml+xml; charset=utf-8')).toBe('html');
  });

  it.each([
    '打包.zip',
    '打包.tar.gz',
    '文档.docx',
    '表格.xlsx',
    '演示.pptx',
    '数据.bin',
    '数据.dat',
    '未知文件',
    '.env.zip',
    'Dockerfile.zip',
  ])('does not guess binary/unknown %s is text', (name) => {
    expect(getPreviewKind(name, 'application/octet-stream')).toBe('other');
  });
});

it.each([
  'application/octet-stream',
  'application/zip',
  'application/gzip',
  'application/wasm',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
])('does not treat arbitrary binary MIME %s as text', (mime) => {
  expect(getPreviewKind('未知文件', mime)).toBe('other');
});

it('does not expand upload permissions when adding text preview formats', () => {
  expect(getPreviewKind('接口.proto')).toBe('text');
  expect(isAllowedFileExt('接口.proto')).toBe(false);
});

it.each([
  ['归档.zip', 'text/plain'],
  ['Dockerfile.zip', 'text/plain'],
  ['备份.tar.gz', 'application/json'],
  ['文档.docx', 'text/markdown'],
  ['数据.bin', 'application/vnd.api+json'],
])('does not classify binary file %s as text when MIME is misleading', (name, mime) => {
  expect(getPreviewKind(name, mime)).toBe('other');
});
