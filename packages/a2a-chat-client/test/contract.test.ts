/**
 * 双 SDK 行为契约套件的包内自证：对 devDep @tc-chat 跑绿。
 * ============================================================================
 * 契约本体在 src/testing.ts（公共出口 ./testing）。本文件用**包自含
 * oracleLoader**（不经任何宿主文件）注入 factory——证明包独立可验证；
 * 两个宿主（nextgen 对开源 SDK、ocb 将来对内部聊天 SDK）以同一套件
 * 各自跑绿后才算消费达成（README 消费契约）。
 */
import { createOracleParserFactory } from './helpers/oracleLoader';
import { createParserContractSuite } from '../src/testing';

createParserContractSuite(createOracleParserFactory(), {
  suiteName: 'a2a parser behavior contract (tc-chat 2.1.0, package oracle)',
});