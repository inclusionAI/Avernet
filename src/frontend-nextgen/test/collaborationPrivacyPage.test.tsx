/** @jest-environment jsdom */
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { TextDecoder, TextEncoder } from 'node:util';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { FriendApprovalEditor } from '../src/components/CollaborationPrivacy/FriendApprovalEditor';
import { IdentityCard } from '../src/components/CollaborationPrivacy/IdentityCard';
import {
  buildOrganizationColumns,
  toggleOrganizationPath,
} from '../src/components/CollaborationPrivacy/OrganizationPathSelector/model';
import { PermissionCard } from '../src/components/CollaborationPrivacy/PermissionCard';
import { PublicationEditor } from '../src/components/CollaborationPrivacy/PublicationEditor';
import { RelationCard } from '../src/components/CollaborationPrivacy/RelationCard';
import { RequestList } from '../src/components/CollaborationPrivacy/RequestList';
import { ScopeViewer } from '../src/components/CollaborationPrivacy/ScopeViewer';
import { ConfirmDialog } from '../src/components/ui/ConfirmDialog';
import { Switch } from '../src/components/ui/Switch';
import type { CollaborationBot } from '../src/domain/collaborationPrivacy/types';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));

Object.assign(globalThis, { TextDecoder, TextEncoder });
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const { renderToStaticMarkup } = require('react-dom/server') as typeof import('react-dom/server');

const bot: CollaborationBot = {
  id: '20260825_mbu0ey8f:447147',
  name: '产品协作助手',
  engine: 'OpenClaw',
  joinedBcn: true,
  collaborationStatus: 'online',
  profilePublic: true,
  taskClaimingEnabled: true,
  dreamModelEnabled: false,
  publication: { user: { scope: 'all', organizationPaths: [] }, bot: { scope: 'none', organizationPaths: [] } },
  pendingPublications: {},
  friendApproval: { mode: 'all', exemptOrganizationPaths: [] },
};

function renderWithPortals(element: React.ReactElement) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => root.render(element));
  const html = document.body.innerHTML;
  act(() => root.unmount());
  container.remove();
  return html;
}

