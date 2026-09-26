type HorizontalOverflowTarget = Pick<HTMLElement, 'clientWidth' | 'scrollWidth'>;

export const isHorizontalTextOverflowing = (
  element: HorizontalOverflowTarget | null,
) => {
  if (!element) return false;
  return element.scrollWidth > element.clientWidth;
};
