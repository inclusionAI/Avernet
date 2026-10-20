/**
 * B16 — botChatProvider / useBotChat A2A bootstrap 接缝静态钉板（surgical diff guard）。
 * ============================================================================
 *
 * 镜像 stage2 legacy 侧 useMultiSessionChat.a2a.seam.test.ts 的字面级钉板口径
 * （nextgen 的 bot 单聊 provider 构造缝在 BotChatProvider.initialize 的 SDK 工厂，
 * 非多会话 hook 工厂）：
 *   ① v3 构造行逐字节等价且唯一（B16 之前即存在的行，零改动——本文件只钉不
 *      评估；构造字面量变化的评审面归 providerConfig 快照钉）；
 *   ② factory 入参 providerConfig 成员快照钉住（v3 / A2A 两条候选共享同一份
 *      config 对象，A2A 的 url/sessionKey/xIAMToken/enableThinkingTag 透传全靠它）；
 *   ③ A2A 触线行数上限（钉死手术刀口径——扩张必须改本钉板评审）；
 *   ④ 发送门（§3.1）/ 取消四态播报（B12）/ 文案单源的接线字面存在。
 * 行为面的断言（决策表/锁/禁令/取消四态核销）在
 * test/adapters/a2a/bootstrap.test.ts 与 provider.test.ts 全覆盖。
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const PROVIDER_SOURCE = readFileSync(
  join(__dirname, '..', '..', '..', 'src', 'services', 'workspace', 'botChatProvider.ts'),
  'utf8',
);
const HOOK_SOURCE = readFileSync(
  join(
    __dirname, '..', '..', '..',
    'src', 'pages', 'Workspace', 'hooks', 'useBotChat.ts',
  ),
  'utf8',
);
const BOOTSTRAP_SOURCE = readFileSync(
  join(__dirname, '..', '..', '..', 'src', 'adapters', 'a2a', 'bootstrap.ts'),
  'utf8',
);
const CANCEL_OUTCOMES_SOURCE = readFileSync(
  join(__dirname, '..', '..', '..', 'src', 'adapters', 'a2a', 'cancelOutcomes.ts'),
  'utf8',
);
const POLICY_SOURCE = readFileSync(
  join(__dirname, '..', '..', '..', 'src', 'adapters', 'a2a', 'policy.ts'),
  'utf8',
);

const V3_LINE = 'return new OpenClawProvider(cfg);';

const countOf = (source: string, needle: string): number =>
  source.split(needle).length - 1;

const a2aTouchingLines = HOOK_SOURCE.split('\n').filter((line) =>
  /adapters\/a2a|A2A M2 bootstrap seam|isSendBanned|subscribeToCancelOutcome|CANCEL_UI_TEXT|SEND_BAN_UI_TEXT/.test(
    line,
  ),
);

describe('botChatProvider A2A bootstrap 接缝钉板（B16 static guard）', () => {
  it('v3 构造行逐字节等价且唯一（零改动红线）', () => {
    expect(PROVIDER_SOURCE).toContain(V3_LINE);
    expect(countOf(PROVIDER_SOURCE, V3_LINE)).toBe(1);
  });

  it('factory 入参 providerConfig 成员快照钉住（v3/A2A 共享 config 的证据带）', () => {
    expect(PROVIDER_SOURCE).toContain('const inner = factory({');
    expect(PROVIDER_SOURCE).toContain('      url,');
    expect(PROVIDER_SOURCE).toContain('      sessionKey: this.options.sessionId,');
    expect(PROVIDER_SOURCE).toContain('      xIAMToken: this.iamToken,');
    expect(PROVIDER_SOURCE).toContain('      enableThinkingTag: true,');
    expect(PROVIDER_SOURCE).toContain("      fallbackMessage: '请求失败,请稍后重试',");
    expect(PROVIDER_SOURCE).toContain('      immediateConnect: true,');
  });

  it('工厂接缝：缓存命中同步换入 A2AProvider；未决/落 v3 时本轮走 v3（B13 两段式）', () => {
    expect(PROVIDER_SOURCE).toContain(
      'const a2aProvider = takeCachedA2AProvider(cfg as A2aBootstrapConfig);',
    );
    expect(PROVIDER_SOURCE).toContain(
      'return a2aProvider as unknown as OpenClawProvider;',
    );
  });

  it('A2A M2 bootstrap seam 标记：provider=3（wsUrl 锚/谓词+订阅面/工厂），hook=2（取消播报/发送门）', () => {
    expect(countOf(PROVIDER_SOURCE, 'A2A M2 bootstrap seam')).toBe(3);
    expect(countOf(HOOK_SOURCE, 'A2A M2 bootstrap seam')).toBe(2);
  });

  it('A2A 触线行数精确钉死（8）——扩张必须改本钉板评审', () => {
    expect(a2aTouchingLines.length).toBe(8);
  });

  it('取消四态播报接线（B12：provider 转发面 + hook toast 消费，文案单源 CANCEL_UI_TEXT）', () => {
    expect(PROVIDER_SOURCE).toContain(
      'a2aProvider.onCancelOutcome = (outcome) => this.emitCancelOutcome(outcome);',
    );
    expect(HOOK_SOURCE).toContain(
      'return provider.subscribeToCancelOutcome?.((outcome) => {',
    );
    expect(HOOK_SOURCE).toContain('toast(CANCEL_UI_TEXT[outcome.kind]);');
  });

  it('发送门：provider 侧纯谓词（§3.1 同键喂 registry），hook 侧入口拦截 + toast', () => {
    expect(PROVIDER_SOURCE).toContain(
      'return this.wsUrl !== null',
    );
    expect(PROVIDER_SOURCE).toContain(
      'isSendBannedForSession(this.options.sessionId, this.wsUrl, this.a2aBaseUrl ?? undefined);',
    );
    expect(PROVIDER_SOURCE).toContain('this.wsUrl = url;');
    expect(HOOK_SOURCE).toContain('if (provider?.isSendBanned?.()) {');
    expect(HOOK_SOURCE).toContain('toast.error(SEND_BAN_UI_TEXT);');
  });

  it('B17 服务端签发 A2A 面：连接响应 kind=a2a socket → cfg.a2aBaseUrl 透传到 SDK 工厂', () => {
    // 连接面读取：kind='a2a' 的 socket 原样收下；缺席时 a2aBaseUrl 恒 null
    expect(PROVIDER_SOURCE).toContain("find((s) => s.kind === 'a2a')");
    // 透传行：签发值进 factory cfg（v3 构造行保持零改动红线）
    expect(PROVIDER_SOURCE).toContain(
      '...(this.a2aBaseUrl ? { a2aBaseUrl: this.a2aBaseUrl } : {}),',
    );
    // 双锚初始化：wsUrl（§3.1 门）与 a2aBaseUrl（B17）同源同拍落位
    expect(PROVIDER_SOURCE).toContain('this.a2aBaseUrl = a2aBaseUrl;');
  });

  it('发送门文案单源在 policy.ts（review R2-4 外提），bootstrap 持 re-export 维持消费面；取消文案矩阵单源在 cancelOutcomes.ts', () => {
    // R2-4：策略纯函数/常量自 bootstrap 外提 policy.ts（公共入口不得传递依赖
    // 宿主装配面）——文案字面量的"唯一来源"随迁，bootstrap 只被允许 re-export
    expect(POLICY_SOURCE).toContain(
      "export const SEND_BAN_UI_TEXT = '该会话暂不可发送（底层执行停止未确认）';",
    );
    expect(BOOTSTRAP_SOURCE).toContain(
      "A2A_CHOICE_SESSION_KEY, SEND_BAN_UI_TEXT,\n  type A2aBootstrapConfig,\n} from './policy';",
    );
    expect(CANCEL_OUTCOMES_SOURCE).toContain(
      'export const CANCEL_UI_TEXT: Record<CancelOutcomeKind, string> = {',
    );
    expect(CANCEL_OUTCOMES_SOURCE).toContain(
      "'cancel-unknown': '取消结果未知，尝试查询任务状态',",
    );
  });
});