"use client";

import { useState, useEffect, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { apiGet, apiPost, apiDelete } from "@/lib/api";
import { useAuth } from "@/components/auth/auth-provider";
import { cn } from "@/lib/utils";

/* ── Types ────────────────────────────────────────────────── */

interface QueueHealth {
  pending: number;
  processing: number;
  dead_letter: number;
  completed_24h: number;
  failed_24h: number;
  oldest_processing_age_seconds: number;
}

interface HealthCheck {
  status: string;
  mode: string;
  version: string;
  checks: Record<string, string>;
}

interface DLQEntry {
  job_id: string;
  job_type: string;
  attempt: number;
  last_error: string;
  enqueued_at: string;
}

interface ChannelHealth {
  id: string;
  platform: string;
  display_name: string;
  is_active: boolean;
  connection_status: string;
  last_health_check_at: string | null;
}

/* ── Helpers ──────────────────────────────────────────────── */

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      className={cn(
        "inline-block h-2.5 w-2.5 rounded-full",
        ok ? "bg-green-500" : "bg-red-500"
      )}
    />
  );
}

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-4">
      <p className="text-xs text-[var(--text-secondary)]">{label}</p>
      <p className="mt-1 text-2xl font-bold text-[var(--text-primary)]">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-[var(--text-secondary)]">{sub}</p>}
    </div>
  );
}

const healthBadge: Record<string, string> = {
  connected: "bg-green-100 text-green-700",
  healthy: "bg-green-100 text-green-700",
  expiring_soon: "bg-yellow-100 text-yellow-700",
  needs_reconnect: "bg-red-100 text-red-700",
  expired: "bg-red-100 text-red-700",
  revoked: "bg-red-100 text-red-700",
  error: "bg-red-100 text-red-700",
  unknown: "bg-gray-100 text-gray-500",
};

const platformEmoji: Record<string, string> = {
  instagram: "📷",
  youtube: "▶️",
  tiktok: "🎵",
  twitter: "𝕏",
};

/* ── Page ─────────────────────────────────────────────────── */

