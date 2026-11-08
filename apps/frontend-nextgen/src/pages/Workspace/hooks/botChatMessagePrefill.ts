/**
 * Bot 单聊发送前的本地乐观消息富化(纯函数,自 useBotChat 抽出)。
 * 仅富化本地乐观消息:把 `<file-ref insert_id="X">` 补上 name,供气泡渲染文件名胶囊。
 */

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function enrichFileRefContent(content: string, fileRefDisplay?: Array<{ insert_id: string; name: string }>): string {
  if (!fileRefDisplay || fileRefDisplay.length === 0) return content;
  let out = content;
  for (const ref of fileRefDisplay) {
    if (!ref.insert_id || !ref.name) continue;
    const escName = ref.name.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const openTag = new RegExp(
      `(<file-ref\\b[^>]*?\\binsert_id=["']${escapeRegExp(ref.insert_id)}["'][^>]*?)(\\s*/?>)`,
      'i',
    );
    out = out.replace(openTag, (match, pre, tail) =>
      /\bname\s*=/i.test(pre) ? match : `${pre} name="${escName}"${tail}`,
    );
  }
  return out;
}