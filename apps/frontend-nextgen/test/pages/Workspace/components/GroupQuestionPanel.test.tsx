/** @jest-environment jsdom */
// GroupQuestionPanel / GroupQuestionForm 交互测试（specs/2026-10-13-bcs-ask-user-question Task4）。
// 覆盖：Bot 分组与名称兜底、首组自动展开 / 折叠不反复展开 / 折叠保留草稿、多题导航与跳过、
// 提交 option 原 value、自定义输入与单选互斥、取消确认、断线 / 提交中禁用、其他 Bot 仍可回答。
import type { ParticipantView } from '@/domain/collaboration';
import type {
  GroupQuestion,
  GroupQuestionInteraction,
  GroupQuestionResolution,
  QuestionDraft,
  QuestionDrafts,
} from '@/domain/collaboration/groupQuestions';
import { GroupQuestionPanel } from '@/pages/Workspace/components/GroupChatPane/GroupQuestionPanel';
import type { GroupChatQuestionsModel } from '@/pages/Workspace/hooks/useGroupChatQuestions';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor, within } from '@testing-library/react';
import { useState } from 'react';

// Radix 弹层在 jsdom 下需要的指针事件桩（对齐 test/components/ui/Wave1B.test.tsx）。
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.setPointerCapture ??= () => undefined;
Element.prototype.releasePointerCapture ??= () => undefined;

const keyAlpha = JSON.stringify(['run-1', 'i1']);
const keyBeta = JSON.stringify(['run-2', 'i2']);

const qEnv: GroupQuestion = {
  questionId: 'q1',
  question: '要部署到哪个环境？',
  header: '环境',
  multiSelect: false,
  allowOther: true,
  options: [
    { value: 'stage', label: '预发布' },
    { value: 'prod', label: '生产' },
  ],
};

const qCluster: GroupQuestion = {
  questionId: 'q2',
  question: '选择目标集群（可多选）',
  multiSelect: true,
  allowOther: false,
  options: [
    { value: 'c1', label: '集群一' },
    { value: 'c2', label: '集群二' },
  ],
};

const qFree: GroupQuestion = {
  questionId: 'q3',
  question: '还有要补充的内容吗？',
  multiSelect: false,
  allowOther: false,
  options: [],
};

const alphaEnvInteraction: GroupQuestionInteraction = {
  key: keyAlpha,
  runId: 'run-1',
  interactionId: 'i1',
  botId: 'bot-alpha',
  questions: [qEnv],
};

const alphaMultiInteraction: GroupQuestionInteraction = {
  key: keyAlpha,
  runId: 'run-1',
  interactionId: 'i1',
  botId: 'bot-alpha',
  questions: [qEnv, qCluster],
};

const betaFreeInteraction: GroupQuestionInteraction = {
  key: keyBeta,
  runId: 'run-2',
  interactionId: 'i2',
  botId: 'bot-beta',
  questions: [qFree],
};

const participants: ParticipantView[] = [
  { actorId: 'human-1', kind: 'human', name: '张三', role: 'owner', mode: 'auto' },
  { actorId: 'bot-alpha', kind: 'bot', name: 'Alpha Bot', role: 'worker', mode: 'auto' },
];

interface HarnessProps {
  interactions: GroupQuestionInteraction[];
  participants?: ParticipantView[];
  connected?: boolean;
  submittingKeys?: string[];
  resolve?: (key: string, resolution: GroupQuestionResolution) => Promise<boolean>;
}

/** 以本地 useState 承载草稿，模拟 useGroupChatQuestions 集中保存的行为。 */
function QuestionPanelHarness(props: HarnessProps) {
  const [drafts, setDrafts] = useState<Record<string, QuestionDrafts>>({});
  const model: GroupChatQuestionsModel = {
    interactions: props.interactions,
    drafts,
    submittingKeys: props.submittingKeys ?? [],
    connected: props.connected ?? true,
    setDraft: (key: string, questionId: string, draft: QuestionDraft) => {
      setDrafts((prev) => {
        const next: Record<string, QuestionDrafts> = { ...prev };
        const per: QuestionDrafts = { ...(prev[key] ?? {}) };
        per[questionId] = draft;
        next[key] = per;
        return next;
      });
    },
    resolve: props.resolve ?? (async () => true),
  };
  return <GroupQuestionPanel model={model} participants={props.participants ?? participants} />;
}

