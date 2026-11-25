import React, { useMemo } from 'react';
import type { ReactNode } from 'react';
import { createLowlight } from 'lowlight';
import yaml from 'highlight.js/lib/languages/yaml';

const highlighter = createLowlight({ yaml });
type HighlightNodes = ReturnType<typeof highlighter.highlight>['children'];

function renderNodes(nodes: HighlightNodes): ReactNode {
  return nodes.map((node, index) => {
    if (node.type === 'text') return node.value;
    if (node.type !== 'element') return null;
    const classes = node.properties.className;
    return (
      <span key={index} className={Array.isArray(classes) ? classes.join(' ') : undefined}>
        {renderNodes(node.children)}
      </span>
    );
  });
}

export default function HighlightedYaml({ source }: { source: string }) {
  // Render text nodes, never HTML: indentation, line endings and literal markup
  // remain exactly as stored. Clipboard copying still uses the original source.
  const highlighted = useMemo(
    () => renderNodes(highlighter.highlight('yaml', source).children),
    [source],
  );
  return <code>{highlighted}</code>;
}
