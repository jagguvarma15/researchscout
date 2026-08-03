// Where this deployment thinks it lives.
//
// The sign-in flow sends `<site>/callback` to the identity provider as the redirect_uri, and
// the provider rejects anything that is not on its allow list. Getting this wrong produces
// "Callback URL mismatch" on the provider's own error page, which says nothing about which
// setting is at fault - so the default matters.
//
// SITE_URL wins when set. Otherwise, on Vercel, VERCEL_PROJECT_PRODUCTION_URL is injected into
// every build and points at the stable production alias, which is the origin people actually
// visit; falling back to it means a deployment is correct without anyone configuring anything.
// Everything else is local.
//
// Preview deployments keep their own hostnames and are deliberately not used here: each one
// would need its own entry in the provider's allow list, so they point at production instead.
//
// Plain JavaScript, not TypeScript: the proxy route and the auth module both import it.

const vercelProduction = process.env.VERCEL_PROJECT_PRODUCTION_URL;

/**
 * A bare hostname is what a dashboard shows you and therefore what gets pasted; without a
 * scheme `new URL('/callback', site)` throws and the sign-in route answers 500 instead of
 * redirecting. Assume https, which is the only thing a deployed site should be.
 */
function withScheme(value) {
  return /^https?:\/\//.test(value) ? value : `https://${value}`;
}

const configured = process.env.SITE_URL?.trim();

export const SITE_URL = configured
  ? withScheme(configured).replace(/\/+$/, '')
  : vercelProduction
    ? `https://${vercelProduction}`
    : 'http://localhost:4321';
