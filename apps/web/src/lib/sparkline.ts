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
  return { line, area, end: last };
}

function round(value: number): number {
  return Math.round(value * 10) / 10;
}
