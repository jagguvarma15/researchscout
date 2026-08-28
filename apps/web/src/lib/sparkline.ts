// Geometry for the inline SVG sparkline: map a size series onto a polyline, an area wash,
// and the terminal dot, inside a given viewBox. Pure math so both topics pages render the
// same marks at different scales, and so the mapping is testable without a browser.

export interface SparklineGeometry {
  /* points attribute for the <polyline>. */
  line: string;
  /* d attribute for the closed <path> area wash under the line. */
  area: string;
  /* Center of the terminal dot - the series' latest reading. */
  end: { x: number; y: number };
  /* Every reading's center, in series order - what makes individual builds clickable. */
  points: { x: number; y: number }[];
}

/** Null when the series has fewer than two points - a single build has no shape. */
export function sparklineGeometry(
  sizes: number[],
  width = 100,
  height = 28,
  pad = 4,
): SparklineGeometry | null {
  if (sizes.length < 2) return null;
  const min = Math.min(...sizes);
  const max = Math.max(...sizes);
  const span = max - min;
  const innerW = width - pad * 2;
  const innerH = height - pad * 2;
  const baseline = height - 2;
  const points = sizes.map((size, index) => {
    const x = pad + (innerW * index) / (sizes.length - 1);
    // A flat series draws the midline rather than dividing by zero.
    const y = span === 0 ? height / 2 : pad + innerH * (1 - (size - min) / span);
    return { x: round(x), y: round(y) };
  });
  const line = points.map((p) => `${p.x},${p.y}`).join(' ');
  const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x} ${p.y}`).join(' ');
  const last = points[points.length - 1];
  const area = `${path} L${last.x} ${baseline} L${points[0].x} ${baseline} Z`;
  return { line, area, end: last, points };
}

export interface StepLineGeometry {
  /* d attribute for the step <path> - a frontier holds its level until the next advance. */
  path: string;
  /* d attribute for the closed area wash under the steps. */
  area: string;
  end: { x: number; y: number };
  points: { x: number; y: number }[];
}

/**
 * A step line for frontier series: each advance holds its level until the next one.
 * Null below two points, same as the sparkline - one reading has no shape.
 */
export function stepLineGeometry(
  values: number[],
  width = 640,
  height = 120,
  pad = 8,
): StepLineGeometry | null {
  const base = sparklineGeometry(values, width, height, pad);
  if (base === null) return null;
  const parts: string[] = [];
  for (const [index, point] of base.points.entries()) {
    if (index === 0) {
      parts.push(`M${point.x} ${point.y}`);
    } else {
      // Horizontal to the new x at the old level, then vertical to the new level.
      parts.push(`L${point.x} ${base.points[index - 1].y}`, `L${point.x} ${point.y}`);
    }
  }
  const baseline = height - 2;
  const last = base.points[base.points.length - 1];
  const area = `${parts.join(' ')} L${last.x} ${baseline} L${base.points[0].x} ${baseline} Z`;
  return { path: parts.join(' '), area, end: base.end, points: base.points };
}

function round(value: number): number {
  return Math.round(value * 10) / 10;
}
