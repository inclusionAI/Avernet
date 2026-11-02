/** TeamClaw registration address; personnel Owner remains a separate field. */
export function botReference(bot: {
  bot_id: string;
  bot_type?: string;
  owner_id: string;
  entity_id: string;
  ext?: { bcn_provider_bot_ref?: string } | null;
}): string {
  if (bot.bot_type === 'service' && bot.bot_id !== 'default') {
    return bot.ext?.bcn_provider_bot_ref || `${bot.bot_id}:${bot.entity_id}`;
  }
  return `${bot.bot_id}:${bot.owner_id}`;
}
