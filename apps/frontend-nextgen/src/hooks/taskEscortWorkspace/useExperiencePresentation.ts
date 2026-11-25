import type { TaskEscortWorkspaceController } from '@/domain/taskEscortWorkspace/types';
import {
  experienceCategories,
  filterExperiences,
  getExperienceSummary,
} from '@/services/taskEscortWorkspace/experiencePresentation';
import { useMemo, useState } from 'react';

export function useExperiencePresentation(demo: TaskEscortWorkspaceController) {
  const [category, setCategory] = useState('all');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const categories = useMemo(() => experienceCategories(demo.experiences), [demo.experiences]);
  const experiences = useMemo(
    () => filterExperiences(demo.experiences, demo.query, category),
    [demo.experiences, demo.query, category],
  );
  const summary = useMemo(() => getExperienceSummary(demo.experiences), [demo.experiences]);
  const selected = demo.experiences.find((entry) => entry.id === selectedId) ?? null;
  const clear = () => {
    demo.setQuery('');
    setCategory('all');
  };
  const reference = () => {
    if (selected) demo.notice(`已打开「${selected.title}」的参考提示；未应用任何变更。`);
    setSelectedId(null);
  };
  return { category, setCategory, selected, setSelectedId, categories, experiences, summary, clear, reference };
}
