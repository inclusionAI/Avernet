import type { PageId, ViewProps } from '@/domain/taskEscortWorkspace';
import type { ComponentType } from 'react';
import { lazy } from 'react';
import { Dashboard } from './Dashboard';

export const views: Record<PageId, ComponentType<ViewProps>> = {
  dashboard: Dashboard,
  workbench: lazy(() => import('./Workbench').then((module) => ({ default: module.Workbench }))),
  runs: lazy(() => import('./Runs').then((module) => ({ default: module.Runs }))),
  issues: lazy(() => import('./Issues').then((module) => ({ default: module.Issues }))),
  experiences: lazy(() => import('./Experiences').then((module) => ({ default: module.Experiences }))),
  editor: lazy(() => import('./Editor').then((module) => ({ default: module.Editor }))),
  settings: lazy(() => import('./Settings').then((module) => ({ default: module.Settings }))),
};
