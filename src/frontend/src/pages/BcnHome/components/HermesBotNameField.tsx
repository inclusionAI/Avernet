import React from 'react';

interface HermesBotNameFieldProps {
  idPrefix: string;
  botName: string;
  botNameError: string | null;
  onBotNameChange: (value: string) => void;
}

export const HermesBotNameField: React.FC<HermesBotNameFieldProps> = ({
  idPrefix,
  botName,
  botNameError,
  onBotNameChange,
}) => (
  <label
    className="block text-xs font-medium text-[#52606d]"
    htmlFor={`${idPrefix}-bot-name`}
  >
    Bot 名称
    <input
      id={`${idPrefix}-bot-name`}
      value={botName}
      onChange={(event) => onBotNameChange(event.target.value)}
      aria-invalid={botNameError ? true : undefined}
      aria-describedby={
        botNameError ? `${idPrefix}-bot-name-error` : undefined
      }
      placeholder="例如 Hermes Reviewer"
      className="mt-1 h-9 w-full rounded-md border border-[#d9e0ea] bg-white px-3 text-sm text-[#1a2332] outline-none focus:border-[#1d4ed8]"
    />
    {botNameError && (
      <span
        id={`${idPrefix}-bot-name-error`}
        className="mt-1 block text-xs text-[#dc2626]"
      >
        {botNameError}
      </span>
    )}
  </label>
);
