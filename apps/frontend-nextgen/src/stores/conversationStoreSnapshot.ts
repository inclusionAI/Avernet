// 选中会话的保留快照:类型与身份判据从 conversationStoreState 拆出(文件大小阈值),
// 语义与消费方不变。Session View 类型经 Domain 收口(Store 禁直连 Service,含 type import)。
import type {
  ConversationBotSection,
  ConversationOrigin,
  ConversationSessionScope,
  ConversationUserView,
  BotChatSessionView,
} from '@/domain/conversation';

/** 选中会话的保留快照载荷:身份字段与 selectConversation 的选中输入一致。
 *  session.sessionId 即被选中的会话;others 分组好友资料供只读舞台使用。 */
export interface SelectedSessionSnapshot {
  section: ConversationBotSection;
  botId: string;
  origin: ConversationOrigin;
  scope: ConversationSessionScope;
  friendUserId: string | null;
  session: BotChatSessionView;
  /** origin=others 时快照所在分组的好友资料;mine 恒 null。 */
  friend: ConversationUserView | null;
}

/** 快照是否仍归属于当前选中身份;身份变化(换 Bot/归属/范围/会话/清空选中)
 *  即失效。是占位遮挡与主会话解析的唯一保留判据。 */
export function selectedSnapshotMatches(
  snapshot: SelectedSessionSnapshot | null,
  selection: {
    botId: string | null;
    section: ConversationBotSection | null;
    origin: ConversationOrigin;
    scope: ConversationSessionScope;
    friendUserId: string | null;
    sessionId: string | null;
  },
): snapshot is SelectedSessionSnapshot {
  if (!snapshot || !selection.botId || !selection.section || !selection.sessionId) return false;
  return (
    snapshot.section === selection.section &&
    snapshot.botId === selection.botId &&
    snapshot.origin === selection.origin &&
    snapshot.scope === selection.scope &&
    snapshot.friendUserId === selection.friendUserId &&
    snapshot.session.sessionId === selection.sessionId
  );
}