"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Pause,
  Play,
  RefreshCw,
  Settings as SettingsIcon,
  XCircle,
} from "lucide-react";
import { Header } from "@/components/layout/header";
import { apiGet, apiPut } from "@/lib/api";
import { cn, formatLocal } from "@/lib/utils";

// ── Types ──────────────────────────────────────────────────────

interface AutomationAuditEntry {
  id: string;
  tenant_id: string;
  action: string;
  reason: string;
  confidence: number | null;
  outcome: string;
  error: string | null;
  entity_type: string | null;
  entity_id: string | null;
  snapshot: Record<string, unknown> | null;
  created_at: string;
}

interface TenantProfile {
  id: string;
  name: string;
  automation_level: string;
  automation_paused: boolean;
}

// ── Constants ──────────────────────────────────────────────────

// Closed set of agent action verbs — kept in sync with
// AUTOMATION_ACTIONS in packages/db/src/amplify/db/models/automation_audit.py.
// If you add a new verb on the backend, add it here too or the filter
// dropdown will silently miss it.
const ACTION_FILTERS = [
  { key: "", label: "All actions" },
  { key: "tick", label: "Tick" },
  { key: "plan", label: "Plan" },
  { key: "approve_batch", label: "Approve batch" },
  { key: "generate_media", label: "Generate media" },
  { key: "publish", label: "Publish" },
  { key: "start_experiment", label: "Start experiment" },
  { key: "score_experiment", label: "Score experiment" },
  { key: "rerank", label: "Rerank" },
  { key: "escalate", label: "Escalate" },
  { key: "pause", label: "Pause" },
  { key: "resume", label: "Resume" },
  { key: "noop", label: "Noop" },
  { key: "error", label: "Error" },
] as const;

const LEVEL_LABELS: Record<string, string> = {
  manual: "Manual",
  assisted: "AI-assisted",
  auto_campaigns: "Auto campaigns",
  autonomous: "Fully autonomous",
};

// ── Helpers ────────────────────────────────────────────────────

function OutcomeBadge({ outcome }: { outcome: string }) {
  const map: Record<string, { cls: string; Icon: typeof CheckCircle2 }> = {
    success: { cls: "bg-green-100 text-green-700", Icon: CheckCircle2 },
    skipped: { cls: "bg-gray-100 text-gray-600", Icon: Activity },
    escalated: { cls: "bg-yellow-100 text-yellow-700", Icon: AlertTriangle },
    failed: { cls: "bg-red-100 text-red-700", Icon: XCircle },
  };
  const { cls, Icon } = map[outcome] || map.success;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium",
        cls
      )}
    >
      <Icon className="h-3 w-3" />
      {outcome}
    </span>
  );
}

function ActionBadge({ action }: { action: string }) {
  return (
    <span className="rounded-md bg-indigo-50 px-2 py-0.5 text-[11px] font-mono font-semibold text-indigo-700">
      {action}
    </span>
  );
}

function ConfidencePill({ value }: { value: number | null }) {
  if (value == null) return null;
  const pct = Math.round(value * 100);
  const color = pct >= 70 ? "text-green-600" : pct >= 40 ? "text-yellow-600" : "text-red-600";
  return (
    <span className={cn("text-[10px] font-mono", color)}>{pct}%</span>
  );
}

// ── Page ───────────────────────────────────────────────────────

