import type { Experience } from '@/domain/taskEscortWorkspace/types';

export function filterExperiences(experiences: Experience[], query: string, category: string): Experience[] {
  const term = query.trim().toLocaleLowerCase();
  return experiences.filter(
    (experience) =>
      (!category || category === 'all' || experience.category === category) &&
      (!term ||
        [experience.id, experience.title, experience.description, experience.workflowName, experience.category]
          .join(' ')
          .toLocaleLowerCase()
          .includes(term)),
  );
}

export function experienceCategories(experiences: Experience[]): string[] {
  return [...new Set(experiences.map((experience) => experience.category))];
}

export function getExperienceSummary(experiences: Experience[]) {
  return {
    verifiedCount: experiences.filter((entry) => entry.verified).length,
    reuseCount: experiences.reduce((total, entry) => total + entry.reuseCount, 0),
  };
}
