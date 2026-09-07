import fs from 'fs';
import path from 'path';

const css = fs.readFileSync(path.join(process.cwd(), 'src/global.css'), 'utf8');

const baseLayerBody = () => /@layer base\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? '';

describe('global.css 指针样式兜底', () => {
  it('兜底规则位于 @layer base 内（低于 utilities 与第三方 unlayered 样式）', () => {
    const body = baseLayerBody();
    expect(body).toContain('cursor: pointer');
    expect(body).toContain('button:not(:disabled)');
    expect(body).toContain("[role='button']:not([aria-disabled='true'])");
    expect(body).toContain('a[href]');
  });

  it('不含 [tabindex] 选择器，避免误伤滚动容器与 Radix 内部节点', () => {
    expect(baseLayerBody()).not.toContain('[tabindex]');
  });

  it('文本输入元素不在兜底名单内，保留 I-beam', () => {
    const body = baseLayerBody();
    expect(body).not.toContain("input[type='text']");
    expect(body).not.toContain('textarea');
  });
});
