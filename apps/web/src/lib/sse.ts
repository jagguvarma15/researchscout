// SSE stream plumbing for the chat drawer, kept pure so tests can cover the frame
// handling without a DOM.

export interface SseEvent {
  event: string;
  payload: unknown;
}

// Split a decoder buffer on frame boundaries, returning the complete frames and the
// unterminated remainder to carry into the next read.
export function splitSseBuffer(buffer: string): { frames: string[]; rest: string } {
  const parts = buffer.split('\n\n');
  const rest = parts.pop() ?? '';
  return { frames: parts.filter((frame) => frame.trim().length > 0), rest };
}

// Parse one frame's event/data lines. Returns null for frames with no data line or
// broken JSON so the caller can skip them instead of tearing down the stream.
export function parseSseFrame(frame: string): SseEvent | null {
  let event = 'message';
  let data = '';
  for (const line of frame.split('\n')) {
    if (line.startsWith('event: ')) event = line.slice(7);
    else if (line.startsWith('data: ')) data = line.slice(6);
  }
  if (!data) return null;
  try {
    return { event, payload: JSON.parse(data) };
  } catch {
    return null;
  }
}
