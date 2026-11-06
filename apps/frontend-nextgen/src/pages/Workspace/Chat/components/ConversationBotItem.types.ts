import type {
  ConversationBotSection,
  ConversationOrigin,
  ConversationSessionListState,
  ConversationSessionScope,
} from '@/domain/conversation/types';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import type { ConversationBotItemOthers } from './ConversationBotOthersArea';
import type { ConversationSessionListActions } from '../hooks/useConversationSessionActions';

export type { ConversationBotItemOthers } from './ConversationBotOthersArea';

export interface ConversationBotItemProps {
  bot: ChatBotView;
  section: ConversationBotSection;
  expanded: boolean;
  /** 当前 Bot 下选中的 Session(跨 origin 高亮)。 */
  selectedSessionId: string | null;
  /** 该 Bot 是否为当前舞台所属 Bot(跨 section 判定由装配层完成):名称点亮 #09090B
   *  (text-foreground)作行锚点——收起时其下选中会话不可见,Bot 名顶位承载选中语义,
   *  与其下会话的灰阶选中胶囊形成「Bot → 会话」两级中性层次(2026-11-02 稿)。 */
  botSelected?: boolean;
  origin: ConversationOrigin;
  /** 生效读取范围(managed mine 用;friend 读 scopeByFriendBotId)。 */
  scope: ConversationSessionScope;
  /** 该 Bot 已生效的 sessionKey 条件(空串 = 未生效);仅 all 范围下允许提交新条件。 */
  sessionKey: string;
  /** mine / friend 交互会话列表;undefined 视为未加载(不判空)。 */
  sessions?: ConversationSessionListState;
  /** 仅 managed 传入:others 好友分组区数据。 */
  others?: ConversationBotItemOthers;
  /** Coding Bot 的 others 直接使用按 sessionId 分流后的扁平会话列表。 */
  flatCodingOthers?: boolean;
  onToggle(): void;
  onOriginChange(origin: ConversationOrigin): void;
  onScopeChange(scope: ConversationSessionScope): void;
  /** 提交 sessionKey 搜索(组件内已 trim;空串清除条件);装配层写 Store 并展开该 Bot。 */
  onSearchSession(sessionKey: string): void;
  onCreateSession(): void;
  onSelectSession(sessionId: string): void;
  onLoadMore(): void;
  onToggleFavorite?(sessionId: string): void;
  isFavoritePending(sessionId: string): boolean;
  /** 「Bot 管理」菜单项回调(#2990 还原):跳转该 Bot 所属空间的 Bot 管理页;
   *  好友 Bot 不传(菜单项不渲染);bot.spaceId 有值才显示菜单入口。 */
  onOpenBotManagement?(bot: ChatBotView): void;
  /** Bot 正在回复中的会话 id(#2990 还原):仅当前会话;透传会话行渲染 loading 指示。 */
  replyingSessionId?: string | null;
  /** 恢复的单聊会话操作(编辑标题/清除上下文/删除会话);只读/好友分组区不消费。 */
  sessionActions?: ConversationSessionListActions;
  /** Bot 级“新建会话”在途标记；行尾 + 与主舞台空态共享，防止重复提交。 */
  createSessionPending?: boolean;
}
