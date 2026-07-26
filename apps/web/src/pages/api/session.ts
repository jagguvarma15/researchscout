import type { APIRoute } from 'astro';

export const GET: APIRoute = ({ locals }) => {
  return Response.json({
    authenticated: true,
    username: locals.user.username,
  });
};
