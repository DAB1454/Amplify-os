/**
 * Centralized platform constants — emojis, colors, labels, icons.
 * Import this instead of duplicating platform maps across pages.
 */

export const PLATFORMS = ["instagram", "youtube", "tiktok", "twitter"] as const;
export type Platform = (typeof PLATFORMS)[number];

export const platformLabels: Record<Platform, string> = {
  instagram: "Instagram",
  youtube: "YouTube",
  tiktok: "TikTok",
  twitter: "Twitter / X",
};

export const platformEmojis: Record<Platform, string> = {
  instagram: "\ud83d\udcf8",
  youtube: "\u25b6\ufe0f",
  tiktok: "\ud83c\udfb5",
  twitter: "\ud83d\udc26",
};

export const platformColors: Record<Platform, string> = {
  instagram: "#E4405F",
  youtube: "#FF0000",
  tiktok: "#000000",
  twitter: "#1DA1F2",
};

export const platformBgColors: Record<Platform, string> = {
  instagram: "bg-pink-100",
  youtube: "bg-red-100",
  tiktok: "bg-gray-100",
  twitter: "bg-blue-100",
};

export const platformTextColors: Record<Platform, string> = {
  instagram: "text-pink-600",
  youtube: "text-red-600",
  tiktok: "text-gray-900",
  twitter: "text-blue-500",
};

export const platformBadgeColors: Record<Platform, string> = {
  instagram: "bg-pink-100 text-pink-700",
  youtube: "bg-red-100 text-red-700",
  tiktok: "bg-gray-100 text-gray-700",
  twitter: "bg-blue-100 text-blue-700",
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
