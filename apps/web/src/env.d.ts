/// <reference types="astro/client" />

declare namespace App {
  interface Locals {
    user: { sub: string; username: string } | null;
  }
}
