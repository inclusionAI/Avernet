export function validUri(value) {
  if (typeof value !== 'string') return false;
  // RFC 3986 Appendix A：先检查原文，避免 URL 解析器容错或编码非法字符。
  if (/[^\x21-\x7e]/.test(value)) return false;
  const match = /^[a-z][a-z0-9+.-]*:(?:\/\/([^/?#]*))?([^?#]*)(?:\?([^#]*))?(?:#(.*))?$/i.exec(value);
  if (!match) return false;
  const [, authority = '', path, query = '', fragment = ''] = match;
  const authorityChars = /^(?:(?:[a-z0-9._~!$&'()*+,;=:-]|%[0-9a-f]{2})*@)?(?:\[[a-z0-9:.]+\]|(?:[a-z0-9._~!$&'()*+,;=-]|%[0-9a-f]{2})*)(?::[0-9]*)?$/i;
  const pathChars = /^(?:[a-z0-9._~!$&'()*+,;=:@/-]|%[0-9a-f]{2})*$/i;
  const queryChars = /^(?:[a-z0-9._~!$&'()*+,;=:@/?-]|%[0-9a-f]{2})*$/i;
  if (!authorityChars.test(authority) || !pathChars.test(path) || !queryChars.test(query) || !queryChars.test(fragment)) return false;
  try {
    const parsed = new URL(value);
    if (['http:', 'https:', 'ftp:', 'ws:', 'wss:'].includes(parsed.protocol)) {
      return /^[a-z]+:\/\/[^/?#]/i.test(value) && Boolean(parsed.hostname);
    }
    return Boolean(parsed.hostname || parsed.pathname);
  } catch {
    return false;
  }
}