export default function AutomationPage() {
  const [tenant, setTenant] = useState<TenantProfile | null>(null);
  const [entries, setEntries] = useState<AutomationAuditEntry[]>([]);
  const [actionFilter, setActionFilter] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [pauseBusy, setPauseBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Toggle the kill switch. Optimistic update so the user gets instant
  // feedback; we revert if the PUT fails.
  const togglePause = useCallback(async () => {
    if (!tenant || pauseBusy) return;
    const next = !tenant.automation_paused;
    setPauseBusy(true);
    setError(null);
    const previous = tenant;
    setTenant({ ...tenant, automation_paused: next });
    try {
      const updated = await apiPut<TenantProfile>("/api/v1/tenants/me", {
        automation_paused: next,
      });
      setTenant(updated);
    } catch (e) {
      setTenant(previous);
      setError(e instanceof Error ? e.message : "Failed to update pause state");
    } finally {
      setPauseBusy(false);
    }
  }, [tenant, pauseBusy]);

  const fetchEntries = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const qs = actionFilter ? `?action=${encodeURIComponent(actionFilter)}` : "";
      const [t, items] = await Promise.all([
        apiGet<TenantProfile>("/api/v1/tenants/me").catch(() => null),
        apiGet<AutomationAuditEntry[]>(`/api/v1/automation/audit${qs}`),
      ]);
      if (t) setTenant(t);
      setEntries(items || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load automation log");
    } finally {
      setLoading(false);
    }
  }, [actionFilter]);

  useEffect(() => {
    void fetchEntries();
  }, [fetchEntries]);

  const isManual = (tenant?.automation_level || "manual") === "manual";

  return (
    <>
      <Header title="Automation" subtitle="Agent decision trail — what the autonomous worker has been doing for you" />

      <div className="mx-auto max-w-5xl space-y-6 p-6">
        {/* Status banner */}
        <div className="rounded-xl border border-[var(--border-color)] bg-white p-5 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <div
                className={cn(
                  "flex h-10 w-10 items-center justify-center rounded-full",
                  tenant?.automation_paused
                    ? "bg-red-100 text-red-600"
                    : isManual
                    ? "bg-gray-100 text-gray-500"
                    : "bg-indigo-100 text-indigo-600"
                )}
              >
                {tenant?.automation_paused ? (
                  <Pause className="h-5 w-5" />
                ) : (
                  <Activity className="h-5 w-5" />
                )}
              </div>
              <div>
                <div className="text-sm font-semibold text-[var(--text-primary)]">
                  {tenant?.automation_paused
                    ? "Automation paused"
                    : `Automation: ${LEVEL_LABELS[tenant?.automation_level || "manual"] || "Manual"}`}
                </div>
                <div className="text-xs text-[var(--text-secondary)]">
                  {isManual
                    ? "You're driving manually. The agent isn't taking any actions."
                    : "The agent will record every action it takes here."}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {tenant && !isManual && (
                <button
                  onClick={() => void togglePause()}
                  disabled={pauseBusy}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-semibold transition-colors disabled:opacity-50",
                    tenant.automation_paused
                      ? "bg-green-600 text-white hover:bg-green-700"
                      : "bg-red-600 text-white hover:bg-red-700"
                  )}
                >
                  {tenant.automation_paused ? (
                    <>
                      <Play className="h-3.5 w-3.5" />
                      Resume agent
                    </>
                  ) : (
                    <>
                      <Pause className="h-3.5 w-3.5" />
                      Pause agent
                    </>
                  )}
                </button>
              )}
              <Link
                href="/settings"
                className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--border-color)] bg-white px-3 py-2 text-xs font-medium text-[var(--text-primary)] hover:bg-[var(--bg-surface-hover)]"
              >
                <SettingsIcon className="h-3.5 w-3.5" />
                Change level
              </Link>
            </div>
          </div>
        </div>

        {/* Filter + refresh */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <label className="text-xs font-medium text-[var(--text-secondary)]">Filter:</label>
            <select
              value={actionFilter}
              onChange={(e) => setActionFilter(e.target.value)}
              className="rounded-lg border border-[var(--border-color)] bg-white px-3 py-1.5 text-xs"
            >
              {ACTION_FILTERS.map((f) => (
                <option key={f.key} value={f.key}>
                  {f.label}
                </option>
              ))}
            </select>
          </div>
          <button
            onClick={() => void fetchEntries()}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--border-color)] bg-white px-3 py-1.5 text-xs font-medium hover:bg-[var(--bg-surface-hover)] disabled:opacity-50"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
            Refresh
          </button>
        </div>

        {/* Error */}
        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700">
            {error}
          </div>
        )}

        {/* Timeline */}
        <div className="rounded-xl border border-[var(--border-color)] bg-white shadow-sm">
          {entries.length === 0 && !loading ? (
            <div className="p-12 text-center">
              <Activity className="mx-auto h-10 w-10 text-[var(--text-secondary)] opacity-40" />
              <div className="mt-3 text-sm font-medium text-[var(--text-primary)]">
                No agent actions yet
              </div>
              <div className="mt-1 text-xs text-[var(--text-secondary)]">
                {isManual
                  ? "Switch off Manual mode in Settings to let the agent start working."
                  : "The agent hasn't ticked yet. Once it runs, every decision will appear here."}
              </div>
            </div>
          ) : (
            <ul className="divide-y divide-[var(--border-color)]">
              {entries.map((e) => (
                <li key={e.id} className="p-4">
                  <div className="flex items-start gap-3">
                    <div className="flex flex-col items-center pt-1">
                      <div className="h-2 w-2 rounded-full bg-indigo-500" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <ActionBadge action={e.action} />
                        <OutcomeBadge outcome={e.outcome} />
                        <ConfidencePill value={e.confidence} />
                        <span className="text-[10px] text-[var(--text-secondary)]">
                          {formatLocal(e.created_at)}
                        </span>
                        {e.entity_type && (
                          <span className="text-[10px] text-[var(--text-secondary)]">
                            · {e.entity_type}
                          </span>
                        )}
                      </div>
                      {e.reason && (
                        <p className="mt-1.5 text-xs text-[var(--text-primary)]">{e.reason}</p>
                      )}
                      {e.error && (
                        <p className="mt-1.5 text-xs font-mono text-red-600">{e.error}</p>
                      )}
                      {e.snapshot && Object.keys(e.snapshot).length > 0 && (
                        <details className="mt-2">
                          <summary className="cursor-pointer text-[10px] font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
                            Decider snapshot
                          </summary>
                          <pre className="mt-1 max-h-48 overflow-auto rounded-md bg-[var(--bg-primary)] p-2 text-[10px] font-mono text-[var(--text-secondary)]">
                            {JSON.stringify(e.snapshot, null, 2)}
                          </pre>
                        </details>
                      )}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </>
  );
}
