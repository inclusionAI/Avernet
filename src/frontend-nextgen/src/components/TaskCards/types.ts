export type TaskCardType = 'task_clarify' | 'task_multi_select' | 'task_ready';

export type TaskReadyData = {
  task_type?: 'dynamic' | 'workflow';
  workflow_id?: string;
  goal?: string;
  deliverables?: string[];
  acceptance_criteria?: string[];
  constraints?: string[];
  resources?: string[];
};

export type TaskCardData = {
  type?: TaskCardType;
  task?: TaskReadyData;
  goal?: string;
  deliverables?: string[];
  acceptance_criteria?: string[];
  constraints?: string[];
  resources?: string[];
  missing_fields?: string[];
  needs_confirmation?: string[];
  questions?: string[];
  tasks?: Array<{ index: number; summary: string }>;
  prompt?: string;
};

export type EditableField = 'goal' | 'deliverables' | 'acceptance_criteria' | 'constraints' | 'resources';
