// Shared facts for the Terms and Privacy pages and the acceptance dialog.
//
// The server is authoritative about which version an account must accept: the dialog posts
// back whatever `GET /v1/me` reports as `terms_required` (RS_TERMS_VERSION). The constant here
// is for display, and must be kept in step with that setting when the documents change.

export const TERMS_VERSION = '2026-08-01';
export const TERMS_EFFECTIVE = '1 August 2026';
export const CONTACT_URL = 'https://github.com/jagguvarma15/researchscout/issues';
export const MINIMUM_AGE = 16;

// The short version shown in the acceptance dialog, so nobody has to read the full page to
// know what they are agreeing to. Each line is also covered by the full documents.
export const TERMS_SUMMARY = [
  'Papers, abstracts and metadata come from arXiv and other sources; they belong to their authors, not to this site.',
  'Scout writes machine-generated summaries and answers, which can be wrong. Check the paper.',
  'The site is run by one person on personal hardware, with no uptime promise.',
  'Your account holds your email, your saved papers, your interests and what you read here. You can export or delete all of it at any time.',
];
