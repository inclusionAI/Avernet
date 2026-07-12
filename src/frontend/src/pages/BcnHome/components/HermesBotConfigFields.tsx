import React from 'react';
import type { HermesBotConfigValidation } from '../lib/botAccess';

interface HermesBotConfigFieldsProps {
  idPrefix: string;
  botName: string;
  profile: string;
  validation: HermesBotConfigValidation;
  onBotNameChange: (value: string) => void;
  onProfileChange: (value: string) => void;
}

export const HermesBotConfigFields: React.FC<HermesBotConfigFieldsProps> = ({
  idPrefix,
  botName,
  profile,
  validation,
  onBotNameChange,
  onProfileChange,
}) => (
  <div className="grid gap-3 sm:grid-cols-2">
    <label
      className="text-xs font-medium text-[#52606d]"
      htmlFor={`${idPrefix}-bot-name`}
    >
      Bot 名称
      <input
        id={`${idPrefix}-bot-name`}
        value={botName}
        onChange={(event) => onBotNameChange(event.target.value)}
        placeholder="例如 Hermes Reviewer"
        className="mt-1 h-9 w-full rounded-md border border-[#d9e0ea] bg-white px-3 text-sm text-[#1a2332] outline-none focus:border-[#1d4ed8]"
      />
      {validation.botNameError && (
        <span className="mt-1 block text-xs text-[#dc2626]">
          {validation.botNameError}
        </span>
      )}
    </label>
    <label
      className="text-xs font-medium text-[#52606d]"
      htmlFor={`${idPrefix}-profile`}
    >
      Profile 名称
      <input
        id={`${idPrefix}-profile`}
        value={profile}
        onChange={(event) => onProfileChange(event.target.value)}
        placeholder="例如 avernet-hermes-2"
        className="mt-1 h-9 w-full rounded-md border border-[#d9e0ea] bg-white px-3 font-mono text-sm text-[#1a2332] outline-none focus:border-[#1d4ed8]"
      />
      {validation.profileError && (
        <span className="mt-1 block text-xs text-[#dc2626]">
          {validation.profileError}
        </span>
      )}
    </label>
  </div>
);
