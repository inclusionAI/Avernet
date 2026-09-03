/**
 * Helpers shared by the BCN access cards and the GroupChat onboarding modal.
 *
 * The browser only renders/copies commands.  The command is executed locally
 * by the user, so values inserted into the DSH installer must be shell quoted
 * before they are copied to the clipboard.
 */

const HTTP_SCHEMES = new Set(['http:', 'https:']);

/** DSH CLI installation hint shown when the local command is unavailable. */
export const DSH_CLI_INSTALL_COMMAND =
  'npm install --global @deepseek-ai/dsh@0.1.1-rc.2';

/** Validate and normalize a configured BCN HTTP endpoint. */
export function normalizeBcnEndpoint(
  configuredValue: string | null | undefined,
): string | null {
  const configured = configuredValue?.trim();
  if (!configured) return null;

  try {
    const url = new URL(configured);
    if (
      !HTTP_SCHEMES.has(url.protocol) ||
      !url.hostname ||
      url.username ||
      url.password ||
      url.search ||
      url.hash
    ) {
      return null;
    }
    // The installer canonicalizes the endpoint itself; keeping the path here
    // lets deployments expose BCS behind a reverse-proxy prefix.
    return configured.replace(/\/+$/, '');
  } catch {
    console.warn('[accessCommands] Ignoring invalid BCN endpoint:', configured);
    return null;
  }
}

/** Quote a value for a POSIX shell command copied from the web UI. */
export function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'"'"'`)}'`;
}

/**
 * Render a DSH installer template.  Templates deliberately use unquoted
 * placeholders so this helper owns escaping for endpoint and Token values.
 */
export function renderDshConnectCommand(
  template: string,
  endpoint: string | null,
  token: string | null,
): string {
  return template
    .replaceAll('{endpoint}', shellQuote(endpoint ?? '<BCN_ENDPOINT>'))
    .replaceAll('{token}', shellQuote(token ?? '<YOUR_TOKEN>'));
}
