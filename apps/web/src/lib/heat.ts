// Per-column min-max normalization for the provider matrix heat: a cell's tint says "stronger
// within this column", so each benchmark normalizes against its own measured spread. Pure so the
// mapping is testable without rendering the table.

export interface HeatRange {
  min: number;
  max: number;
}

export interface HeatColumn {
  id: string;
}

export interface HeatRow {
  scores: Record<string, number>;
}

/** The measured min and max of each column that at least one row has a score in. */
export function heatRange(rows: HeatRow[], columns: HeatColumn[]): Map<string, HeatRange> {
  const ranges = new Map<string, HeatRange>();
  for (const column of columns) {
    const values = rows
      .filter((row) => column.id in row.scores)
      .map((row) => row.scores[column.id]);
    if (values.length > 0) {
      ranges.set(column.id, { min: Math.min(...values), max: Math.max(...values) });
    }
  }
  return ranges;
}

/** Where a value sits in its column's spread, 0..1; 1 when the column has no spread. */
export function heatFraction(
  ranges: Map<string, HeatRange>,
  columnId: string,
  value: number,
): number {
  const range = ranges.get(columnId);
  if (!range || range.max === range.min) return 1;
  return (value - range.min) / (range.max - range.min);
}
