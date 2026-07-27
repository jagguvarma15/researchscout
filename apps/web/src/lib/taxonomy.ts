// Display-only mirror of researchscout/taxonomy.py for rendering the sidebar's subject
// controls. The server stays authoritative for filtering: if this list drifts, a stale group
// yields an empty result, never wrong data.

export interface GroupOption {
  key: string;
  label: string;
  tech: boolean;
}

export const GROUPS: GroupOption[] = [
  { key: 'cs', label: 'Computer Science', tech: true },
  { key: 'stat', label: 'Statistics', tech: true },
  { key: 'eess', label: 'Electrical Engineering and Systems', tech: true },
  { key: 'math', label: 'Mathematics', tech: false },
  { key: 'physics', label: 'Physics', tech: false },
  { key: 'q-bio', label: 'Quantitative Biology', tech: false },
  { key: 'q-fin', label: 'Quantitative Finance', tech: false },
  { key: 'econ', label: 'Economics', tech: false },
];

export function groupLabel(key: string): string {
  return GROUPS.find((group) => group.key === key)?.label ?? key;
}
