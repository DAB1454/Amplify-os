"use client";

import { useState } from "react";

interface MediaPreviewProps {
  urls: string[];
  /** Compact mode for inline use in lists */
  compact?: boolean;
}

function getMediaType(url: string): "video" | "image" | "audio" | "unknown" {
  // Strip query params before checking extension
  const clean = url.split("?")[0].toLowerCase();
  // Check file extensions first (most reliable)
  if (/\.(mp4|mov|webm|avi|mkv)$/.test(clean)) return "video";
  if (/\.(jpg|jpeg|png|gif|webp|svg|bmp)$/.test(clean)) return "image";
  if (/\.(mp3|wav|ogg|aac|flac|m4a)$/.test(clean)) return "audio";
  // S3 path segments (only match /type/ directory patterns, not substrings)
  if (/\/video\//.test(clean)) return "video";
  if (/\/audio\//.test(clean)) return "audio";
  if (/\/image\//.test(clean)) return "image";
  // Default to image for S3 media URLs (most common case)
  if (clean.includes(".s3.") || clean.includes("amazonaws.com")) return "image";
  return "unknown";
}

function getFilename(url: string): string {
  try {
    const path = new URL(url).pathname;
    const parts = path.split("/");
    return parts[parts.length - 1] || "file";
  } catch {
    return url.split("/").pop() || "file";
  }
}

async function downloadFile(url: string, filename: string) {
  try {
    const response = await fetch(url);
    const blob = await response.blob();
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(blobUrl);
  } catch {
    // Fallback: open in new tab
    window.open(url, "_blank");
  }
}

function MediaItem({ url, compact }: { url: string; compact?: boolean }) {
  const type = getMediaType(url);
  const filename = getFilename(url);
  const [downloading, setDownloading] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const handleDownload = async () => {
    setDownloading(true);
    await downloadFile(url, filename);
    setDownloading(false);
  };

  const previewSize = compact ? "max-h-32" : "max-h-64";

  return (
    <div className="rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] overflow-hidden">
      {/* Preview */}
      {type === "image" && (
        <div
          className={`relative cursor-pointer ${expanded ? "" : previewSize} overflow-hidden`}
          onClick={() => setExpanded(!expanded)}
        >
          <img
            src={url}
            alt={filename}
            className="w-full object-contain"
            loading="lazy"
          />
        </div>
      )}
      {type === "video" && (
        <div className={expanded ? "" : previewSize}>
          <video
            src={url}
            controls
            preload="metadata"
            crossOrigin="anonymous"
            className="w-full"
            style={expanded ? {} : { maxHeight: compact ? "128px" : "256px" }}
          />
        </div>
      )}
      {type === "audio" && (
        <div className="p-4">
          <div className="flex items-center gap-3 mb-3">
            <div className="w-10 h-10 rounded-full bg-indigo-100 flex items-center justify-center">
              <svg className="w-5 h-5 text-indigo-600" fill="currentColor" viewBox="0 0 24 24">
                <path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/>
              </svg>
            </div>
            <span className="text-sm font-medium text-[var(--text-primary)] truncate">{filename}</span>
          </div>
          <audio src={url} controls preload="metadata" crossOrigin="anonymous" className="w-full" style={{ height: "40px" }} />
        </div>
      )}
      {type === "unknown" && (
        <div className="p-3 flex items-center gap-2 text-sm text-[var(--text-secondary)]">
          <span className="text-lg">{"📎"}</span>
          <span className="truncate">{filename}</span>
        </div>
      )}

      {/* Action bar */}
      <div className="flex items-center justify-between px-3 py-2 border-t border-[var(--border-color)] bg-[var(--bg-surface)]">
        <span className="text-[10px] text-[var(--text-secondary)] truncate max-w-[60%]" title={filename}>
          {filename}
        </span>
        <div className="flex gap-2">
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded px-2 py-1 text-[10px] font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-primary)] transition-colors"
            title="Open in new tab"
          >
            Open
          </a>
          <button
            onClick={handleDownload}
            disabled={downloading}
            className="rounded px-2 py-1 text-[10px] font-medium text-indigo-600 hover:bg-indigo-50 disabled:opacity-50 transition-colors"
            title="Download file"
          >
            {downloading ? "Downloading..." : "Download"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function MediaPreview({ urls, compact = false }: MediaPreviewProps) {
  if (!urls || urls.length === 0) return null;

  // Separate visual items from audio for better layout
  const audioUrls = urls.filter((u) => getMediaType(u) === "audio");
  const visualUrls = urls.filter((u) => getMediaType(u) !== "audio");

  return (
    <div className="mt-2 space-y-2">
      {/* Visual items (images/videos) in grid */}
      {visualUrls.length > 0 && (
        <div className={visualUrls.length > 1 ? "grid grid-cols-2 gap-2" : ""}>
          {visualUrls.map((url, i) => (
            <MediaItem key={`v-${i}`} url={url} compact={compact} />
          ))}
        </div>
      )}
      {/* Audio items below visuals */}
      {audioUrls.map((url, i) => (
        <MediaItem key={`a-${i}`} url={url} compact={compact} />
      ))}
    </div>
  );
}

/** Standalone "Download All" button for multiple media files */
export function DownloadAllButton({ urls }: { urls: string[] }) {
  const [downloading, setDownloading] = useState(false);

  if (!urls || urls.length <= 1) return null;

  const handleDownloadAll = async () => {
    setDownloading(true);
    for (const url of urls) {
      await downloadFile(url, getFilename(url));
      // Small delay to avoid browser blocking multiple downloads
      await new Promise((r) => setTimeout(r, 300));
    }
    setDownloading(false);
  };

  return (
    <button
      onClick={handleDownloadAll}
      disabled={downloading}
      className="rounded-lg bg-indigo-600/10 px-3 py-1.5 text-xs font-medium text-indigo-600 hover:bg-indigo-600/20 disabled:opacity-50 transition-colors"
    >
      {downloading ? "Downloading..." : `Download All (${urls.length} files)`}
    </button>
  );
}
