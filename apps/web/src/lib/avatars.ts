// The avatar set: a pirate crew of owls, drawn in AvatarArt.svelte. The server stores only
// a slug and checks its shape, so this list is the single place that decides what exists -
// a stored slug that is not here renders as initials, which is how an old account survives
// the set changing.

export const AVATARS = [
  { slug: 'captain', label: 'Captain' },
  { slug: 'swordsman', label: 'Swordsman' },
  { slug: 'navigator', label: 'Navigator' },
  { slug: 'cook', label: 'Cook' },
  { slug: 'sniper', label: 'Sniper' },
  { slug: 'doctor', label: 'Doctor' },
  { slug: 'shipwright', label: 'Shipwright' },
  { slug: 'musician', label: 'Musician' },
  { slug: 'archaeologist', label: 'Archaeologist' },
  { slug: 'helmsman', label: 'Helmsman' },
] as const;

export type AvatarSlug = (typeof AVATARS)[number]['slug'];

export function isAvatarSlug(value: string | null | undefined): value is AvatarSlug {
  return AVATARS.some((choice) => choice.slug === value);
}
