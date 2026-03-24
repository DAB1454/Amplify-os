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
  // If no timezone info, append Z to mark as UTC
  if (!dateStr.endsWith("Z") && !dateStr.includes("+") && !/\d{2}:\d{2}$/.test(dateStr.slice(-6))) {
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
