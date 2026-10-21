/**
 * 包自含 oracle 加载器（C2 迁移件）——**禁止 import 宿主 SDK 适配器**
 *（宿主 tcChatParser 属各前端自备；包离开 nextgen 后必须可独立验证）。
 * 以 v3-frame-shapes.test 的 test-scoped 同步加载器为蓝本：包安装位直取
 * （devDep @tc-chat/adapters 2.1.0；jest 经 babel 转译后 require 透明），
 * A2A_OPENCLAW_PARSER_PATH 显式覆盖兜底。失败即抛——真 parser 就是 oracle。
 */
import * as fs from 'fs';
import type {
  SdkParser, SdkParserCtor, SdkParserConfig, SdkParserFactory,
} from '../../src/sdkParser';

/** 同步加载真 OpenClawParser（test 环境专用：require.resolve 候选 + env 覆盖） */
export function loadOpenClawParser(): SdkParserCtor {
  const relay = '@tc-chat/adapters/dist/openclaw/OpenClawParser.js';
  const candidates: Array<() => string | null> = [
    () => {
      try {
        return require.resolve(relay);
      } catch {
        return null;
      }
    },
    () => process.env.A2A_OPENCLAW_PARSER_PATH || null,
  ];
  for (const candidate of candidates) {
    const resolved = candidate();
    if (resolved && fs.existsSync(resolved)) {
      const mod = require(resolved) as { OpenClawParser?: SdkParserCtor };
      if (mod && mod.OpenClawParser) return mod.OpenClawParser;
    }
  }
  throw new Error(
    'OpenClawParser oracle not found: install @tc-chat/adapters or ' +
      'set A2A_OPENCLAW_PARSER_PATH to dist/openclaw/OpenClawParser.js',
  );
}

/** oracle 版 parserFactory：构造参数形状与宿主注入缝一致（同步返回） */
export function createOracleParserFactory(): SdkParserFactory {
  return (config) => {
    const Ctor = loadOpenClawParser();
    return new Ctor(config) as SdkParser;
  };
}
