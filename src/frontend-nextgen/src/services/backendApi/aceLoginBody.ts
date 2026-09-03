/**
 * 网关级 ACE 登录拦截体的识别与登录链接提取(纯数据,无副作用)。
 *
 * 未登录时,TeamClaw 网关(ACE)在 HTTP 200 把所有后端接口的响应替换为登录体:
 *   { actionType:'LOGIN', buserviceErrorCode:'USER_NOT_LOGIN', decisionBy:'ACE',
 *     buserviceErrorMsg:<pubLogin 链接(含 goto 回跳)>, help }
 * 该体走「成功路径」到达(HTTP 2xx、response.ok),非 401。
 *
 * 本模块只做识别/取链,不碰 toast / DOM / 跳转——副作用由 loginRedirectStore +
 * 顶层观察者 useGatewayLoginRedirect 承载,守 Service 禁 toast/DOM 分层(见
 * openspec/changes/redirect-not-login-to-gateway-login/design.md D2)。
 *
 * 已知缺口:blob/字节流 content 下载路由(sessionFileController / botSessionFileController /
 * botSessionFileDownload)读 blob 不读 JSON,不在本能力覆盖范围,见 design.md Q4。
 */
export interface AceLoginBody {
  actionType: 'LOGIN';
  buserviceErrorCode: 'USER_NOT_LOGIN';
  decisionBy: 'ACE';
  buserviceErrorMsg: string;
  help?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

/**
 * 识别网关级 ACE 登录拦截体。三字段严格匹配(actionType + buserviceErrorCode + decisionBy),
 * 避免把业务错误体误判为登录拦截。网关始终下发 decisionBy:'ACE',故这一严格校验在真实流量下
 * 不漏识别,同时与现存 4 处实现里较严的两处一致、对较松的两处为安全收紧(既有 ACE 测试体均带 decisionBy)。
 */
export function isAceLoginResponse(value: unknown): value is AceLoginBody {
  return (
    isRecord(value) &&
    value.actionType === 'LOGIN' &&
    value.buserviceErrorCode === 'USER_NOT_LOGIN' &&
    value.decisionBy === 'ACE'
  );
}

/**
 * 从(已识别的 ACE 体或任意值)取出登录链接 buserviceErrorMsg。非 ACE 体、或链接缺失/非字符串/空白,
 * 一律返回 undefined。调用方据此决定是否触发跳转(undefined 不跳转)。
 */
export function extractLoginUrl(value: unknown): string | undefined {
  if (!isAceLoginResponse(value)) return undefined;
  const url = value.buserviceErrorMsg;
  return typeof url === 'string' && url.trim() !== '' ? url : undefined;
}
