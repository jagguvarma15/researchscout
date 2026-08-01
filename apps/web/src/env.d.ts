/// <reference types="astro/client" />

declare namespace App {
  interface Locals {
    // Null when sign-in is configured and nobody is signed in. With sign-in unconfigured this
    // is always the built-in local user, which is what a local install runs as.
    user: { sub: string; username: string } | null;
    // The API access token for this request, for the server-side proxy only. Never rendered.
    accessToken: string | null;
  }
}
