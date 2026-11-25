import type { BotOption } from '../../../types/monitoring';
import { MonitoringIcon as Icon } from './MonitoringIcon';

export function MonitoringEnrollment({ bot, onOpenSettings }: { bot: BotOption; onOpenSettings?: () => void }) {
  return <section className="enrollment-panel" aria-label="未监控 Bot">
    <span className="enrollment-art"><Icon name="bot" /></span>
    <h2>{bot.configuration?.diagnosisEnabled ? '诊断监控已开启，等待首次检查。' : '此 Bot 尚未开启诊断监控。'}</h2>
    {onOpenSettings && <button type="button" className="settings-entry" onClick={onOpenSettings}>诊断监控设置</button>}
    <p className="enrollment-identity">{bot.botName} · {bot.botId} · {bot.ownerId}</p>
  </section>;
}
