export interface BotActionPermissionsView {
  actions: string[];
  disabledActions: Record<string, string>;
}

export function mapBotActionPermissions(actions: unknown, disabledActions: unknown): BotActionPermissionsView {
  return {
    actions: Array.isArray(actions) ? actions.filter((action): action is string => typeof action === 'string') : [],
    disabledActions:
      disabledActions && typeof disabledActions === 'object'
        ? Object.fromEntries(
            Object.entries(disabledActions).filter((entry): entry is [string, string] => typeof entry[1] === 'string'),
          )
        : {},
  };
}
