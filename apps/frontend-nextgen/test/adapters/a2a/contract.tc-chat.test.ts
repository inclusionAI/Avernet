/**
 * B16 — 双 SDK 契约套件的 nextgen 侧消费：对 @tc-chat/adapters 跑绿。
 * ============================================================================
 * 契约本体在 src/adapters/a2a/testing.ts（公共面，OCB 消费公共核心时对其内部
 * 聊天 SDK 跑同一套 createParserContractSuite——行为对拍门槛，不凭 TS 类型
 * 兼容即认定一致）。本文件只做接线：注入 nextgen 的 @tc-chat parserFactory。
 */
import { createTcChatParserFactory } from '@/adapters/a2a/tcChatParser';
import { createParserContractSuite } from '@avernet/a2a-chat-client/testing';

createParserContractSuite(createTcChatParserFactory(), {
  suiteName: 'a2a parser behavior contract (tc-chat 2.1.0)',
});