// The web taxonomy is a display-only mirror of researchscout/taxonomy.py. It cannot import the
// server's copy, so these tests pin the properties that would break the pages if the two drifted:
// that every subject key the sidebar can emit exists, that no subject renders as an empty
// control, and that a category checklist only ever offers codes the badge tooltips can name.
//
// A drifted key is not silently wrong - the API answers 422 naming it - but it is a broken
// filter, and finding that in a test is cheaper than finding it in the browser.

import { describe, expect, it } from 'vitest';

import {
  CATEGORY_NAMES,
  SUBJECTS,
  TOPICS,
  categoryName,
  subjectCategories,
  subjectLabel,
  topicLabel,
} from './taxonomy';

describe('subjects', () => {
  it('mirrors the server list, in order', () => {
    expect(SUBJECTS.map((subject) => subject.key)).toEqual([
      'ai',
      'stats',
      'data',
      'math',
      'bio',
      'physical',
      'security',
      'society',
      'systems',
    ]);
  });

  it('names the four core ones', () => {
    expect(SUBJECTS.filter((subject) => subject.core).map((s) => s.key)).toEqual([
      'ai',
      'stats',
      'data',
      'math',
    ]);
  });

  it('gives every subject something to select', () => {
    for (const subject of SUBJECTS) {
      expect(subject.archives.length + subject.categories.length).toBeGreaterThan(0);
    }
  });

  it('uses only category codes the tooltips can name', () => {
    for (const subject of SUBJECTS) {
      for (const code of subject.categories) {
        expect(categoryName(code), code).toBeDefined();
      }
    }
  });

  it('falls back to the key for a subject it does not know', () => {
    expect(subjectLabel('ai')).toBe('AI and machine learning');
    expect(subjectLabel('notreal')).toBe('notreal');
  });
});

describe('subjectCategories', () => {
  it('expands an archive subject to its codes', () => {
    const codes = subjectCategories('stats').map((entry) => entry.code);
    expect(codes).toContain('stat.ML');
    expect(codes).toContain('stat.ME');
    expect(codes.every((code) => code.startsWith('stat.'))).toBe(true);
  });

  it('combines archives and individual codes', () => {
    const codes = subjectCategories('math').map((entry) => entry.code);
    expect(codes).toContain('math.OC'); // from the archive
    expect(codes).toContain('cs.NA'); // named individually
  });

  it('returns a code list subject unchanged', () => {
    expect(subjectCategories('security')).toEqual([
      { code: 'cs.CR', name: 'Cryptography and Security' },
    ]);
  });

  it('leaves physical sciences as the subject alone', () => {
    // Over a hundred codes; a checklist that long is a wall rather than a control.
    expect(subjectCategories('physical')).toEqual([]);
  });

  it('is empty for a subject it does not know', () => {
    expect(subjectCategories('notreal')).toEqual([]);
  });

  it('names every code it offers', () => {
    for (const subject of SUBJECTS) {
      for (const entry of subjectCategories(subject.key)) {
        expect(CATEGORY_NAMES[entry.code], entry.code).toBeDefined();
        expect(entry.name).toBe(CATEGORY_NAMES[entry.code]);
      }
    }
  });

  it('sorts by code, the way arXiv writes them', () => {
    const codes = subjectCategories('systems').map((entry) => entry.code);
    expect(codes).toEqual([...codes].sort());
  });
});

describe('topics', () => {
  it('is the three techniques the toolbar offers', () => {
    expect(TOPICS.map((topic) => topic.key)).toEqual(['nlp', 'cv', 'rl']);
  });

  it('has a short form for each, since three sit side by side', () => {
    expect(TOPICS.map((topic) => topic.short)).toEqual(['NLP', 'CV', 'RL']);
    for (const topic of TOPICS) {
      expect(topic.short.length).toBeLessThanOrEqual(3);
      expect(topic.label.length).toBeGreaterThan(topic.short.length);
    }
  });

  it('falls back to the key for a topic it does not know', () => {
    expect(topicLabel('rl')).toBe('RL');
    expect(topicLabel('notreal')).toBe('notreal');
  });
});
