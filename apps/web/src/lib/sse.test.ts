import { describe, expect, it } from 'vitest';

import { parseSseFrame, splitSseBuffer } from './sse';

describe('splitSseBuffer', () => {
  it('returns complete frames and carries the remainder', () => {
    const { frames, rest } = splitSseBuffer(
      'event: meta\ndata: {"retrieved":3}\n\nevent: token\ndata: {"del',
    );
    expect(frames).toEqual(['event: meta\ndata: {"retrieved":3}']);
    expect(rest).toBe('event: token\ndata: {"del');
  });

  it('handles multiple frames in one read', () => {
    const { frames, rest } = splitSseBuffer('event: a\ndata: {}\n\nevent: b\ndata: {}\n\n');
    expect(frames).toHaveLength(2);
    expect(rest).toBe('');
  });

  it('drops whitespace-only fragments between separators', () => {
    const { frames } = splitSseBuffer('\n\nevent: a\ndata: {}\n\n');
    expect(frames).toEqual(['event: a\ndata: {}']);
  });

  it('buffers an incomplete frame entirely', () => {
    const { frames, rest } = splitSseBuffer('event: meta\ndata: {"retr');
    expect(frames).toEqual([]);
    expect(rest).toBe('event: meta\ndata: {"retr');
  });
});

describe('parseSseFrame', () => {
  it('parses event name and JSON payload', () => {
    expect(parseSseFrame('event: meta\ndata: {"retrieved":3,"mode":"fast"}')).toEqual({
      event: 'meta',
      payload: { retrieved: 3, mode: 'fast' },
    });
  });

  it('defaults the event name when only data is present', () => {
    expect(parseSseFrame('data: {"a":1}')).toEqual({ event: 'message', payload: { a: 1 } });
  });

  it('returns null without a data line', () => {
    expect(parseSseFrame('event: ping')).toBeNull();
  });

  it('returns null on broken JSON instead of throwing', () => {
    expect(parseSseFrame('event: token\ndata: {"delta": broke')).toBeNull();
  });
});
