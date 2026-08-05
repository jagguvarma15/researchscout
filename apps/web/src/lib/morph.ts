// view-transition-name values must be CSS custom identifiers, and canonical paper ids are
// not ("arxiv:2401.12345" carries ':' and '.'). One place turns an id into a stable name so
// the feed, the saved list and the paper page all derive the same one - which is the whole
// trick behind a title morphing from a list into a heading during navigation.

export function morphName(prefix: string, id: string): string {
  return `${prefix}-${id.replace(/[^a-zA-Z0-9_-]/g, '-')}`;
}
