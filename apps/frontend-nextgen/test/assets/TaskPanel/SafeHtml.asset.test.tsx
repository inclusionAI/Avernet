/** @jest-environment jsdom */
// 注意:@jest/globals 的 import 必须最先(被 mock 模块在此之前),否则 hoist 后 jest 绑定未初始化。
import { describe, expect, it } from '@jest/globals';

// assets/TaskPanel/SafeHtml(发布说明富文本渲染)行覆盖补全:
// href/src 协议白名单、richText style 二道裁决(颜色/字号)、空白标题剪枝、非白名单标签 Fragment 降级、
// VOID 标签、注释节点、table 空白文本节点过滤等。
// 与 src/components/ui/SafeHtml.tsx 是两个同名组件,此处只管 assets 下这份。
import { SafeHtml } from '@/assets/TaskPanel/SafeHtml';
import { sanitizeMarkdownHtml, sanitizeRichHtml } from '@/assets/TaskPanel/sanitizeHtml';
import { render } from '@testing-library/react';

describe('SafeHtml(assets 副屏资产)', () => {
  it('纯文本与白名单排版标签直出,非白名单标签降级为 Fragment 保留子内容', () => {
    const { container } = render(
      <SafeHtml html={'Hello <strong>bold</strong><hr><em>italic</em><!-- 注释 -->end'} />,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(root.textContent).toBe('Hello bolditalicend');
    expect(root.querySelector('strong')?.textContent).toBe('bold');
    expect(root.querySelector('hr')).not.toBeNull();
    // 注释节点非 Element/Text → 丢弃:文本 Hello /strong/hr/em/end 共 5 个
    expect(root.childNodes.length).toBe(5);
  });

  it('非白名单标签(如 article)不渲染本体,子元素经 Fragment 存活', () => {
    const { container } = render(<SafeHtml html={'<article><p>段落</p></article>'} />);
    const root = container.firstElementChild as HTMLElement;
    expect(root.querySelector('article')).toBeNull();
    expect(root.querySelector('p')?.textContent).toBe('段落');
  });

  it('title 属性透传到白名单标签', () => {
    const { container } = render(<SafeHtml html={'<h2 title="彩蛋">标题</h2>'} />);
    const h2 = container.querySelector('h2') as HTMLElement;
    expect(h2.getAttribute('title')).toBe('彩蛋');
  });

  it('a 链接:安全协议保留并加 noopener,危险协议剥离 href', () => {
    const { container } = render(
      <SafeHtml
        html={
          '<a href="https://example.com/a">https</a>' +
          '<a href="mailto:a@b.c">mail</a>' +
          '<a href="tel:123">tel</a>' +
          '<a href="/relative">rel</a>' +
          '<a href="#frag">frag</a>' +
          '<a href="?q=1">query</a>' +
          '<a href="javascript:alert(1)">evil</a>' +
          '<a>no-href</a>'
        }
      />,
    );
    const anchors = Array.from(container.querySelectorAll('a'));
    expect(anchors[0].getAttribute('href')).toBe('https://example.com/a');
    expect(anchors[0].getAttribute('rel')).toBe('noopener noreferrer');
    // 非 richText 不强制新标签
    expect(anchors[0].getAttribute('target')).toBeNull();
    expect(anchors[1].getAttribute('href')).toBe('mailto:a@b.c');
    expect(anchors[2].getAttribute('href')).toBe('tel:123');
    expect(anchors[3].getAttribute('href')).toBe('/relative');
    expect(anchors[4].getAttribute('href')).toBe('#frag');
    expect(anchors[5].getAttribute('href')).toBe('?q=1');
    // javascript: 协议与空 href 都不注入 href 属性
    expect(anchors[6].hasAttribute('href')).toBe(false);
    expect(anchors[7].hasAttribute('href')).toBe(false);
  });

  it('richText 模式 a 链接强制新标签打开', () => {
    const { container } = render(<SafeHtml html={'<a href="https://example.com/">链接</a>'} richText />);
    const anchor = container.querySelector('a') as HTMLAnchorElement;
    expect(anchor.getAttribute('target')).toBe('_blank');
    expect(anchor.getAttribute('rel')).toBe('noopener noreferrer');
  });

  it('img:安全 src 保留 + alt 缺省空串 + lazy;危险 src 剥离', () => {
    const { container } = render(
      <SafeHtml
        html={
          '<img src="https://cdn.example.com/i.png" alt="示意图">' +
          '<img src="/local.png">' +
          '<img src="javascript:alert(1)">' +
          '<img>'
        }
      />,
    );
    const imgs = Array.from(container.querySelectorAll('img'));
    expect(imgs[0].getAttribute('src')).toBe('https://cdn.example.com/i.png');
    expect(imgs[0].getAttribute('alt')).toBe('示意图');
    expect(imgs[0].getAttribute('loading')).toBe('lazy');
    expect(imgs[1].getAttribute('src')).toBe('/local.png');
    expect(imgs[1].getAttribute('alt')).toBe('');
    // javascript: / 缺省 src 不注入
    expect(imgs[2].hasAttribute('src')).toBe(false);
    expect(imgs[3].hasAttribute('src')).toBe(false);
  });

  it('richText style 白名单:颜色与字号保留,其余属性/取值静默丢弃', () => {
    const { container } = render(
      <SafeHtml richText html={'<h1 style="color: #ff0000; font-size: 18px;">达标</h1>'} />,
    );
    const h1 = container.querySelector('h1') as HTMLElement;
    // jsdom style 序列化为 rgb 形式,验证白名单取值穿透到位
    expect(h1.style.color).toBe('rgb(255, 0, 0)');
    expect(h1.style.fontSize).toBe('18px');
  });

  it('richText style 命名颜色 + em/百分比字号 + 残缺片段(缺冒号)容错', () => {
    const { container } = render(
      <SafeHtml
        richText
        html={'<h2 style="; color red;font-size: 1.5em">A</h2><h3 style="font-size: 20%">B</h3>'}
      />,
    );
    const h2 = container.querySelector('h2') as HTMLElement;
    // "color red" 无冒号被跳过,font-size 命中
    expect(h2.style.color).toBe('');
    expect(h2.style.fontSize).toBe('1.5em');
    const h3 = container.querySelector('h3') as HTMLElement;
    expect(h3.style.fontSize).toBe('20%');
  });

  it('richText style 非白名单属性全部丢弃 → 不输出 style', () => {
    const { container } = render(
      <SafeHtml
        richText
        html={'<p style="background-color: black; color:javascript:x; width: 10px">x</p><p>y</p>'}
      />,
    );
    const p = Array.from(container.querySelectorAll('p'))[0] as HTMLElement;
    expect(p.hasAttribute('style')).toBe(false);
    // 无 style 属性或全非法 → props.style 为 null
    expect(Array.from(container.querySelectorAll('p'))[1].hasAttribute('style')).toBe(false);
  });

  it('非 richText 模式忽略 style 属性', () => {
    const { container } = render(<SafeHtml html={'<p style="color: red">x</p>'} />);
    expect((container.querySelector('p') as HTMLElement).hasAttribute('style')).toBe(false);
  });

  it('richText 剪除纯空白标题占位行,含图标题保留', () => {
    const { container } = render(
      <SafeHtml
        richText
        html={'<h1>&nbsp;</h1><h2> </h2><h3>&#160;</h3><h1><img src="/logo.png" alt=""></h1><h2>实题</h2>'}
      />,
    );
    const headings = Array.from(container.querySelectorAll('h1,h2,h3'));
    // &nbsp;/半角空格/&#160; 三种空白占位标题都被剪枝
    expect(container.querySelectorAll('h1,h2,h3').length).toBe(2);
    expect((headings[0] as HTMLElement).querySelector('img')).not.toBeNull();
    expect((headings[1] as HTMLElement).textContent).toBe('实题');
  });

  it('table 内空白文本子节点被过滤(React 不允许 table 直接文本子节点)', () => {
    const { container } = render(
      <SafeHtml html={'<table> <thead><tr><th>列A</th></tr></thead> <tbody><tr><td>1</td></tr></tbody> </table>'} />,
    );
    const table = container.querySelector('table') as HTMLElement;
    expect(table.firstChild?.nodeName).toBe('THEAD');
    expect(Array.from(table.childNodes).every((n) => n.nodeName !== '#text')).toBe(true);
    expect(table.querySelector('th')?.textContent).toBe('列A');
    expect(table.querySelector('td')?.textContent).toBe('1');
  });
});

describe('sanitizeHtml(DOMPurify 白名单净化)', () => {
  it('markdown 白名单:剥离 script 与事件属性', () => {
    const out = sanitizeMarkdownHtml('<p onclick="alert(1)">x</p><script>alert(1)</script>');
    expect(out).not.toContain('script');
    expect(out).not.toContain('onclick');
    expect(out).toContain('<p>x</p>');
  });

  it('richText 白名单:保留排版增强标签(b/i/u/s/small/mark 等)与 style 属性', () => {
    const out = sanitizeRichHtml('<p style="color: red"><b>加粗</b><mark>高亮</mark></p><script>s</script>');
    expect(out).toContain('<b>加粗</b>');
    expect(out).toContain('<mark>高亮</mark>');
    expect(out).toContain('style');
    expect(out).not.toContain('script');
  });
});
