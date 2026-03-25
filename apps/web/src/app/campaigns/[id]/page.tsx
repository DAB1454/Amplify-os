"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { Header } from "@/components/layout/header";
import { apiGet, apiPost, apiPut } from "@/lib/api";
import { cn } from "@/lib/utils";
import { LoadingOverlay, ButtonSpinner } from "@/components/ui/spinner";

// Interfaces matching the API response (snake_case from Python)
interface CampaignDetail {
  id: string;
  name: string;
  status: string;
  mode: string;
  phase: string;
  artist_id: string;
  release_id: string | null;
  start_date: string | null;
  end_date: string | null;
  budget: number | null;
  created_at: string;
}

interface PlanPost {
  id: string;
  platform: string;
  status: string;
  content_text: string;
  approval_status: string | null;
  day_number: number | null;
  action_type_label: string | null;
  scheduled_at: string | null;
  destination_url: string | null;
  channel_id: string;
  media_urls: string[];
  created_at: string;
}

interface CalendarItem {
  id: string;
  title: string;
  description: string;
  item_type: string;
  scheduled_date: string;
  scheduled_time: string | null;
  is_completed: boolean;
}

interface PlanDay {
  day: string;
  day_number: number;
  calendar_items: CalendarItem[];
  posts: PlanPost[];
}

interface CampaignPlan {
  campaign: CampaignDetail;
  days: PlanDay[];
  stats: {
    total_posts: number;
    pending_review: number;
    approved: number;
    rejected: number;
    published: number;
  };
}

const approvalColors: Record<string, string> = {
  pending_review: "bg-amber-100 text-amber-700",
  approved: "bg-green-100 text-green-700",
  rejected: "bg-red-100 text-red-700",
};

const platformIcons: Record<string, string> = {
  instagram: "\u{1F4F8}",
  tiktok: "\u{1F3B5}",
  youtube: "\u25B6\uFE0F",
  twitter: "\u{1D54F}",
  facebook: "\u{1F4D8}",
};

const modeLabels: Record<string, { label: string; color: string }> = {
  manual: { label: "Manual", color: "bg-gray-100 text-gray-600" },
  ai_plan: { label: "AI Plan", color: "bg-indigo-100 text-indigo-600" },
  autopilot: { label: "Autopilot", color: "bg-purple-100 text-purple-600" },
};