describe('GroupQuestionPanel', () => {
  it('无待处理交互时不渲染任何内容', () => {
    const { container } = render(<QuestionPanelHarness interactions={[]} />);
    expect(container.firstElementChild).toBeNull();
  });

  it('按 Bot 分组展示；成员名缺失时回退 Bot ID', () => {
    render(<QuestionPanelHarness interactions={[alphaEnvInteraction, betaFreeInteraction]} />);
    expect(screen.getByRole('region', { name: /Alpha Bot/ })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: /bot-beta/ })).toBeInTheDocument();
  });

  it('内容区域限高 min(38vh,360px) 并支持内部滚动', () => {
    render(<QuestionPanelHarness interactions={[alphaEnvInteraction]} />);
    const scroller = screen.getByTestId('question-panel-scroll');
    expect(scroller.className).toContain('max-h-[min(38vh,360px)]');
    expect(scroller.className).toContain('overflow-y-auto');
  });

  it('问答出现时自动展开面板与首个 Bot 分组，其余分组默认收起', () => {
    render(<QuestionPanelHarness interactions={[alphaEnvInteraction, betaFreeInteraction]} />);
    expect(screen.getByText('要部署到哪个环境？')).toBeVisible();
    expect(screen.queryByText('还有要补充的内容吗？')).not.toBeInTheDocument();
  });

  it('折叠后普通状态更新不反复展开；全部结束后新问答重新自动展开', async () => {
    const user = userEvent.setup();
    const { rerender } = render(<QuestionPanelHarness interactions={[alphaEnvInteraction]} />);
    // 折叠外层面板
    await user.click(screen.getByRole('button', { name: /等待回答/ }));
    expect(screen.queryByText('要部署到哪个环境？')).not.toBeInTheDocument();

    // 普通更新（新增另一 Bot 的问答）不重新展开
    rerender(<QuestionPanelHarness interactions={[alphaEnvInteraction, betaFreeInteraction]} />);
    expect(screen.queryByText('要部署到哪个环境？')).not.toBeInTheDocument();

    // 全部处理完成 → 面板消失；新的问答到来（0→N）重新自动展开
    rerender(<QuestionPanelHarness interactions={[]} />);
    expect(screen.queryByRole('button', { name: /等待回答/ })).not.toBeInTheDocument();
    rerender(<QuestionPanelHarness interactions={[betaFreeInteraction]} />);
    expect(screen.getByText('还有要补充的内容吗？')).toBeVisible();
  });

  it('折叠分组不丢已填草稿', async () => {
    const user = userEvent.setup();
    render(<QuestionPanelHarness interactions={[alphaEnvInteraction]} />);
    await user.type(screen.getByLabelText('自定义回答'), '灰度环境');
    await user.click(screen.getByRole('button', { name: /Alpha Bot/ }));
    expect(screen.queryByLabelText('自定义回答')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Alpha Bot/ }));
    expect(screen.getByLabelText('自定义回答')).toHaveValue('灰度环境');
  });

  it('选择“预发布”并确认：提交 option 原 value 而非 label', async () => {
    const resolve = jest.fn<any>().mockResolvedValue(true);
    render(<QuestionPanelHarness interactions={[alphaEnvInteraction]} resolve={resolve} />);
    const user = userEvent.setup();
    await user.click(screen.getByLabelText('预发布'));
    await user.click(screen.getByRole('button', { name: '确认提交' }));
    expect(resolve).toHaveBeenCalledTimes(1);
    expect(resolve).toHaveBeenCalledWith(alphaEnvInteraction.key, {
      action: 'submit',
      answers: { q1: { values: ['stage'] } },
    });
  });

  it('多题导航与整组提交：多选合并，answers 覆盖全部 questionId', async () => {
    const resolve = jest.fn<any>().mockResolvedValue(true);
    render(<QuestionPanelHarness interactions={[alphaMultiInteraction]} resolve={resolve} />);
    const user = userEvent.setup();
    await user.click(screen.getByLabelText('预发布'));
    await user.click(screen.getByRole('button', { name: '下一题' }));
    // 第 2 题 allowOther=false 且带选项：不提供自定义输入
    expect(screen.queryByLabelText('自定义回答')).not.toBeInTheDocument();
    await user.click(screen.getByLabelText('集群一'));
    await user.click(screen.getByLabelText('集群二'));
    await user.click(screen.getByRole('button', { name: '确认提交' }));
    expect(resolve).toHaveBeenCalledWith(alphaMultiInteraction.key, {
      action: 'submit',
      answers: { q1: { values: ['stage'] }, q2: { values: ['c1', 'c2'] } },
    });
  });

  it('跳过此题：记录跳过并前进；末题跳过仅在全部处理完毕后自动提交', async () => {
    const resolve = jest.fn<any>().mockResolvedValue(true);
    render(<QuestionPanelHarness interactions={[alphaMultiInteraction]} resolve={resolve} />);
    const user = userEvent.setup();

    // 第 1 题跳过 → 自动前进到第 2 题
    await user.click(screen.getByRole('button', { name: '跳过此题' }));
    expect(screen.getByText('选择目标集群（可多选）')).toBeVisible();
    expect(screen.getByRole('button', { name: '问题 2' })).toHaveAttribute('aria-current', 'step');

    // 切回第 1 题可见跳过标记
    await user.click(screen.getByRole('button', { name: '环境' }));
    expect(screen.getByText('已跳过')).toBeVisible();

    // 末题（第 2 题）跳过：第 1 题已处理 → 自动提交，跳过题 values 为 []
    await user.click(screen.getByLabelText('预发布'));
    await user.click(screen.getByRole('button', { name: '下一题' }));
    await user.click(screen.getByRole('button', { name: '跳过此题' }));
    expect(resolve).toHaveBeenCalledWith(alphaMultiInteraction.key, {
      action: 'submit',
      answers: { q1: { values: ['stage'] }, q2: { values: [] } },
    });
  });

  it('末题跳过时其余题目未完成则不提交并定位首个未完成题', async () => {
    const resolve = jest.fn<any>().mockResolvedValue(true);
    render(<QuestionPanelHarness interactions={[alphaMultiInteraction]} resolve={resolve} />);
    const user = userEvent.setup();
    // 直接切到第 2 题并跳过：第 1 题未答 → 不提交，回到第 1 题
    await user.click(screen.getByRole('button', { name: '问题 2' }));
    await user.click(screen.getByRole('button', { name: '跳过此题' }));
    expect(resolve).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '环境' })).toHaveAttribute('aria-current', 'step');
  });

  it('单选与自定义互斥；多选可组合选项与自定义文本', async () => {
    const resolve = jest.fn<any>().mockResolvedValue(true);
    const interaction: GroupQuestionInteraction = {
      key: keyAlpha,
      runId: 'run-1',
      interactionId: 'i1',
      botId: 'bot-alpha',
      questions: [
        qEnv,
        { ...qCluster, allowOther: true },
      ],
    };
    render(<QuestionPanelHarness interactions={[interaction]} resolve={resolve} />);
    const user = userEvent.setup();

    // 第 1 题（单选）：选中选项清空自定义、输入自定义清空选项
    await user.click(screen.getByLabelText('预发布'));
    expect(screen.getByLabelText('预发布')).toBeChecked();
    await user.type(screen.getByLabelText('自定义回答'), '灰度集群');
    expect(screen.getByLabelText('预发布')).not.toBeChecked();
    await user.click(screen.getByLabelText('生产'));
    expect(screen.getByLabelText('自定义回答')).toHaveValue('');
    // 以自定义文本作为第 1 题答案
    await user.type(screen.getByLabelText('自定义回答'), '灰度集群');

    // 第 2 题（多选）：选项与自定义文本合并提交
    await user.click(screen.getByRole('button', { name: '下一题' }));
    await user.click(screen.getByLabelText('集群一'));
    await user.type(screen.getByLabelText('自定义回答'), '华南新集群');
    await user.click(screen.getByRole('button', { name: '确认提交' }));
    expect(resolve).toHaveBeenCalledWith(interaction.key, {
      action: 'submit',
      answers: {
        q1: { values: ['灰度集群'] },
        q2: { values: ['c1', '华南新集群'] },
      },
    });
  });

  it('无 options 的题目始终提供自由文本输入（allowOther=false 亦可）', async () => {
    const resolve = jest.fn<any>().mockResolvedValue(true);
    render(<QuestionPanelHarness interactions={[betaFreeInteraction]} resolve={resolve} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('你的回答'), '今晚 8 点前完成发布');
    await user.click(screen.getByRole('button', { name: '确认提交' }));
    expect(resolve).toHaveBeenCalledWith(betaFreeInteraction.key, {
      action: 'submit',
      answers: { q3: { values: ['今晚 8 点前完成发布'] } },
    });
  });

  it('确认时经领域校验并聚焦首个无效题目；修改输入清除错误', async () => {
    const resolve = jest.fn<any>().mockResolvedValue(true);
    render(<QuestionPanelHarness interactions={[alphaMultiInteraction]} resolve={resolve} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '确认提交' }));
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('q1');
    expect(screen.getByRole('button', { name: '环境' })).toHaveAttribute('aria-current', 'step');
    expect(screen.getByLabelText('预发布')).toHaveFocus();
    expect(resolve).not.toHaveBeenCalled();

    // 修改输入清除错误；末题未答时继续校验并定位到末题
    await user.click(screen.getByLabelText('预发布'));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '下一题' }));
    await user.click(screen.getByRole('button', { name: '确认提交' }));
    expect(screen.getByRole('alert')).toHaveTextContent('q2');
    expect(screen.getByLabelText('集群一')).toHaveFocus();
  });

  it('取消问答需确认，确认后发送 cancel 并关闭对话框', async () => {
    const resolve = jest.fn<any>().mockResolvedValue(true);
    render(<QuestionPanelHarness interactions={[alphaEnvInteraction]} resolve={resolve} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('自定义回答'), '草稿内容');
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '取消问答' }));
    const dialog = screen.getByRole('alertdialog');
    expect(within(dialog).getByRole('heading', { name: /确认取消问答/ })).toBeVisible();

    await user.click(within(dialog).getByRole('button', { name: '确认取消' }));
    expect(resolve).toHaveBeenCalledWith(alphaEnvInteraction.key, { action: 'cancel' });
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
  });

  it('取消失败：对话框保持打开且草稿不丢', async () => {
    const resolve = jest.fn<any>().mockResolvedValue(false);
    render(<QuestionPanelHarness interactions={[alphaEnvInteraction]} resolve={resolve} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('自定义回答'), '草稿内容');
    await user.click(screen.getByRole('button', { name: '取消问答' }));
    const dialog = screen.getByRole('alertdialog');

    await user.click(within(dialog).getByRole('button', { name: '确认取消' }));
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
    // 问答与输入保留
    expect(screen.getByLabelText('自定义回答')).toHaveValue('草稿内容');
  });

  it('断线时问答操作全部禁用并给出提示', () => {
    render(<QuestionPanelHarness interactions={[alphaEnvInteraction]} connected={false} />);
    expect(screen.getByText('连接不可用，暂时无法回答')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '确认提交' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '跳过此题' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '取消问答' })).toBeDisabled();
    expect(screen.getByLabelText('预发布')).toBeDisabled();
    expect(screen.getByLabelText('自定义回答')).toBeDisabled();
  });

  it('提交中的交互禁用作答，其他 Bot 的问答仍可回答', async () => {
    const resolve = jest.fn<any>().mockResolvedValue(true);
    render(
      <QuestionPanelHarness
        interactions={[alphaEnvInteraction, betaFreeInteraction]}
        submittingKeys={[keyAlpha]}
        resolve={resolve}
      />,
    );
    const user = userEvent.setup();
    const alphaRegion = screen.getByRole('region', { name: /Alpha Bot/ });
    expect(within(alphaRegion).getByRole('button', { name: '确认提交' })).toBeDisabled();

    // beta（收起分组）展开后仍可正常作答
    await user.click(screen.getByRole('button', { name: /bot-beta/ }));
    const betaRegion = screen.getByRole('region', { name: /bot-beta/ });
    expect(within(betaRegion).getByRole('button', { name: '确认提交' })).toBeEnabled();
    await user.type(screen.getByLabelText('你的回答'), 'beta 的回答');
    await user.click(within(betaRegion).getByRole('button', { name: '确认提交' }));
    expect(resolve).toHaveBeenCalledWith(keyBeta, {
      action: 'submit',
      answers: { q3: { values: ['beta 的回答'] } },
    });
  });
});