describe('collaboration privacy accessible UI', () => {
  test('操作结果使用全局 Toast，不在页面内容区插入状态提示条', () => {
    const pageSource = readFileSync(path.join(process.cwd(), 'src/pages/CollaborationPrivacy/index.tsx'), 'utf8');
    const hookSource = readFileSync(path.join(process.cwd(), 'src/hooks/useCollaborationPrivacy.ts'), 'utf8');

    expect(pageSource).not.toContain('PageMessage');
    // 错误/成功经统一 notify 入口（全局上方居中、错误 6s、可关），页面 Hook 不单独设置位置。
    expect(hookSource).not.toContain("position: 'top-center'");
    expect(hookSource).toContain("from '@/components/ui/notify'");
    expect(hookSource).toContain('notifyError');
    expect(hookSource).toContain('notifySuccess');
    expect(hookSource).toContain('公开范围已关闭，当前已立即生效');
    expect(hookSource).toContain('审批申请已提交，当前公开范围保持不变');
    expect(hookSource).toContain("config.scope === 'none'");
    expect(hookSource).not.toContain('setFeedback');
    expect(hookSource).not.toContain('Mock 审批');
    expect(hookSource).not.toContain('（Mock）');
  });

  test('页面入口不包含开发验证文案，Mock 列表不展示 BCN 例外样例', () => {
    const pageSource = readFileSync(path.join(process.cwd(), 'src/pages/CollaborationPrivacy/index.tsx'), 'utf8');
    const mockPath = path.join(process.cwd(), 'mock/collaborationPrivacy.ts');
    // Open Core intentionally excludes the development mock tree.
    const mockSource = existsSync(mockPath) ? readFileSync(mockPath, 'utf8') : '';

    expect(pageSource).not.toContain('协作治理');
    expect(pageSource).not.toContain('Mock 验证');
    expect(pageSource).not.toContain('sample-only Mock 数据');
    expect(pageSource).toContain(
      '管理用户信息，以及归属于当前用户的所有 Bot 在 BCN 网络中的各类协作状态及好友审批策略。',
    );
    expect(mockSource).not.toContain('待接入助手');
    expect(mockSource).not.toContain('joined_bcn: false');
  });

  test('Switch 暴露标准 switch 语义和动态名称', () => {
    const html = renderToStaticMarkup(<Switch checked aria-label="开启任务认领" onCheckedChange={jest.fn()} />);
    expect(html).toContain('role="switch"');
    expect(html).toContain('aria-checked="true"');
    expect(html).toContain('aria-label="开启任务认领"');
  });

  test('ConfirmDialog 使用项目 Button 且提供 alertdialog 语义', () => {
    const html = renderWithPortals(
      <ConfirmDialog
        open
        title="确认隐藏 Bot"
        description="隐藏后将不再参与协作群。"
        onCancel={jest.fn()}
        onConfirm={jest.fn()}
      />,
    );
    expect(html).toContain('role="alertdialog"');
    expect(html).toContain('取消');
    expect(html).toContain('确认');
    expect(html).not.toContain('<dialog');
  });

  test('Bot 卡片不展示 Mock、BCN 或群聊状态标签', () => {
    const html = renderToStaticMarkup(
      <PermissionCard
        bot={bot}
        busyAction={null}
        onCopyId={jest.fn()}
        onRefresh={jest.fn()}
        onToggleDirect={jest.fn()}
        onEditPublication={jest.fn()}
        onEditFriendApproval={jest.fn()}
        onViewScope={jest.fn()}
        onViewFriendApprovalScope={jest.fn()}
      />,
    );
    expect(html).not.toContain('示例数据');
    expect(html).not.toContain('已加入 BCN');
    expect(html).not.toContain('未加入 BCN');
    expect(html).not.toContain('群聊在线');
    expect(html).not.toContain('群聊隐身');
    expect(html).toContain('OpenClaw');
    expect(html).not.toContain('bg-gray-');
    expect(html).not.toContain('animate-pulse');
    expect(html).toContain('>Bot UUID<');
    expect(html).toContain('title="20260825_mbu0ey8f:447147"');
    expect(html).not.toContain('Bot ID：20260825_mbu0ey8f:447147');
    expect(html).toContain('aria-label="复制 产品协作助手 的 Bot UUID"');
    expect(html).toContain('参与协作群聊');
    expect(html).toContain('控制当前 Bot 是否可参与群聊。关闭后无法加入新协作群，已加入的协作群也不再回复。');
    expect(html).toContain('允许其他用户在群聊中通过「融合模式」查看公开画像并进行跨 Bot 增量洞察。');
    expect(html).toContain('Bot 画像公开');
    expect(html).toContain('开启后，Bot 将每天自动扫描任务广场并认领可执行的任务。');
    expect(html).toContain('开启后，Bot 将每天基于用户数据（语雀、会议纪要等）挖掘潜在任务并推送。');
    expect(html).not.toContain('协作群可见');
    expect(html).not.toContain('在协作群中隐身');
    expect(html).toContain('text-xs font-semibold tracking-wide text-muted-foreground');
    expect(html).toContain('mb-3 flex items-center');
    expect(html).not.toContain('border-b border-border pb-2');
    expect(html).toContain('divide-y divide-border');

    const collaborationPrivacyHelpersSource = readFileSync(
      path.join(process.cwd(), 'src/hooks/collaborationPrivacyHelpers.ts'),
      'utf8',
    );
    expect(collaborationPrivacyHelpersSource).toContain(
      '开启后可加入新协作群，并在已加入的协作群会话中回复消息。好友单聊不受影响。',
    );
    expect(collaborationPrivacyHelpersSource).toContain(
      '关闭后无法加入新协作群，并停止在已加入的协作群会话中回复消息。好友单聊不受影响。',
    );
  });

  test('身份卡展示 user_id 工号和原始 dept_name，部门同步入口保持紧凑', () => {
    const html = renderToStaticMarkup(
      <IdentityCard
        identity={{
          displayName: '示例管理员',
          employeeNumber: '447147',
          departmentPath: ['示例集团-产品部'],
          lastSyncedAt: '2026-08-18T08:00:00.000Z',
        }}
        avatarUrl="https://avatar.example/admin.png"
        syncing={false}
        onSync={jest.fn()}
      />,
    );

    expect(html).toContain('aria-label="同步用户部门信息"');
    expect(html).toContain('src="https://avatar.example/admin.png"');
    expect(html).toContain('alt="示例管理员"');
    expect(html).not.toContain('UserRound');
    expect(html).not.toContain('title="同步用户部门信息"');
    expect(html).toContain('h-5 w-5 shrink-0 rounded-md p-0');
    expect(html).toContain('text-base font-semibold text-foreground');
    expect(html).toContain('text-xs text-muted-foreground');
    expect(html).toContain('text-xs leading-5 text-muted-foreground');
    expect(html).not.toContain('text-sm leading-6 text-[var(--color-muted)]');
    expect(html).toContain('工号 447147');
    expect(html).toContain('示例集团-产品部');
    expect(html).not.toContain('示例集团 / 产品部');
    expect(html).not.toContain('同步部门');
    expect(html).not.toContain('最近同步');
  });

  test('待审批公开范围只透出审批进度入口', () => {
    const html = renderToStaticMarkup(
      <RelationCard
        audience="user"
        config={{ scope: 'restricted', organizationPaths: [['示例集团', '产品部']] }}
        pending={{
          id: 'MOCK-WO-24081801',
          audience: 'user',
          target: { scope: 'all', organizationPaths: [] },
          submittedAt: '2026-08-18T09:30:00.000Z',
          approvalUrl: 'https://approval.example.com/ticket/MOCK-WO-24081801',
        }}
        onEdit={jest.fn()}
        onViewScope={jest.fn()}
      />,
    );

    expect(html).toContain('查看审批进度');
    expect(html).toContain('<a');
    expect(html).toContain('href="https://approval.example.com/ticket/MOCK-WO-24081801"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html).not.toContain('Mock 工单');
    expect(html).not.toContain('目标配置尚未生效');
    expect(html).not.toContain('>MOCK-WO-24081801<');
  });

  test('待审批响应没有安全审批地址时不渲染伪造入口', () => {
    const html = renderToStaticMarkup(
      <RelationCard
        audience="bot"
        config={{ scope: 'all', organizationPaths: [] }}
        pending={{
          id: 'PENDING-bot',
          audience: 'bot',
          target: { scope: 'restricted', organizationPaths: [['示例集团']] },
          submittedAt: '2026-08-18T09:30:00.000Z',
        }}
        onEdit={jest.fn()}
        onViewScope={jest.fn()}
      />,
    );

    expect(html).toContain('待审批');
    expect(html).not.toContain('查看审批进度');
    expect(html).not.toContain('/admin/work-orders');
  });

  test('组织级联支持可变层级且父级和中间层级均可选择', () => {
    const options = [
      ['示例集团', '平台事业群'],
      ['示例集团', '企业服务事业群', '协作平台部', '体验团队', '增长小组'],
      ['另一家公司'],
    ];

    expect(buildOrganizationColumns(options, [])).toHaveLength(1);
    expect(buildOrganizationColumns(options, ['示例集团', '企业服务事业群', '协作平台部', '体验团队'])).toHaveLength(5);
    expect(toggleOrganizationPath([], ['示例集团'])).toEqual([['示例集团']]);
    expect(toggleOrganizationPath([['示例集团']], ['示例集团'])).toEqual([]);

    const selectorSource = readFileSync(
      path.join(process.cwd(), 'src/components/CollaborationPrivacy/OrganizationPathSelector/index.tsx'),
      'utf8',
    );
    expect(selectorSource).toContain('overflow-x-auto');
    expect(selectorSource).toContain('w-56 shrink-0');
    expect(selectorSource).not.toContain('lg:grid-cols-4');
    expect(selectorSource).not.toContain('truncate');
    expect(selectorSource).not.toContain('已选团队范围');
    expect(selectorSource).not.toContain('团队节点');
  });

  test('好友审批摘要统一使用组织范围口径', () => {
    const onViewScope = jest.fn();
    const html = renderToStaticMarkup(
      <RequestList
        config={{ mode: 'partial_exempt', exemptOrganizationPaths: [['示例集团'], ['示例集团', '技术部']] }}
        disabled={false}
        onEdit={jest.fn()}
        onViewScope={onViewScope}
      />,
    );
    expect(html).toContain('部分组织免审批');
    expect(html).toContain('2 个免审批范围');
    expect(html).toContain('>查看<');
    expect(html).not.toContain('部分团队免审批');
  });

  test('好友审批免审批范围可独立查看具体组织路径', () => {
    const html = renderWithPortals(
      <ScopeViewer
        open
        kind="friendApproval"
        config={{
          mode: 'partial_exempt',
          exemptOrganizationPaths: [
            ['示例集团', '技术部'],
            ['示例集团', '产品部'],
          ],
        }}
        onClose={jest.fn()}
      />,
    );

    expect(html).toContain('好友申请免审批范围');
    expect(html).toContain('示例集团 / 技术部');
    expect(html).toContain('示例集团 / 产品部');
  });

  test('好友审批范围未解析出名称时不再把部门编码当作展示名称', () => {
    const html = renderWithPortals(
      <ScopeViewer
        open
        kind="friendApproval"
        config={{ mode: 'partial_exempt', exemptOrganizationPaths: [], exemptDepartmentNos: ['A4195', 'A4196'] }}
        onClose={jest.fn()}
      />,
    );

    expect(html).toContain('范围数量：2');
    expect(html).toContain('部门名称暂未加载');
    expect(html).not.toContain('部门编码：');
    expect(html).not.toContain('A4195');
    expect(html).not.toContain('A4196');
  });

  test('公开范围编辑器按对象使用易理解的发现与好友申请文案', () => {
    const userHtml = renderWithPortals(
      <PublicationEditor
        open
        audience="user"
        initialConfig={{ scope: 'all', organizationPaths: [] }}
        onSearch={jest.fn()}
        onClose={jest.fn()}
        onSubmit={jest.fn()}
      />,
    );
    expect(userHtml).toContain('其他用户无法发现当前 Bot');
    expect(userHtml).toContain('其他用户可发现并申请添加当前 Bot 为好友');
    expect(userHtml).toContain('仅所选组织范围可申请添加当前 Bot 为好友');
    expect(userHtml).not.toContain('该受众');
    expect(userHtml).not.toContain('所有主体');

    const botHtml = renderWithPortals(
      <PublicationEditor
        open
        audience="bot"
        initialConfig={{ scope: 'all', organizationPaths: [] }}
        onSearch={jest.fn()}
        onClose={jest.fn()}
        onSubmit={jest.fn()}
      />,
    );
    expect(botHtml).toContain('其他 Bot 无法发现当前 Bot');
    expect(botHtml).toContain('其他 Bot 可发现并申请添加当前 Bot 为好友');
    expect(botHtml).toContain('仅所选组织范围可申请添加当前 Bot 为好友');
  });

  test('公开范围编辑器阻止无变化提交并提供级联多选入口', () => {
    const html = renderWithPortals(
      <PublicationEditor
        open
        audience="user"
        initialConfig={{ scope: 'restricted', organizationPaths: [['示例集团', '事业部', '部门', '团队']] }}
        onSearch={jest.fn()}
        onClose={jest.fn()}
        onSubmit={jest.fn()}
      />,
    );
    expect(html).toContain('选择组织范围');
    expect(html).toContain('已选组织范围（1）');
    expect(html).toContain('示例集团 / 事业部 / 部门 / 团队');
    expect(html).toContain('配置未发生变化，无需提交审批');
    expect(html).not.toContain('Mock');
    expect(html).toContain('disabled=""');
  });

  test('公开范围和好友审批编辑器使用单选组语义', () => {
    const publicationHtml = renderWithPortals(
      <PublicationEditor
        open
        audience="user"
        initialConfig={{ scope: 'all', organizationPaths: [] }}
        onSearch={jest.fn()}
        onClose={jest.fn()}
        onSubmit={jest.fn()}
      />,
    );
    expect(publicationHtml).toContain('role="radiogroup"');
    expect(publicationHtml).toContain('aria-label="公开范围"');
    expect(publicationHtml).toContain('role="radio"');
    expect(publicationHtml).toContain('aria-checked="true"');

    const friendApprovalHtml = renderWithPortals(
      <FriendApprovalEditor
        open
        initialConfig={{ mode: 'all', exemptOrganizationPaths: [] }}
        onSearch={jest.fn()}
        onClose={jest.fn()}
        onSubmit={jest.fn()}
      />,
    );
    expect(friendApprovalHtml).toContain('role="radiogroup"');
    expect(friendApprovalHtml).toContain('aria-label="好友审批策略"');
    expect(friendApprovalHtml).toContain('role="radio"');
    expect(friendApprovalHtml).toContain('aria-checked="true"');
  });
});
