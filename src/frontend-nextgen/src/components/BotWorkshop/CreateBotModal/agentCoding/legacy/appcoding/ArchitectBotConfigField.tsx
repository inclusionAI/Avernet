/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * ArchitectBotConfigField - 域架构 Bot 配置字段。
 *
 * 该字段原本内联在 AppCodingConfigForm 中；抽离后可被应用 Coding
 * 和 AgentCoding 模板 Bot 的动态字段共同复用。
 */

import type { Bot } from '@/services/botWorkshop/agentCodingLegacyService';
import { DOMAIN_BOTS_PAGE_SIZE, searchDomainBots } from '@/services/botWorkshop/agentCodingLegacyService';
import { useEffect, useState } from 'react';
import DomainBotSelect from './DomainBotSelect';

export interface ArchitectBotConfigFieldProps {
  label?: string;
  required?: boolean;
  value?: string;
  disabled?: boolean;
  placeholder?: string;
  description?: string;
  onChange: (value: string) => void;
}

export function ArchitectBotConfigField({
  label = '域架构 Bot',
  required = false,
  value = '',
  disabled = false,
  placeholder = '选择负责此应用的架构 Bot',
  description,
  onChange,
}: ArchitectBotConfigFieldProps) {
  const [domainBots, setDomainBots] = useState<Bot[]>([]);
  const [domainBotsLoading, setDomainBotsLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const fetchDomainBots = async () => {
      setDomainBotsLoading(true);
      try {
        const res = await searchDomainBots({ page: 1, page_size: DOMAIN_BOTS_PAGE_SIZE });
        if (cancelled) return;

        if (res.success && res.data?.items) {
          // 过滤掉 bot_id 为 'default' 的异常数据，避免下拉选项 key 重复导致多选项高亮。
          setDomainBots(res.data.items.filter((bot) => bot.bot_id !== 'default'));
        }
      } catch (err) {
        console.error('[ArchitectBotConfigField] Failed to load domain bots:', err);
        // 应用 Bot 埋点 A5：配置加载失败 - DomainBot
        // 详见 docs/架构与规范/埋点/应用Bot埋点方案.md
      } finally {
        if (!cancelled) setDomainBotsLoading(false);
      }
    };

    fetchDomainBots();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="space-y-1.5">
      <label className="text-xs font-semibold text-slate-600 flex items-center gap-1">
        {label}
        {required ? (
          <span className="text-red-500">*</span>
        ) : (
          <span className="text-slate-400 font-normal">（可选）</span>
        )}
      </label>
      <DomainBotSelect
        value={value || ''}
        options={domainBots}
        loading={domainBotsLoading}
        disabled={disabled}
        onChange={onChange}
        placeholder={placeholder}
      />
      {description && <p className="text-[11px] leading-relaxed text-slate-400">{description}</p>}
    </div>
  );
}

export default ArchitectBotConfigField;
