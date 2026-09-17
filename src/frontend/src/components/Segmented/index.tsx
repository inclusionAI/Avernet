import React from 'react';
import { Button } from '../Button';

interface SegmentedProps {
  value: string;
  options: Array<{ value: string; label: React.ReactNode }>;
  onChange: (value: string) => void;
}

export function Segmented({ value, options, onChange }: SegmentedProps) {
  return <div role="group" aria-label="图形视图" className="inline-flex gap-1 rounded-lg bg-slate-100 p-1">
    {options.map((option) => <Button key={option.value} type="button" size="sm"
      variant={value === option.value ? 'secondary' : 'default'}
      soft={value === option.value} ghost={value !== option.value}
      aria-pressed={value === option.value} onClick={() => onChange(option.value)}>
      {option.label}
    </Button>)}
  </div>;
}
