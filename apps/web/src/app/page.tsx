"use client";

import { useState, useEffect, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { OnboardingBanner } from "@/components/onboarding/onboarding-banner";
import { apiGet } from "@/lib/api";

interface Overview {
  total_campaigns: number;
  total_posts: number;
  published_posts: number;
}

interface ApprovalCount {
  pending_count: number;
}

interface Campaign {
  id: string;
  name: string;
  status: string;
  phase: string;
  start_date: string | null;
  end_date: string | null;
  created_at: string;
}

interface Post {
  id: string;
  platform: string;
  status: string;
  content_text: string;
  created_at: string;
  published_at: string | null;
  scheduled_at: string | null;
}

interface BlendedRecommendation {
  feature: string;
  recommended_value: string;
  title: string;
  explanation: string;
  confidence: number;
  source: "global_prior" | "cohort_prior" | "tenant_learned" | "blended";
  category: string;
  tenant_weight: number;
  global_weight: number;
  global_tenant_count: number | null;
}

interface DataMaturity {
  is_cold_start: boolean;
  maturity_label: string;
  tenant_weight: number;
  global_weight: number;
  feature_vectors: number;
  has_enough_data: boolean;
}

interface BlendedResponse {
  recommendations: BlendedRecommendation[];
  data_status: DataMaturity;
}

function SourceBadge({ source }: { source: string }) {
  const config: Record<string, { label: string; className: string }> = {
    global_prior: {
      label: "Global Default",
      className: "bg-blue-500/20 text-blue-400",
    },
    cohort_prior: {
      label: "Cohort Insight",
      className: "bg-purple-500/20 text-purple-400",
    },
    tenant_learned: {
      label: "Your Data",
      className: "bg-green-500/20 text-green-400",
    },
    blended: {
      label: "Blended",
      className: "bg-[var(--brand-gold)]/20 text-[var(--brand-gold)]",
    },
  };
  const c = config[source] || config.global_prior;
  return (
    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${c.className}`}>
      {c.label}
    </span>
  );
}

export default function DashboardPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [pendingCount, setPendingCount] = useState(0);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [recentPosts, setRecentPosts] = useState<Post[]>([]);
  const [insights, setInsights] = useState<BlendedResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchDashboard = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [ov, approvals, camps, posts, blended] = await Promise.allSettled([
        apiGet<Overview>("/api/v1/analytics/overview"),
        apiGet<ApprovalCount>("/api/v1/approvals/count"),
        apiGet<Campaign[]>("/api/v1/campaigns/?status=active"),
        apiGet<Post[]>("/api/v1/posts/"),
        apiGet<BlendedResponse>("/api/v1/learning/tenant/me/blended-recommendations"),
      ]);
      const failures = [ov, approvals, camps, posts, blended].filter((r) => r.status === "rejected");
      if (failures.length === 5) {
        throw new Error((failures[0] as PromiseRejectedResult).reason?.message || "Failed to load dashboard");
      }
      setOverview(ov.status === "fulfilled" ? ov.value : null);
      setPendingCount(approvals.status === "fulfilled" ? approvals.value.pending_count : 0);
      setCampaigns(camps.status === "fulfilled" ? camps.value : []);
      // Show most recent 5 posts
      setRecentPosts(posts.status === "fulfilled" ? posts.value.slice(0, 5) : []);
      setInsights(blended.status === "fulfilled" ? blended.value : null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load dashboard");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDashboard();
  }, [fetchDashboard]);

  const activeCampaigns = campaigns.length;
  const scheduledPosts = recentPosts.filter((p) => p.status === "scheduled").length;

  const stats = [
    { label: "Active Campaigns", value: overview ? activeCampaigns : null },
    { label: "Scheduled Posts", value: overview ? scheduledPosts : null },
    { label: "Pending Approvals", value: pendingCount },
    { label: "Published Posts", value: overview?.published_posts ?? null },
  ];

  return (
    <>
      <Header title="Dashboard" />

      <OnboardingBanner />

      {error && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      <div className="mt-8 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
        {stats.map((stat) => (
          <div
            key={stat.label}
            className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-6"
          >
            <p className="text-sm text-[var(--text-secondary)]">{stat.label}</p>
            <p className="mt-2 text-3xl font-semibold text-[var(--brand-gold)]">
              {loading ? (
                <span className="inline-block h-9 w-12 animate-pulse rounded bg-[var(--bg-primary)]" />
              ) : (
                stat.value ?? "\u2014"
              )}
            </p>
          </div>
        ))}
      </div>

      {/* Insights / Recommendations */}
      {insights && insights.recommendations.length > 0 && (
        <section className="mt-12">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-[var(--text-primary)]">Insights</h2>
            {insights.data_status.is_cold_start && (
              <span className="rounded-full bg-[var(--brand-gold)]/20 px-3 py-1 text-xs font-medium text-[var(--brand-gold)]">
                Smart Defaults
              </span>
            )}
          </div>
          {insights.data_status.is_cold_start && (
            <p className="mt-1 text-xs text-[var(--text-secondary)]">
              Based on aggregate data from similar accounts. These will be replaced by your own insights as you publish more.
            </p>
          )}
          <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {insights.recommendations.slice(0, 6).map((rec, i) => (
              <div
                key={`${rec.feature}-${rec.recommended_value}-${i}`}
                className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-4"
              >
                <div className="flex items-start justify-between">
                  <p className="text-sm font-medium text-[var(--text-primary)]">{rec.title}</p>
                  <SourceBadge source={rec.source} />
                </div>
                <p className="mt-1 text-xs text-[var(--text-secondary)] line-clamp-2">
                  {rec.explanation}
                </p>
                <div className="mt-2 flex items-center gap-2">
                  <div className="h-1.5 flex-1 rounded-full bg-[var(--bg-primary)]">
                    <div
                      className="h-1.5 rounded-full bg-[var(--brand-gold)]"
                      style={{ width: `${Math.round(rec.confidence * 100)}%` }}
                    />
                  </div>
                  <span className="text-[10px] text-[var(--text-secondary)]">
                    {Math.round(rec.confidence * 100)}%
                  </span>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Active campaigns */}
      <section className="mt-12">
        <h2 className="text-lg font-semibold text-[var(--text-primary)]">Active Campaigns</h2>
        {loading ? (
          <div className="mt-4 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
            Loading...
          </div>
        ) : campaigns.length === 0 ? (
          <div className="mt-4 rounded-xl border border-dashed border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
            No active campaigns. Create one from the Campaigns page.
          </div>
        ) : (
          <div className="mt-4 space-y-2">
            {campaigns.slice(0, 5).map((c) => (
              <div
                key={c.id}
                className="flex items-center justify-between rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] px-5 py-3"
              >
                <div>
                  <p className="text-sm font-medium text-[var(--text-primary)]">{c.name}</p>
                  <p className="text-xs text-[var(--text-secondary)]">
                    {c.phase} &middot; {c.start_date || "No start date"}
                  </p>
                </div>
                <span className="rounded-full bg-green-500/20 px-2 py-0.5 text-xs font-medium text-green-400">
                  {c.status}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Recent activity */}
      <section className="mt-12">
        <h2 className="text-lg font-semibold text-[var(--text-primary)]">Recent Posts</h2>
        {loading ? (
          <div className="mt-4 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
            Loading...
          </div>
        ) : recentPosts.length === 0 ? (
          <div className="mt-4 rounded-xl border border-dashed border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
            No posts yet.
          </div>
        ) : (
          <div className="mt-4 space-y-2">
            {recentPosts.map((p) => (
              <div
                key={p.id}
                className="flex items-center justify-between rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] px-5 py-3"
              >
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-[var(--text-primary)] truncate">
                    {p.content_text || "(no content)"}
                  </p>
                  <p className="text-xs text-[var(--text-secondary)]">
                    {p.platform} &middot; {new Date(p.created_at).toLocaleDateString()}
                  </p>
                </div>
                <span
                  className={`ml-3 shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${
                    p.status === "published"
                      ? "bg-green-500/20 text-green-400"
                      : p.status === "failed"
                        ? "bg-red-500/20 text-red-400"
                        : p.status === "scheduled"
                          ? "bg-blue-500/20 text-blue-400"
                          : "bg-gray-500/20 text-gray-400"
                  }`}
                >
                  {p.status}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>
    </>
  );
}
