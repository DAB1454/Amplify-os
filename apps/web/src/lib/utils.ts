import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Parse a UTC datetime string from the API into a local Date.
 * API returns ISO strings without timezone suffix — treat as UTC.
 */
export function utcToLocal(dateStr: string): Date {
  if (!dateStr) return new Date();
  // If no timezone info, append Z to mark as UTC.
  // API returns formats like "2026-04-02T19:30:00" or "2026-04-02T19:30:00.123456"
  // Only skip Z if string already has Z, +HH:MM, or -HH:MM timezone suffix.
  if (!dateStr.endsWith("Z") && !/[+-]\d{2}:\d{2}$/.test(dateStr)) {
    return new Date(dateStr + "Z");
  }
  return new Date(dateStr);
}

/**
 * Format a UTC datetime string from the API as a local string.
 */
export function formatLocal(dateStr: string): string {
  return utcToLocal(dateStr).toLocaleString();
}

export function formatLocalDate(dateStr: string): string {
  return utcToLocal(dateStr).toLocaleDateString();
}
