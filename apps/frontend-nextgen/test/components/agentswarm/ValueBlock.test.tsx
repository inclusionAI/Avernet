/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { ValueBlock } from '@/components/agentswarm/ValueBlock';

describe('ValueBlock（阶段产出/最终结果渲染）', () => {
  test('字符串按正文渲染，而不是代码块', () => {
    render(<ValueBlock value="hello" label="产出" empty="空" />);
    expect(screen.getByText('hello')).toBeInTheDocument();
    expect(document.querySelector('pre')).toBeNull();
  });

  test('{"text": "..."} 取出正文，原始 JSON 收进折叠区', () => {
    render(<ValueBlock value={{ text: 'world' }} label="产出" empty="空" />);

    // 取值而不是序列化
    expect(screen.getByText('world')).toBeInTheDocument();
    expect(screen.queryByText(/\{"text"/)).toBeNull();
    // 仍然可以查看原始 JSON
    expect(screen.getByText('查看原始 JSON')).toBeInTheDocument();
  });

  test('纯字符串不显示多余的原始 JSON 折叠', () => {
    render(<ValueBlock value="same" label="产出" empty="空" />);
    expect(screen.queryByText('查看原始 JSON')).toBeNull();
  });

  test('多字段对象不伪装成一句话，默认收起结构', () => {
    render(<ValueBlock value={{ steps: [1, 2], usage: { tokens: 3 } }} label="结果" empty="空" />);

    expect(screen.getByText('该结果是结构化数据，展开查看字段。')).toBeInTheDocument();
    expect(screen.queryByText(/steps/)).toBeNull();
  });

  test('空值显示占位文案', () => {
    render(<ValueBlock value={null} label="产出" empty="（还没有产出）" />);
    expect(screen.getByText('（还没有产出）')).toBeInTheDocument();
  });
});