export default function CampaignDetailPage() {
  const params = useParams();
  const router = useRouter();
  const campaignId = params.id as string;

  const [plan, setPlan] = useState<CampaignPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [approvingAll, setApprovingAll] = useState(false);
  const [actioningPost, setActioningPost] = useState<string | null>(null);
  const [editingPost, setEditingPost] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [generatingPlan, setGeneratingPlan] = useState(false);

  const fetchPlan = useCallback(async () => {
    try {
      const data = await apiGet<CampaignPlan>(`/api/v1/campaigns/${campaignId}/plan`);
      setPlan(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load plan");
    } finally {
      setLoading(false);
    }
  }, [campaignId]);

  useEffect(() => {
    fetchPlan();
  }, [fetchPlan]);

  const handleApproveAll = async () => {
    setApprovingAll(true);
    try {
      await apiPost(`/api/v1/campaigns/${campaignId}/approve-all`, {});
      await fetchPlan();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Approve all failed");
    } finally {
      setApprovingAll(false);
    }
  };

  const handlePostAction = async (postId: string, action: "review-approve" | "review-reject") => {
    setActioningPost(postId);
    try {
      await apiPost(`/api/v1/posts/${postId}/${action}`, {});
      await fetchPlan();
    } catch (err) {
      setError(err instanceof Error ? err.message : `${action} failed`);
    } finally {
      setActioningPost(null);
    }
  };

  const handleSaveEdit = async (postId: string) => {
    setActioningPost(postId);
    try {
      await apiPut(`/api/v1/posts/${postId}`, { content_text: editText });
      setEditingPost(null);
      await fetchPlan();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setActioningPost(null);
    }
  };

  const handleGeneratePlan = async () => {
    setGeneratingPlan(true);
    setError(null);
    try {
      await apiPost("/api/v1/ai/generate-plan", { campaign_id: campaignId });
      await fetchPlan();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Plan generation failed");
    } finally {
      setGeneratingPlan(false);
    }
  };

  const handleLaunch = async () => {
    try {
      await apiPost(`/api/v1/campaigns/${campaignId}/launch`, {});
      await fetchPlan();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Launch failed");
    }
  };

  if (loading) {
    return (
      <>
        <Header title="Campaign" />
        <LoadingOverlay text="Loading campaign plan..." />
      </>
    );
  }

  if (!plan) {
    return (
      <>
        <Header title="Campaign" />
        <div className="mt-8 text-center text-[var(--text-secondary)]">
          Campaign not found.
        </div>
      </>
    );
  }

  const { campaign, days, stats } = plan;
  const modeInfo = modeLabels[campaign.mode] || modeLabels.manual;

  return (
    <>
      <Header title={campaign.name} />

      {/* Back link */}
      <button
        onClick={() => router.push("/campaigns")}
        className="mt-4 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
      >
        &larr; Back to Campaigns
      </button>

      {error && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-50 px-4 py-3 text-sm text-red-600 flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-xs opacity-60 hover:opacity-100">Dismiss</button>
        </div>
      )}

      {/* Campaign Header Card */}
      <div className="mt-6 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-semibold text-[var(--text-primary)]">{campaign.name}</h2>
            <span className={cn("rounded-full px-2.5 py-0.5 text-xs font-medium", modeInfo.color)}>
              {modeInfo.label}
            </span>
            <span className={cn(
              "rounded-full px-2.5 py-0.5 text-xs font-medium",
              campaign.status === "active" ? "bg-green-100 text-green-600" :
              campaign.status === "paused" ? "bg-yellow-100 text-yellow-600" :
              campaign.status === "completed" ? "bg-blue-100 text-blue-600" :
              "bg-gray-100 text-gray-500"
            )}>
              {campaign.status}
            </span>
          </div>
          <div className="flex gap-2">
            {campaign.mode !== "manual" && days.length === 0 && (
              <button
                onClick={handleGeneratePlan}
                disabled={generatingPlan}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
              >
                {generatingPlan ? <ButtonSpinner label="Generating..." /> : "Generate AI Plan"}
              </button>
            )}
            {campaign.mode !== "manual" && days.length > 0 && (
              <button
                onClick={handleGeneratePlan}
                disabled={generatingPlan}
                className="rounded-lg bg-indigo-600/10 px-4 py-2 text-sm font-medium text-indigo-600 hover:bg-indigo-600/20 disabled:opacity-50"
              >
                {generatingPlan ? <ButtonSpinner label="Regenerating..." /> : "Regenerate Plan"}
              </button>
            )}
            {campaign.status === "draft" && (
              <button
                onClick={handleLaunch}
                className="rounded-lg bg-green-600 px-4 py-2 text-sm font-medium text-white hover:opacity-90"
              >
                Launch Campaign
              </button>
            )}
          </div>
        </div>
        {(campaign.start_date || campaign.end_date) && (
          <p className="mt-2 text-xs text-[var(--text-secondary)]">
            {campaign.start_date && `Starts ${campaign.start_date}`}
            {campaign.end_date && ` \u00B7 Ends ${campaign.end_date}`}
            {campaign.phase && ` \u00B7 ${campaign.phase.replace("_", " ")}`}
          </p>
        )}
      </div>

      {/* Stats Bar */}
      {stats.total_posts > 0 && (
        <div className="mt-4 grid grid-cols-5 gap-3">
          {[
            { label: "Total Posts", value: stats.total_posts, color: "text-[var(--text-primary)]" },
            { label: "Pending Review", value: stats.pending_review, color: "text-amber-600" },
            { label: "Approved", value: stats.approved, color: "text-green-600" },
            { label: "Rejected", value: stats.rejected, color: "text-red-600" },
            { label: "Published", value: stats.published, color: "text-blue-600" },
          ].map((s) => (
            <div key={s.label} className="rounded-lg border border-[var(--border-color)] bg-[var(--bg-surface)] p-3 text-center">
              <div className={cn("text-2xl font-bold", s.color)}>{s.value}</div>
              <div className="text-xs text-[var(--text-secondary)]">{s.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* Bulk Actions */}
      {stats.pending_review > 0 && campaign.mode === "ai_plan" && (
        <div className="mt-4 flex items-center gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
          <span className="text-sm text-amber-700">
            {stats.pending_review} post{stats.pending_review !== 1 ? "s" : ""} awaiting your review
          </span>
          <button
            onClick={handleApproveAll}
            disabled={approvingAll}
            className="ml-auto rounded-lg bg-green-600 px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {approvingAll ? <ButtonSpinner label="Approving..." /> : "Approve All"}
          </button>
        </div>
      )}

      {/* Empty State */}
      {days.length === 0 && (
        <div className="mt-8 rounded-xl border border-dashed border-[var(--border-color)] bg-[var(--bg-surface)] p-12 text-center">
          <p className="text-[var(--text-secondary)]">
            {campaign.mode === "manual"
              ? "No posts yet. Create posts manually from the Posts page."
              : "No plan generated yet. Click \"Generate AI Plan\" to get started."}
          </p>
        </div>
      )}

      {/* Daily Timeline */}
      <div className="mt-6 space-y-4">
        {days.map((day) => (
          <div key={day.day} className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] overflow-hidden">
            {/* Day Header */}
            <div className="bg-[var(--bg-primary)] px-5 py-3 border-b border-[var(--border-color)]">
              <div className="flex items-center gap-2">
                <span className="inline-flex items-center justify-center w-7 h-7 rounded-full bg-indigo-100 text-indigo-600 text-xs font-bold">
                  {day.day_number}
                </span>
                <span className="text-sm font-medium text-[var(--text-primary)]">{day.day}</span>
                <span className="text-xs text-[var(--text-secondary)]">
                  {day.posts.length} post{day.posts.length !== 1 ? "s" : ""}
                  {day.calendar_items.length > 0 && ` \u00B7 ${day.calendar_items.length} task${day.calendar_items.length !== 1 ? "s" : ""}`}
                </span>
              </div>
            </div>

            {/* Calendar Items */}
            {day.calendar_items.length > 0 && (
              <div className="px-5 py-2 border-b border-[var(--border-color)] bg-slate-50/50">
                {day.calendar_items.map((item) => (
                  <div key={item.id} className="flex items-center gap-2 py-1.5 text-xs text-[var(--text-secondary)]">
                    <span className="text-[10px]">{"\u{1F4CB}"}</span>
                    <span>{item.title}</span>
                    {item.scheduled_time && <span className="opacity-50">{item.scheduled_time}</span>}
                  </div>
                ))}
              </div>
            )}

            {/* Posts */}
            <div className="divide-y divide-[var(--border-color)]">
              {day.posts.map((post) => {
                const isEditing = editingPost === post.id;
                const isActioning = actioningPost === post.id;

                return (
                  <div key={post.id} className="px-5 py-4">
                    <div className="flex items-start gap-3">
                      {/* Platform Icon */}
                      <span className="text-lg mt-0.5">{platformIcons[post.platform] || "\u{1F4DD}"}</span>

                      {/* Content */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1.5">
                          <span className="text-xs font-medium text-[var(--text-primary)] capitalize">{post.platform}</span>
                          {post.action_type_label && (
                            <span className="rounded bg-[var(--bg-primary)] px-1.5 py-0.5 text-[10px] text-[var(--text-secondary)]">
                              {post.action_type_label}
                            </span>
                          )}
                          {post.approval_status && (
                            <span className={cn(
                              "rounded-full px-2 py-0.5 text-[10px] font-medium",
                              approvalColors[post.approval_status] || "bg-gray-100 text-gray-500"
                            )}>
                              {post.approval_status === "pending_review" ? "Pending Review" :
                               post.approval_status === "approved" ? "Approved" : "Rejected"}
                            </span>
                          )}
                        </div>

                        {isEditing ? (
                          <div className="space-y-2">
                            <textarea
                              value={editText}
                              onChange={(e) => setEditText(e.target.value)}
                              rows={4}
                              className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-indigo-500 focus:outline-none resize-none"
                            />
                            <div className="flex gap-2">
                              <button
                                onClick={() => handleSaveEdit(post.id)}
                                disabled={isActioning}
                                className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
                              >
                                {isActioning ? <ButtonSpinner label="Saving..." /> : "Save"}
                              </button>
                              <button
                                onClick={() => setEditingPost(null)}
                                className="rounded-lg px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                              >
                                Cancel
                              </button>
                            </div>
                          </div>
                        ) : (
                          <p className="text-sm text-[var(--text-secondary)] whitespace-pre-line">{post.content_text}</p>
                        )}

                        {post.destination_url && (
                          <p className="mt-1 text-xs text-indigo-500 truncate">{"\u{1F517}"} {post.destination_url}</p>
                        )}
                      </div>

                      {/* Actions */}
                      {!isEditing && post.approval_status === "pending_review" && (
                        <div className="flex gap-1.5 shrink-0">
                          <button
                            onClick={() => handlePostAction(post.id, "review-approve")}
                            disabled={isActioning}
                            className="rounded-lg bg-green-600 px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
                            title="Approve"
                          >
                            {isActioning ? "..." : "\u2713 Approve"}
                          </button>
                          <button
                            onClick={() => {
                              setEditingPost(post.id);
                              setEditText(post.content_text);
                            }}
                            className="rounded-lg bg-[var(--bg-primary)] px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] border border-[var(--border-color)]"
                            title="Edit"
                          >
                            Edit
                          </button>
                          <button
                            onClick={() => handlePostAction(post.id, "review-reject")}
                            disabled={isActioning}
                            className="rounded-lg bg-red-50 px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-100 disabled:opacity-50"
                            title="Reject"
                          >
                            {"\u2715"}
                          </button>
                        </div>
                      )}
                      {!isEditing && post.approval_status === "approved" && (
                        <span className="text-green-500 text-lg" title="Approved">{"\u2713"}</span>
                      )}
                      {!isEditing && post.approval_status === "rejected" && (
                        <span className="text-red-400 text-lg" title="Rejected">{"\u2715"}</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
