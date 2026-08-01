import type { APIRoute } from 'astro';

// Who the browser is, for client components. Deliberately thin: no token, no email.
export const GET: APIRoute = ({ locals }) => {
  return Response.json({
    authenticated: locals.user !== null,
    username: locals.user?.username ?? null,
  });
};
