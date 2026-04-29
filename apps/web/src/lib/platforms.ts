/**
 * Centralized platform constants — emojis, colors, labels, icons.
 * Import this instead of duplicating platform maps across pages.
 */

export const PLATFORMS = [
  "instagram", "youtube", "tiktok", "twitter",
  "bandcamp", "linktree", "email",
] as const;
export type Platform = (typeof PLATFORMS)[number];

export const platformLabels: Record<Platform, string> = {
  instagram: "Instagram",
  youtube: "YouTube",
  tiktok: "TikTok",
  twitter: "Twitter / X",
  bandcamp: "Bandcamp",
  linktree: "Linktree",
  email: "Email",
};

export const platformEmojis: Record<Platform, string> = {
  instagram: "\ud83d\udcf8",
  youtube: "\u25b6\ufe0f",
  tiktok: "\ud83c\udfb5",
  twitter: "\ud835\udd4f",
  bandcamp: "\ud83c\udfb6",
  linktree: "\ud83d\udd17",
  email: "\ud83d\udce7",
};

export const platformColors: Record<Platform, string> = {
  instagram: "#E1306C",
  youtube: "#FF0000",
  tiktok: "#00f2ea",
  twitter: "#000000",
  bandcamp: "#1DA0C3",
  linktree: "#43E660",
  email: "#c9a84c",
};

export const platformBgColors: Record<Platform, string> = {
  instagram: "bg-pink-100",
  youtube: "bg-red-100",
  tiktok: "bg-gray-100",
  twitter: "bg-blue-100",
  bandcamp: "bg-cyan-100",
  linktree: "bg-green-100",
  email: "bg-amber-100",
};

export const platformTextColors: Record<Platform, string> = {
  instagram: "text-pink-600",
  youtube: "text-red-600",
  tiktok: "text-gray-900",
  twitter: "text-blue-500",
  bandcamp: "text-cyan-600",
  linktree: "text-green-600",
  email: "text-amber-600",
};

export const platformBadgeColors: Record<Platform, string> = {
  instagram: "bg-pink-100 text-pink-700",
  youtube: "bg-red-100 text-red-700",
  tiktok: "bg-gray-100 text-gray-700",
  twitter: "bg-blue-100 text-blue-700",
  bandcamp: "bg-cyan-100 text-cyan-700",
  linktree: "bg-green-100 text-green-700",
  email: "bg-amber-100 text-amber-700",
};

/**
 * Get a display-friendly label with emoji for a platform.
 */
export function platformLabel(platform: string): string {
  const p = platform.toLowerCase() as Platform;
  const emoji = platformEmojis[p] || "\ud83c\udf10";
  const label = platformLabels[p] || platform;
  return `${emoji} ${label}`;
}

/**
 * Check if a string is a known platform.
 */
export function isPlatform(value: string): value is Platform {
  return PLATFORMS.includes(value.toLowerCase() as Platform);
}
