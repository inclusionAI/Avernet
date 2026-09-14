import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import RunEvolutionAnalysis from '../../evolution/RunEvolutionAnalysis';

it('shows only the selected diagnosis and its original proposal, with no apply control', () => {
  const diagnosis = (id: string, summary?: string) => ({ diagnosisId: id, failureSignature: id, reasoning: `reason-${id}`,
    sourceEvidence: [], evidenceEventIds: [], proposal: summary ? { summary } : undefined });
  const analysis = { facts: [], inferences: [], unknowns: [], diagnoses: [diagnosis('a', 'original-a'), diagnosis('b')], evidenceStatus: 'missing' };
  const { rerender } = render(<RunEvolutionAnalysis analysis={analysis as never} variant="evidence" focusDiagnosisId="a" />);
  expect(screen.getByText('reason-a')).toBeTruthy();
  expect(screen.getByText('original-a')).toBeTruthy();
  expect(screen.queryByRole('button', { name: /应用/ })).toBeNull();
  rerender(<RunEvolutionAnalysis analysis={analysis as never} variant="evidence" focusDiagnosisId="b" />);
  expect(screen.queryByText('original-a')).toBeNull();
  expect(screen.getByText('本次未生成建议')).toBeTruthy();
});
