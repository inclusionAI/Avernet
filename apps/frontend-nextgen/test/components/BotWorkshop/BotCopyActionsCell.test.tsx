/** @jest-environment jsdom */
/**
 * BotCopyActionsCell owner 门控契约(对应 Dima 2026111900120769162 / AC-2~AC-6):
 * copy 态(copyState 非空 = 复制中 / 复制失败)下重试 + 删除两按钮仅 owner 显示;
 * 非 owner 两按钮均不渲染(操作列为空,取舍 A)。owner 严格口径 bot.ownerId === currentUserId,
 * 与 useBotWorkshop.canRestartContainer 同款;currentUserId 缺省(身份未就绪)保守为非 owner。
 */
import { BotCopyActionsCell } from '@/components/BotWorkshop/BotCard/BotCopyActionsCell';
import type { BotCopyStateSummary } from '@/domain/botWorkshop';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { render, screen } from '@testing-library/react';

// bot_type=personal → legacyLifecycle:display_state 'running'→running,'pending'→deploying,
// 'failed'→failed(见 botLifecycleResolver DISPLAY_STATE_LIFECYCLE)。
const STATE_RUNNING = 'running';
const STATE_DEPLOYING = 'pending';

const OWNER = 'u_owner';

/** 用 mapBotDto 构造真实 BotDomain,再设 ownerId / lifecycle 驱动门控分支。 */
function makeBot(opts: { ownerId: string; displayState: string }) {
  const { item } = mapBotDto({
    bot_id: 'dst_1',
    bot_name: '副本 Bot',
    active_engine: 'openclaw',
    bot_type: 'personal',
    display_state: opts.displayState,
    owner_entity_id: opts.ownerId,
    actions: ['view', 'edit', 'delete'],
  } as Record<string, unknown>);
  return item;
}

const cp = (status: BotCopyStateSummary['status']): BotCopyStateSummary => ({
  operationId: 'op_1',
  status,
  sourceBotId: 'src_1',
});

const onAction = jest.fn(async () => undefined);
const onRetryCopy = jest.fn(() => undefined);
const noopDeleteAction = jest.fn(async () => undefined);

function renderCell(props: {
  ownerId: string;
  status: BotCopyStateSummary['status'];
  displayState: string;
  currentUserId?: string;
}) {
  const bot = makeBot({ ownerId: props.ownerId, displayState: props.displayState });
  return render(
    <BotCopyActionsCell
      bot={bot}
      copyState={cp(props.status)}
      onRetryCopy={onRetryCopy}
      onAction={noopDeleteAction}
      currentUserId={props.currentUserId}
    />,
  );
}

// 重试按钮文案来自 BotCopyRetryAction(「重试」);删除按钮文案来自 BotCopyDeleteAction(「删除」)。
const retry = () => screen.queryByText('重试');
const del = () => screen.queryByText('删除');

describe('BotCopyActionsCell — owner 门控', () => {
  beforeEach(() => {
    onAction.mockClear();
    onRetryCopy.mockClear();
    noopDeleteAction.mockClear();
  });

  it('AC-2: 复制中(status≠FAILED)·非 owner → 重试/删除均不渲染(空)', () => {
    renderCell({ ownerId: OWNER, status: 'BOT_STARTED', displayState: STATE_RUNNING, currentUserId: 'other_user' });
    expect(retry()).toBeNull();
    expect(del()).toBeNull();
  });

  it('AC-3: 复制中·owner·running → 仅删除(无重试);owner 行为不变', () => {
    renderCell({ ownerId: OWNER, status: 'BOT_STARTED', displayState: STATE_RUNNING, currentUserId: OWNER });
    expect(retry()).toBeNull();
    expect(del()).not.toBeNull();
  });

  it('AC-3: 复制中·owner·非 running(中间态 deploying) → 空', () => {
    renderCell({ ownerId: OWNER, status: 'INITIAL', displayState: STATE_DEPLOYING, currentUserId: OWNER });
    expect(retry()).toBeNull();
    expect(del()).toBeNull();
  });

  it('AC-4: 复制失败(FAILED)·非 owner → 重试/删除均不渲染(空)', () => {
    renderCell({ ownerId: OWNER, status: 'FAILED', displayState: STATE_RUNNING, currentUserId: 'other_user' });
    expect(retry()).toBeNull();
    expect(del()).toBeNull();
  });

  it('AC-5: 复制失败·owner·running → 重试 + 删除', () => {
    renderCell({ ownerId: OWNER, status: 'FAILED', displayState: STATE_RUNNING, currentUserId: OWNER });
    expect(retry()).not.toBeNull();
    expect(del()).not.toBeNull();
  });

  it('AC-5: 复制失败·owner·非 running(deploying) → 仅重试(删除需 running)', () => {
    renderCell({ ownerId: OWNER, status: 'FAILED', displayState: STATE_DEPLOYING, currentUserId: OWNER });
    expect(retry()).not.toBeNull();
    expect(del()).toBeNull();
  });

  it('currentUserId 缺省(身份未就绪) → 保守为非 owner,两按钮均不渲染', () => {
    renderCell({ ownerId: OWNER, status: 'FAILED', displayState: STATE_RUNNING /* currentUserId undefined */ });
    expect(retry()).toBeNull();
    expect(del()).toBeNull();
  });
});
