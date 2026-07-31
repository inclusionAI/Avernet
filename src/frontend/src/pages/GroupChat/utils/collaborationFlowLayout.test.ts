import {
  COLLABORATION_FLOW_SPLIT_MIN_WIDTH,
  getCollaborationFlowAvailableWidth,
  shouldUseCompactCollaborationFlow,
} from './collaborationFlowLayout';

describe('collaboration flow responsive layout', () => {
  it('uses the split layout when the expanded modal can provide enough width', () => {
    expect(getCollaborationFlowAvailableWidth(1440)).toBe(1324.8);
    expect(shouldUseCompactCollaborationFlow(1440)).toBe(false);
  });

  it('keeps the original modal width when the expanded layout would be too narrow', () => {
    expect(getCollaborationFlowAvailableWidth(1000)).toBe(920);
    expect(shouldUseCompactCollaborationFlow(1000)).toBe(true);
  });

  it('switches layouts at the minimum split width', () => {
    const exactViewportWidth = COLLABORATION_FLOW_SPLIT_MIN_WIDTH / 0.92;

    expect(shouldUseCompactCollaborationFlow(exactViewportWidth)).toBe(false);
    expect(shouldUseCompactCollaborationFlow(exactViewportWidth - 1)).toBe(
      true,
    );
  });
});
