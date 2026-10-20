/** Keep installation and onboarding in one shell, without upgrading an existing Bridge. */
export function withBridgeInstallation(command: string, installCommand: string, prepareCommand?: string): string {
  if (!command.trim() || !installCommand.trim()) return '';
  if (prepareCommand) return `(\n${prepareCommand} &&\n${command}\n)`;
  return `(
if command -v bcs-bridge >/dev/null 2>&1; then
  :
elif [ -x "$HOME/.local/bin/bcs-bridge" ]; then
  export PATH="$HOME/.local/bin:$PATH"
else
  (
${installCommand}
  ) &&
  export PATH="$HOME/.local/bin:$PATH" &&
  { command -v bcs-bridge >/dev/null 2>&1 || { printf '%s\\n' '安装后仍未找到 bcs-bridge，请检查安装结果和 PATH。' >&2; false; }; }
fi &&
${command}
)`;
}
