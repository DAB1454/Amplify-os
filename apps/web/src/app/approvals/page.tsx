"use client";

import { useState, useEffect, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { apiGet, apiPost } from "@/lib/api";

interface Comment {
  id: string;
  user_id: string | null;
  body: string;
  created_at: string;
}

interface ApprovalItem {
  id: string;
  action_type: string;
  entity_type: string;
  entity_id: string | null;
  risk_level: string;
  risk_reasons: string[];
  status: string;
  notes: string;
  requested_by: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  expires_at: string | null;
  comments: Comment[];
  created_at: string;
}

const riskColors: Record<string, string> = {
  low: "bg-green-100 text-green-600",
  medium: "bg-yellow-100 text-yellow-600",
  high: "bg-orange-100 text-orange-600",
  critical: "bg-red-100 text-red-600",
};

const statusColors: Record<string, string> = {
  pending: "bg-yellow-100 text-yellow-600",
  resubmitted: "bg-blue-100 text-blue-600",
  approved: "bg-green-100 text-green-600",
  rejected: "bg-red-100 text-red-600",
  revision_requested: "bg-orange-100 text-orange-600",
  expired: "bg-gray-100 text-gray-500",
};

type TabKey = "pending" | "history";

export default function ApprovalsPage() {
  const [approvals, setApprovals] = useState<ApprovalItem[]>([]);
  const [tab, setTab] = useState<TabKey>("pending");
  const [loading, setLoading] = useState(true);
  const [pendingCount, setPendingCount] = useState(0);
  const [commentText, setCommentText] = useState<Record<string, string>>({});
  const [decisionComment, setDecisionComment] = useState<Record<string, string>>({});
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchApprovals = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const endpoint =
        tab === "pending"
          ? "/api/v1/approvals/pending"
          : "/api/v1/approvals/history";
      const data = await apiGet<ApprovalItem[]>(endpoint);
      setApprovals(data);
    } catch (err) {
      setApprovals([]);
      setError(err instanceof Error ? err.message : "Failed to load approvals");
    } finally {
      setLoading(false);
    }
  }, [tab]);

  const fetchCount = useCallback(async () => {
    try {
      const data = await apiGet<{ pending_count: number }>("/api/v1/approvals/count");
      setPendingCount(data.pending_count);
    } catch {
      // Count badge is non-critical
    }
  }, []);

  useEffect(() => {
    fetchApprovals();
    fetchCount();
  }, [fetchApprovals, fetchCount]);

  const handleDecision = async (id: string, action: "approve" | "reject" | "request-revision") => {
    const comment = decisionComment[id] || "";
    await apiPost(`/api/v1/approvals/${id}/${action}`, { comment });
    setDecisionComment((prev) => ({ ...prev, [id]: "" }));
    fetchApprovals();
    fetchCount();
  };

  const handleResubmit = async (id: string) => {
    await apiPost(`/api/v1/approvals/${id}/resubmit`, { notes: "Resubmitted" });
    fetchApprovals();
    fetchCount();
  };

  const handleAddComment = async (id: string) => {
    const body = commentText[id];
    if (!body?.trim()) return;
    await apiPost(`/api/v1/approvals/${id}/comments`, { body });
    setCommentText((prev) => ({ ...prev, [id]: "" }));
    fetchApprovals();
  };

  const formatAction = (t: string) => t.replace(/_/g, " ");
  const formatDate = (s: string) => new Date(s).toLocaleString();

  return (
    <>
      <Header title="Approvals" />

      {/* Tabs */}
      <div className="mt-8 flex items-center gap-3">
        <button
          onClick={() => setTab("pending")}
          className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
            tab === "pending"
              ? "bg-[var(--brand-gold)] text-white"
              : "bg-[var(--bg-surface)] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)]"
          }`}
        >
          Pending
        </button>
        {pendingCount > 0 && (
          <span className="inline-flex h-6 min-w-6 items-center justify-center rounded-full bg-[var(--brand-gold)] px-2 text-xs font-bold text-white">
            {pendingCount}
          </span>
        )}
        <button
          onClick={() => setTab("history")}
          className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
            tab === "history"
              ? "bg-[var(--brand-gold)] text-white"
              : "bg-[var(--bg-surface)] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)]"
          }`}
        >
          History
        </button>
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-50 px-4 py-3 text-sm text-red-600">
          {error}
        </div>
      )}

      {/* List */}
      <div className="mt-6 space-y-4">
        {loading ? (
          <div className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
            Loading...
          </div>
        ) : approvals.length === 0 ? (
          <div className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
            {tab === "pending" ? "No pending approvals." : "No approval history yet."}
          </div>
        ) : (
          approvals.map((item) => {
            const expanded = expandedId === item.id;
            return (
              <div
                key={item.id}
                className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] overflow-hidden"
              >
                {/* Header row */}
                <button
                  onClick={() => setExpandedId(expanded ? null : item.id)}
                  className="w-full p-5 text-left hover:bg-[var(--bg-surface-hover)] transition-colors"
                >
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <div className="flex items-center gap-3 mb-1">
                        <span className="text-sm font-medium capitalize text-[var(--text-primary)]">
                          {formatAction(item.action_type)}
                        </span>
                        <span
                          className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                            statusColors[item.status] || "bg-gray-100 text-gray-500"
                          }`}
                        >
                          {item.status.replace(/_/g, " ")}
                        </span>
                        <span
                          className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                            riskColors[item.risk_level] || "bg-gray-100 text-gray-500"
                          }`}
                        >
                          {item.risk_level} risk
                        </span>
                      </div>

                      {item.risk_reasons.length > 0 && (
                        <ul className="mt-1 text-xs text-[var(--text-secondary)]">
                          {item.risk_reasons.map((r, i) => (
                            <li key={i}>- {r}</li>
                          ))}
                        </ul>
                      )}

                      {item.notes && (
                        <p className="mt-1 text-xs text-[var(--text-secondary)]">
                          Notes: {item.notes}
                        </p>
                      )}

                      <div className="mt-2 flex gap-4 text-xs text-[var(--text-secondary)]">
                        <span>Created: {formatDate(item.created_at)}</span>
                        {item.reviewed_at && (
                          <span>Reviewed: {formatDate(item.reviewed_at)}</span>
                        )}
                        {item.expires_at && (
                          <span>Expires: {formatDate(item.expires_at)}</span>
                        )}
                      </div>
                    </div>

                    <div className="ml-4 text-xs text-[var(--text-secondary)]">
                      {item.comments.length > 0 && (
                        <span>{item.comments.length} comment{item.comments.length !== 1 ? "s" : ""}</span>
                      )}
                    </div>
                  </div>
                </button>

                {/* Expanded section */}
                {expanded && (
                  <div className="border-t border-[var(--border-color)] p-5 space-y-4">
                    {/* Comments */}
                    {item.comments.length > 0 && (
                      <div className="space-y-2">
                        <h4 className="text-xs font-semibold text-[var(--text-secondary)] uppercase tracking-wider">
                          Comments
                        </h4>
                        {item.comments.map((c) => (
                          <div
                            key={c.id}
                            className="rounded-lg bg-[var(--bg-primary)] p-3 text-sm"
                          >
                            <p className="text-[var(--text-primary)]">{c.body}</p>
                            <p className="mt-1 text-xs text-[var(--text-secondary)]">
                              {formatDate(c.created_at)}
                            </p>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Add comment */}
                    <div className="flex gap-2">
                      <input
                        type="text"
                        placeholder="Add a comment..."
                        value={commentText[item.id] || ""}
                        onChange={(e) =>
                          setCommentText((prev) => ({ ...prev, [item.id]: e.target.value }))
                        }
                        className="flex-1 rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-secondary)]"
                      />
                      <button
                        onClick={() => handleAddComment(item.id)}
                        className="rounded-lg bg-[var(--bg-surface-hover)] px-3 py-2 text-xs font-medium text-[var(--text-primary)] hover:bg-[var(--border-color)]"
                      >
                        Post
                      </button>
                    </div>

                    {/* Actions */}
                    {(item.status === "pending" || item.status === "resubmitted") && (
                      <div className="space-y-3">
                        <input
                          type="text"
                          placeholder="Decision comment (optional)..."
                          value={decisionComment[item.id] || ""}
                          onChange={(e) =>
                            setDecisionComment((prev) => ({
                              ...prev,
                              [item.id]: e.target.value,
                            }))
                          }
                          className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-secondary)]"
                        />
                        <div className="flex gap-2">
                          <button
                            onClick={() => handleDecision(item.id, "approve")}
                            className="rounded-lg bg-green-600 px-4 py-2 text-xs font-medium text-white hover:bg-green-700"
                          >
                            Approve
                          </button>
                          <button
                            onClick={() => handleDecision(item.id, "reject")}
                            className="rounded-lg bg-red-100 px-4 py-2 text-xs font-medium text-red-600 hover:bg-red-600/30"
                          >
                            Reject
                          </button>
                          <button
                            onClick={() => handleDecision(item.id, "request-revision")}
                            className="rounded-lg bg-orange-100 px-4 py-2 text-xs font-medium text-orange-600 hover:bg-orange-600/30"
                          >
                            Request Revision
                          </button>
                        </div>
                      </div>
                    )}

                    {(item.status === "rejected" || item.status === "revision_requested") && (
                      <button
                        onClick={() => handleResubmit(item.id)}
                        className="rounded-lg bg-[var(--brand-gold)] px-4 py-2 text-xs font-medium text-white hover:opacity-90"
                      >
                        Resubmit
                      </button>
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </>
  );
}
