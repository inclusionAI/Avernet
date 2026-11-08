import type { BotOption } from '../../../types/monitoring';
import { MonitoringIcon as Icon } from './MonitoringIcon';

export function MonitoringEnrollment({ bot }: { bot: BotOption }) {
  return <section className="enrollment-panel" aria-label="未监控 Bot">
    <span className="enrollment-art"><Icon name="bot" /></span>
    <h2>此 Bot 尚未纳入监控范围。</h2><p>如有需要请联系@沐荛添加监控 Bot。</p>
    <p className="enrollment-identity">{bot.botName} · {bot.botId} · {bot.ownerId}</p>
  </section>;
}
