/** Quote every argument; user input and tokens must remain literal shell values. */
export function quoteShellArg(value: string): string {
  if (/^[A-Za-z0-9_./:@=-]+$/.test(value)) return value;
  return `'${value.replace(/'/g, `'"'"'`)}'`;
}

export function getBotRegistrationNameError(botName: string): string | null {
  if (botName.trim().length < 2 || botName.trim().length > 64) return 'Bot 名称需要填写 2–64 个字符。';
  return null;
}

export function getCodexRegistrationInputError(botName: string, cwd: string): string | null {
  const nameError = getBotRegistrationNameError(botName);
  if (nameError) return nameError;
  if (!cwd.trim()) return '请填写项目目录（绝对路径或 ~/ 开头的路径）。';
  if (!/^(?:\/|~\/|[A-Za-z]:[\\/])/.test(cwd.trim())) return '项目目录需要使用绝对路径或 ~/ 开头的路径。';
  return null;
}
