import type { APIRoute } from 'astro';

export const GET: APIRoute = ({ locals }) => {
  return Response.json({
    authenticated: locals.user !== null,
    username: locals.user?.username ?? null,
  });
};
