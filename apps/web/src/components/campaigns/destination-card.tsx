"use client";

import { useState } from "react";
import type { CampaignDestinationReport } from "@/types";

const PLATFORM_ICONS: Record<string, string> = {
  instagram: "IG",
  tiktok: "TT",
  youtube: "YT",
  twitter: "X",
  facebook: "FB",
  bandcamp: "BC",
  email: "EM",
};

interface DestinationCardProps {
  campaignId: string;
}

export function DestinationCard({ campaignId }: DestinationCardProps) {
  const [report, setReport] = useState<CampaignDestinationReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const fetchReport = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/v1/campaigns/${campaignId}/destinations`
      );
      if (!res.ok) throw new Error("Failed to load destinations");
      const data = await res.json();
      setReport(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  const copyUrl = async (url: string) => {
    await navigator.clipboard.writeText(url);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // Auto-fetch on mount
  if (!report && !loading && !error) {
    fetchReport();
  }

  if (loading) {
    return (
      <div className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-6">
        <p className="text-sm text-[var(--text-secondary)]">
          Loading destinations...
        </p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-red-500/30 bg-[var(--bg-surface)] p-6">
        <p className="text-sm text-red-400">{error}</p>
        <button
          onClick={fetchReport}
          className="mt-2 text-xs text-[var(--brand-gold)] hover:underline"
        >
          Retry
        </button>
      </div>
    );
  }

  if (!report) return null;

  const allGood =
    report.postsMissingDestination.length === 0 && report.totalPosts > 0;
  const hasWarnings = report.postsMissingDestination.length > 0;
  const noPosts = report.totalPosts === 0;

  return (
    <div className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-6">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-[var(--text-primary)]">
          CTA Destination
        </h3>
        {allGood && (
          <span className="rounded-full bg-green-500/10 px-2 py-0.5 text-xs font-medium text-green-400">
            All posts linked
          </span>
        )}
        {hasWarnings && (
          <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-400">
            {report.postsMissingDestination.length} missing
          </span>
        )}
      </div>

      {report.canonicalUrl ? (
        <div className="mt-3 flex items-center gap-2">
          <code className="flex-1 truncate rounded-lg bg-[var(--bg-surface-hover)] px-3 py-2 text-xs text-[var(--text-primary)]">
            {report.canonicalUrl}
          </code>
          <button
            onClick={() => copyUrl(report.canonicalUrl!)}
            className="shrink-0 rounded-lg bg-[var(--brand-gold)] px-3 py-2 text-xs font-medium text-black transition-colors hover:bg-[var(--brand-gold)]/80"
          >
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
      ) : (
        <p className="mt-3 text-xs text-[var(--text-secondary)]">
          No destination URLs configured on the linked release.
        </p>
      )}

      {noPosts && (
        <p className="mt-3 text-xs text-[var(--text-secondary)]">
          No posts in this campaign yet.
        </p>
      )}

      {hasWarnings && (
        <div className="mt-4">
          <p className="mb-2 text-xs font-medium text-amber-400">
            Posts missing destination URL:
          </p>
          <div className="space-y-1">
            {report.postsMissingDestination.map((post) => (
              <div
                key={post.postId}
                className="flex items-center gap-2 rounded-lg bg-amber-500/5 px-3 py-1.5 text-xs"
              >
                <span className="font-mono font-medium text-[var(--text-primary)]">
                  {PLATFORM_ICONS[post.platform] || post.platform}
                </span>
                <span className="text-[var(--text-secondary)]">
                  {post.status}
                </span>
                {post.scheduledAt && (
                  <span className="ml-auto text-[var(--text-secondary)]">
                    {new Date(post.scheduledAt).toLocaleDateString()}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="mt-4 flex gap-4 text-xs text-[var(--text-secondary)]">
        <span>
          {report.postsWithDestination}/{report.totalPosts} posts linked
        </span>
        <button
          onClick={fetchReport}
          className="text-[var(--brand-gold)] hover:underline"
        >
          Refresh
        </button>
      </div>
    </div>
  );
}
