/**
 * 构建期默认开关的值形态源直测（review P2）：config.ts 的 umi 配置链在
 * jest 里驱动不了，值计算抽成 config/a2aBuildDefault.ts 纯函数在此钉死。
 * 断言的是「传给 umi define 的 JS 值形态」：布尔 true/false——旧实现
 * 手拼 "'true'" 字符串经 define 转换后 runtime 是带引号字面量、恒关。
 */
import { a2aClientBuildDefault } from '../../config/a2aBuildDefault';

describe('a2aClientBuildDefault（define 值形态单源）', () => {
  it('A2A_CLIENT_DEFAULT=true → 布尔 true（灰度镜像默认开）', () => {
    expect(a2aClientBuildDefault({ A2A_CLIENT_DEFAULT: 'true' })).toBe(true);
  });
  it('未注入 → 布尔 false（主线构建 off，零行为变化）', () => {
    expect(a2aClientBuildDefault({})).toBe(false);
  });
  it('其它取值一概 false（不认 1/yes/TRUE 等）', () => {
    expect(a2aClientBuildDefault({ A2A_CLIENT_DEFAULT: '1' })).toBe(false);
    expect(a2aClientBuildDefault({ A2A_CLIENT_DEFAULT: 'TRUE' })).toBe(false);
    expect(a2aClientBuildDefault({ A2A_CLIENT_DEFAULT: 'false' })).toBe(false);
  });
});
