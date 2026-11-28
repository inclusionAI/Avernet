/** @jest-environment jsdom */
import { GenomeDiffView, RevisionDiff } from '@/components/rsi/GenomeDiff';
import { getGenomeDiff, type GenomeDiff } from '@/services/rsi/service';
import { act, fireEvent, render, screen } from '@testing-library/react';

jest.mock('@/services/rsi/service', () => ({ getGenomeDiff: jest.fn() }));
const getDiff = jest.mocked(getGenomeDiff);
const scope = { botId: 'bot-a', ownerId: 'owner', actorId: 'actor' };
const diff: GenomeDiff = {
  revision: 'sha256:candidate', against: 'sha256:parent', risk_tier: 'T2', rewrite_flags: [],
  changes: [
    { gene: 'skills/editor/SKILL.md', kind: 'modified', diff: '-old\n+<script>unsafe()</script>' },
    { gene: 'resources/image', kind: 'added', diff: null },
  ],
};
beforeEach(() => jest.clearAllMocks());

test('renders actual member paths, null-diff limitation and content as text, not HTML', () => {
  const view = render(<GenomeDiffView diff={diff} />);
  expect(screen.getByText('modified · skills/editor/SKILL.md')).toBeTruthy();
  expect(screen.getByText(/未提供文本 Diff/)).toBeTruthy();
  expect(screen.getByText(/unsafe\(\)/)).toBeTruthy();
  expect(view.container.querySelector('script')).toBeNull();
});

test('loads exact immutable hashes and distinguishes errors from an empty diff; retry is read-only', async () => {
  getDiff.mockRejectedValueOnce(new Error('failed')).mockResolvedValueOnce(diff);
  render(<RevisionDiff scope={scope} revision={diff.revision} against={diff.against} />);
  await screen.findByRole('alert');
  expect(screen.queryByText('这两个版本没有基因差异。')).toBeNull();
  fireEvent.click(screen.getByText('重试读取 Diff'));
  await screen.findByText('modified · skills/editor/SKILL.md');
  expect(getDiff).toHaveBeenLastCalledWith(scope, diff.revision, diff.against);
});

test('late previous Bot results are discarded and wrong revision receipts are refused', async () => {
  let resolveOld!: (value: GenomeDiff) => void;
  getDiff.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
    .mockResolvedValueOnce({ ...diff, revision: 'wrong' });
  const view = render(<RevisionDiff scope={scope} revision={diff.revision} against={diff.against} />);
  view.rerender(<RevisionDiff scope={{ ...scope, botId: 'bot-b' }} revision={diff.revision} against={diff.against} />);
  await screen.findByRole('alert');
  await act(async () => resolveOld(diff));
  expect(screen.queryByText('modified · skills/editor/SKILL.md')).toBeNull();
});