export default function OpsPage() {
  const { role } = useAuth();
  const isAdmin = role === "admin" || role === "owner";

  const [health, setHealth] = useState<HealthCheck | null>(null);
  const [queue, setQueue] = useState<QueueHealth | null>(null);
  const [dlq, setDlq] = useState<DLQEntry[]>([]);
  const [channels, setChannels] = useState<ChannelHealth[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const fetchAll = useCallback(async () => {
    setError(null);
    try {
      const [h, q, d, c] = await Promise.all([
        apiGet<HealthCheck>("/health"),
        apiGet<QueueHealth>("/api/v1/admin/queue/health").catch(() => null),
        apiGet<DLQEntry[]>("/api/v1/admin/queue/dlq?limit=10").catch(() => []),
        apiGet<ChannelHealth[]>("/api/v1/channels").catch(() => []),
      ]);
      setHealth(h);
      setQueue(q);
      setDlq(d);
      setChannels(c);
      setLastRefresh(new Date());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load ops data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  // Auto-refresh every 30s
  useEffect(() => {
    const interval = setInterval(fetchAll, 30_000);
    return () => clearInterval(interval);
  }, [fetchAll]);

  const handleRecoverStuck = async () => {
    try {
      const res = await apiPost<{ recovered: number }>("/api/v1/admin/queue/recover-stuck", {});
      alert(`Recovered ${res.recovered} stuck jobs`);
      fetchAll();
    } catch { /* ignore */ }
  };

  const handleFlushDLQ = async () => {
    if (!confirm("Clear all dead-letter queue entries?")) return;
    try {
      await apiDelete("/api/v1/admin/queue/dlq");
      fetchAll();
    } catch { /* ignore */ }
  };

  const handleReplayDLQ = async (index: number) => {
    try {
      await apiPost(`/api/v1/admin/queue/dlq/${index}/replay`, {});
      fetchAll();
    } catch { /* ignore */ }
  };

  if (!isAdmin) {
    return (
      <>
        <Header title="Ops Dashboard" />
        <div className="flex items-center justify-center py-20">
          <p className="text-[var(--text-secondary)]">Admin access required.</p>
        </div>
      </>
    );
  }

  return (
    <>
      <Header title="Ops Dashboard" />

      {error && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-50 px-4 py-3 text-sm text-red-600">
          {error}
        </div>
      )}

      <div className="mt-2 flex items-center gap-3">
        <button
          onClick={fetchAll}
          className="rounded-lg bg-[var(--bg-surface)] px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)] transition-colors"
        >
          Refresh
        </button>
        {lastRefresh && (
          <span className="text-xs text-[var(--text-secondary)]">
            Last updated: {lastRefresh.toLocaleTimeString()}
          </span>
        )}
      </div>

      {loading ? (
        <div className="mt-6 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
          Loading...
        </div>
      ) : (
        <>
          {/* ── System Health ─────────────────────────── */}
          <section className="mt-6">
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">System Health</h2>
            {health && (
              <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
                <div className="flex items-center gap-2 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] px-4 py-3">
                  <StatusDot ok={health.status === "ok"} />
                  <div>
                    <p className="text-xs text-[var(--text-secondary)]">Overall</p>
                    <p className="text-sm font-medium text-[var(--text-primary)]">{health.status}</p>
                  </div>
                </div>
                {Object.entries(health.checks).map(([name, status]) => (
                  <div
                    key={name}
                    className="flex items-center gap-2 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] px-4 py-3"
                  >
                    <StatusDot ok={status === "ok"} />
                    <div>
                      <p className="text-xs text-[var(--text-secondary)] capitalize">{name}</p>
                      <p className="text-sm font-medium text-[var(--text-primary)]">{status}</p>
                    </div>
                  </div>
                ))}
                <div className="flex items-center gap-2 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] px-4 py-3">
                  <span className="text-xs text-[var(--text-secondary)]">v{health.version}</span>
                  <span className="ml-auto rounded-full bg-indigo-100 px-2 py-0.5 text-xs text-indigo-600">
                    {health.mode}
                  </span>
                </div>
              </div>
            )}
          </section>

          {/* ── Queue Metrics ─────────────────────────── */}
          {queue && (
            <section className="mt-8">
              <div className="flex items-center gap-3">
                <h2 className="text-sm font-semibold text-[var(--text-primary)]">Job Queue</h2>
                <button
                  onClick={handleRecoverStuck}
                  className="ml-auto rounded-lg bg-[var(--bg-surface)] px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)] transition-colors"
                >
                  Recover Stuck
                </button>
              </div>
              <div className="mt-3 grid grid-cols-3 gap-3 sm:grid-cols-6">
                <StatCard label="Pending" value={queue.pending} />
                <StatCard label="Processing" value={queue.processing} />
                <StatCard label="Dead Letter" value={queue.dead_letter} />
                <StatCard label="Completed (24h)" value={queue.completed_24h} />
                <StatCard label="Failed (24h)" value={queue.failed_24h} />
                <StatCard
                  label="Oldest In-Flight"
                  value={queue.oldest_processing_age_seconds > 0 ? `${Math.round(queue.oldest_processing_age_seconds)}s` : "—"}
                />
              </div>

              {/* Success rate */}
              {(queue.completed_24h + queue.failed_24h) > 0 && (
                <div className="mt-3 flex items-center gap-3">
                  <div className="h-2 flex-1 rounded-full bg-red-200 overflow-hidden">
                    <div
                      className="h-full rounded-full bg-green-500 transition-all"
                      style={{
                        width: `${(queue.completed_24h / (queue.completed_24h + queue.failed_24h)) * 100}%`,
                      }}
                    />
                  </div>
                  <span className="text-xs font-medium text-[var(--text-secondary)]">
                    {Math.round((queue.completed_24h / (queue.completed_24h + queue.failed_24h)) * 100)}% success
                  </span>
                </div>
              )}
            </section>
          )}

          {/* ── Dead Letter Queue ─────────────────────── */}
          {dlq.length > 0 && (
            <section className="mt-8">
              <div className="flex items-center gap-3">
                <h2 className="text-sm font-semibold text-[var(--text-primary)]">Dead Letter Queue</h2>
                <button
                  onClick={handleFlushDLQ}
                  className="ml-auto rounded-lg bg-red-50 px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-100 transition-colors"
                >
                  Flush All
                </button>
              </div>
              <div className="mt-3 space-y-2">
                {dlq.map((entry, i) => (
                  <div
                    key={entry.job_id || i}
                    className="rounded-xl border border-red-200 bg-red-50/50 px-4 py-3"
                  >
                    <div className="flex items-center gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-mono font-medium text-[var(--text-primary)]">
                            {entry.job_type}
                          </span>
                          <span className="text-xs text-[var(--text-secondary)]">
                            attempt {entry.attempt}
                          </span>
                        </div>
                        <p className="mt-1 text-xs text-red-600 truncate">{entry.last_error}</p>
                      </div>
                      <button
                        onClick={() => handleReplayDLQ(i)}
                        className="shrink-0 rounded-lg bg-white px-3 py-1.5 text-xs font-medium text-indigo-600 border border-indigo-200 hover:bg-indigo-50 transition-colors"
                      >
                        Replay
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* ── Channel Health ────────────────────────── */}
          {channels.length > 0 && (
            <section className="mt-8">
              <h2 className="text-sm font-semibold text-[var(--text-primary)]">Channel Health</h2>
              <div className="mt-3 space-y-2">
                {channels.map((ch) => {
                  const status = ch.connection_status || (ch.is_active ? "connected" : "unknown");
                  const badge = healthBadge[status] || healthBadge.unknown;
                  const lastCheck = ch.last_health_check_at
                    ? new Date(ch.last_health_check_at)
                    : null;

                  return (
                    <div
                      key={ch.id}
                      className="flex items-center gap-3 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] px-4 py-3"
                    >
                      <span className="text-lg">{platformEmoji[ch.platform] || "🔗"}</span>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-[var(--text-primary)]">
                          {ch.display_name || ch.platform}
                        </p>
                        <p className="text-xs text-[var(--text-secondary)]">
                          {ch.platform}
                          {lastCheck && (
                            <> · last checked {lastCheck.toLocaleString()}</>
                          )}
                        </p>
                      </div>
                      <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium", badge)}>
                        {status.replace(/_/g, " ")}
                      </span>
                    </div>
                  );
                })}
              </div>
            </section>
          )}
        </>
      )}
    </>
  );
}
