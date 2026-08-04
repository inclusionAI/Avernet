export const COLLABORATION_FLOW_SPLIT_MIN_WIDTH = 1100;

const COLLABORATION_FLOW_MODAL_MAX_WIDTH = 1440;
const COLLABORATION_FLOW_MODAL_VIEWPORT_RATIO = 0.92;
const MODAL_HORIZONTAL_INSET = 32;

export function getCollaborationFlowAvailableWidth(viewportWidth: number) {
  return Math.max(
    0,
    Math.min(
      viewportWidth * COLLABORATION_FLOW_MODAL_VIEWPORT_RATIO,
      viewportWidth - MODAL_HORIZONTAL_INSET,
      COLLABORATION_FLOW_MODAL_MAX_WIDTH,
    ),
  );
}

export function shouldUseCompactCollaborationFlow(viewportWidth: number) {
  return (
    getCollaborationFlowAvailableWidth(viewportWidth) <
    COLLABORATION_FLOW_SPLIT_MIN_WIDTH
  );
}
