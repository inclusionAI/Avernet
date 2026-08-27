import { DIMENSIONS_INFO } from '@/components/BotWorkshop/BotHealthCheckDrawer/constants';
import type { BotHealthDimension } from '@/domain/botHealthCheck';
import { useMemo, useState } from 'react';

interface RadarChartProps {
  dimensions?: BotHealthDimension[];
}

export function RadarChart({ dimensions = [] }: RadarChartProps) {
  const [hovered, setHovered] = useState<number | null>(null);

  const size = 280;
  const centerX = size / 2;
  const centerY = size / 2 - 10;
  const maxRadius = 80;

  const values = useMemo(() => {
    return DIMENSIONS_INFO.map((dim) => {
      const matched = dimensions.find((item) => item.scanDim === dim.scanDim || item.key === dim.dimensionKey);
      if (!matched) return 0;
      if (matched.status === 'scanning') return 0;
      return matched.score ?? 0;
    });
  }, [dimensions]);

  const maxValue = 100;
  const gridLevels = [0.2, 0.4, 0.6, 0.8, 1];

  function getPoint(value: number, angle: number, radius: number) {
    return {
      x: centerX + Math.cos(angle) * radius,
      y: centerY + Math.sin(angle) * radius,
    };
  }

  const dataPoints = values.map((value, index) =>
    getPoint(value, DIMENSIONS_INFO[index].angle, (value / maxValue) * maxRadius),
  );

  const polygonPoints = dataPoints.map((point) => `${point.x},${point.y}`).join(' ');

  return (
    <div className="relative flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label="多维健康度雷达图">
        {gridLevels.map((level) => {
          const radius = level * maxRadius;
          const points = DIMENSIONS_INFO.map((dim) => {
            const { x, y } = getPoint(0, dim.angle, radius);
            return `${x},${y}`;
          }).join(' ');
          return (
            <polygon
              key={level}
              points={points}
              fill="none"
              stroke="var(--color-border)"
              strokeWidth={1}
              opacity={0.6}
            />
          );
        })}
        {DIMENSIONS_INFO.map((dim) => {
          const { x, y } = getPoint(0, dim.angle, maxRadius);
          return (
            <line key={dim.key} x1={centerX} y1={centerY} x2={x} y2={y} stroke="var(--color-border)" strokeWidth={1} />
          );
        })}
        {DIMENSIONS_INFO.map((dim) => {
          const { x, y } = getPoint(0, dim.angle, maxRadius + 24);
          return (
            <text
              key={`label-${dim.key}`}
              x={x}
              y={y}
              textAnchor="middle"
              dominantBaseline="middle"
              className="fill-[var(--color-muted)]"
              style={{ fontSize: 12 }}
            >
              {dim.name}
            </text>
          );
        })}
        <polygon
          points={polygonPoints}
          fill="var(--color-primary-soft)"
          stroke="var(--color-primary)"
          strokeWidth={2}
          fillOpacity={0.5}
        />
        {values.map((value, index) => {
          const point = dataPoints[index];
          const isHovered = hovered === index;
          return (
            <g key={`point-${index}`}>
              <circle
                cx={point.x}
                cy={point.y}
                r={isHovered ? 5 : 3}
                fill="var(--color-primary)"
                stroke="white"
                strokeWidth={2}
                onMouseEnter={() => setHovered(index)}
                onMouseLeave={() => setHovered(null)}
                style={{ cursor: 'pointer' }}
              />
              {isHovered && (
                <g>
                  <rect x={point.x + 8} y={point.y - 24} width={80} height={20} rx={4} fill="rgba(0,0,0,0.75)" />
                  <text
                    x={point.x + 48}
                    y={point.y - 10}
                    textAnchor="middle"
                    className="fill-white"
                    style={{ fontSize: 12 }}
                  >
                    {DIMENSIONS_INFO[index].name} {value}分
                  </text>
                </g>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
