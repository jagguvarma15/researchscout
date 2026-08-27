// Slash-command parsing for the chat composer. Unknown commands and commands without an
// argument never send; the composer keeps the hint line up instead.

export type ParsedInput =
  | { kind: 'question'; text: string }
  | { kind: 'web'; query: string }
  | { kind: 'ai'; question: string }
  | { kind: 'deep'; question: string }
  | { kind: 'unknown'; command: string };

export function parseInput(raw: string): ParsedInput {
  const trimmed = raw.trim();
  if (!trimmed.startsWith('/')) return { kind: 'question', text: trimmed };
  const space = trimmed.indexOf(' ');
  const command = (space === -1 ? trimmed : trimmed.slice(0, space)).toLowerCase();
  const rest = space === -1 ? '' : trimmed.slice(space + 1).trim();
  if (command === '/web') return { kind: 'web', query: rest };
  if (command === '/ai') return { kind: 'ai', question: rest };
  if (command === '/deep') return { kind: 'deep', question: rest };
  return { kind: 'unknown', command };
}

export function commandHint(raw: string): string | null {
  if (!raw.trimStart().startsWith('/')) return null;
  return (
    'Commands: /web <query> - quick web search, /ai <question> - ask the AI directly, ' +
    '/deep <question> - multi-step research (slow, several AI calls)'
  );
}
