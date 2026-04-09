"use client";

import { useState, useEffect, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { apiGet, apiPut, apiPost } from "@/lib/api";
import { cn } from "@/lib/utils";
import { LoadingOverlay, ButtonSpinner } from "@/components/ui/spinner";

// ── Types ──────────────────────────────────────────────────────

interface ExplorationControls {
  enabled: boolean;
  max_exploration_rate: number;
  confidence_threshold: number;
  min_sample_size: number;
  quiet_hours_start: number | null;
  quiet_hours_end: number | null;
  max_experiments_per_week: number;
  channel_limits: Record<string, number>;
  rollback_threshold: number;
  baseline_window: number;
  require_approval_for_experiments: boolean;
  shadow_only: boolean;
}

interface ExperimentStats {
  controls: ExplorationControls;
  experiments_this_week: number;
  total_observations: number;
  platform_usage: Record<string, number>;
  budget_remaining: number;
}

interface AlertItem {
  severity: string;
  code: string;
  message: string;
  timestamp: string;
}

interface RollbackResult {
  should_rollback: boolean;
  reason: string;
  baseline_reward: number | null;
  current_reward: number | null;
  drop_fraction: number | null;
  threshold: number | null;
  alerts: AlertItem[];
}

interface TenantProfile {
  id: string;
  name: string;
  automation_level: "manual" | "assisted" | "auto_campaigns" | "autonomous";
  automation_paused: boolean;
}

const AUTOMATION_LEVELS: {
  key: TenantProfile["automation_level"];
  title: string;
  tagline: string;
  details: string[];
}[] = [
  {
    key: "manual",
    title: "Manual",
    tagline: "You drive everything. AI is off.",
    details: [
      "You generate plans by clicking Generate Plan",
      "You approve every post yourself",
      "You generate media yourself",
      "You publish on your own schedule",
    ],
  },
  {
    key: "assisted",
    title: "AI Assisted",
    tagline: "AI drafts, you approve.",
    details: [
      "AI generates campaign plans automatically",
      "You review and approve every post before it goes out",
      "AI generates media after you approve",
      "You still publish (or schedule) manually",
    ],
  },
  {
    key: "auto_campaigns",
    title: "AI Full Campaigns",
    tagline: "AI runs the campaign end-to-end. You hold the kill switch.",
    details: [
      "AI generates AND auto-approves posts within guardrails",
      "AI generates media and schedules everything",
      "AI publishes on the calendar automatically",
      "You kick off each new campaign manually",
      "Pause button stops everything immediately",
    ],
  },
  {
    key: "autonomous",
    title: "Fully Autonomous",
    tagline: "Set it and walk away. AI replans on its own.",
    details: [
      "AI decides when a new campaign is needed",
      "AI generates, approves, schedules, publishes — all automatic",
      "AI watches metrics and replans based on what's working",
      "AI tests new library assets against proven winners",
      "You only see notifications when something needs your attention",
    ],
  },
];

// ── Component ──────────────────────────────────────────────────

export default function SettingsPage() {
  const [controls, setControls] = useState<ExplorationControls | null>(null);
  const [stats, setStats] = useState<ExperimentStats | null>(null);
  const [anomalies, setAnomalies] = useState<AlertItem[]>([]);
  const [rollback, setRollback] = useState<RollbackResult | null>(null);
  const [tenant, setTenant] = useState<TenantProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [banner, setBanner] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [tab, setTab] = useState<"automation" | "controls" | "monitoring">("automation");

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [controlsData, statsData, anomalyData, tenantData] = await Promise.all([
        apiGet<ExplorationControls>("/api/v1/safety/controls").catch(() => null),
        apiGet<ExperimentStats>("/api/v1/safety/stats").catch(() => null),
        apiGet<{ alerts: AlertItem[] }>("/api/v1/safety/anomalies").catch(() => ({ alerts: [] })),
        apiGet<TenantProfile>("/api/v1/tenants/me"),
      ]);
      if (controlsData) setControls(controlsData);
      if (statsData) setStats(statsData);
      setAnomalies(anomalyData.alerts);
      setTenant(tenantData);
    } catch (err) {
      setBanner({ type: "error", message: err instanceof Error ? err.message : "Failed to load settings" });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const saveControls = async (updates: Partial<ExplorationControls>) => {
    setSaving(true);
    setBanner(null);
    try {
      const updated = await apiPut<ExplorationControls>("/api/v1/safety/controls", updates);
      setControls(updated);
      setBanner({ type: "success", message: "Exploration controls saved." });
    } catch (e: any) {
      setBanner({ type: "error", message: e?.detail || "Failed to save." });
    } finally {
      setSaving(false);
    }
  };

  const saveAutomation = async (updates: Partial<TenantProfile>) => {
    setSaving(true);
    setBanner(null);
    try {
      const updated = await apiPut<TenantProfile>("/api/v1/tenants/me", updates);
      setTenant(updated);
      setBanner({ type: "success", message: "Automation settings saved." });
    } catch (e: any) {
      setBanner({ type: "error", message: e?.detail || e?.message || "Failed to save." });
    } finally {
      setSaving(false);
    }
  };

  const checkRollback = async () => {
    try {
      const result = await apiPost<RollbackResult>("/api/v1/safety/rollback-check", {});
      setRollback(result);
    } catch (err) {
      setBanner({ type: "error", message: err instanceof Error ? err.message : "Rollback check failed" });
    }
  };

  const tabs = [
    { key: "automation" as const, label: "Automation" },
    { key: "controls" as const, label: "Exploration Controls" },
    { key: "monitoring" as const, label: "Monitoring" },
  ];

  if (loading) {
    return (
      <div className="min-h-screen bg-[var(--bg-primary)]">
        <Header title="Settings" />
        <div className="mx-auto max-w-4xl px-6 py-12">
          <LoadingOverlay text="Loading settings..." />
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[var(--bg-primary)]">
      <Header title="Settings" />
      <div className="mx-auto max-w-4xl px-6 py-8">
        {/* Banner */}
        {banner && (
          <div
            className={cn(
              "mb-6 rounded-lg px-4 py-3 text-sm",
              banner.type === "success"
                ? "bg-green-50 text-green-600 border border-green-500/30"
                : "bg-red-50 text-red-600 border border-red-500/30"
            )}
          >
            {banner.message}
          </div>
        )}

        {/* Tabs */}
        <div className="flex gap-2 border-b border-[var(--border-color)]">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={cn(
                "px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px",
                tab === t.key
                  ? "border-[var(--brand-gold)] text-[var(--brand-gold)]"
                  : "border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
              )}
            >
              {t.label}
            </button>
          ))}
        </div>

        {tab === "automation" && tenant && (
          <AutomationTab
            tenant={tenant}
            saving={saving}
            onSave={saveAutomation}
          />
        )}

        {tab === "controls" && controls && (
          <ControlsTab
            controls={controls}
            saving={saving}
            onSave={saveControls}
          />
        )}

        {tab === "monitoring" && (
          <MonitoringTab
            stats={stats}
            anomalies={anomalies}
            rollback={rollback}
            onCheckRollback={checkRollback}
          />
        )}
      </div>
    </div>
  );
}

// ── Automation tab ─────────────────────────────────────────────

function AutomationTab({
  tenant,
  saving,
  onSave,
}: {
  tenant: TenantProfile;
  saving: boolean;
  onSave: (updates: Partial<TenantProfile>) => void;
}) {
  const [pendingLevel, setPendingLevel] = useState<TenantProfile["automation_level"] | null>(null);

  const currentIdx = AUTOMATION_LEVELS.findIndex((l) => l.key === tenant.automation_level);
  const handleSelect = (key: TenantProfile["automation_level"]) => {
    if (key === tenant.automation_level) return;
    setPendingLevel(key);
  };

  const confirmChange = () => {
    if (pendingLevel) {
      onSave({ automation_level: pendingLevel });
      setPendingLevel(null);
    }
  };

  const togglePause = () => {
    onSave({ automation_paused: !tenant.automation_paused });
  };

  return (
    <div className="mt-8 space-y-6">
      {/* Pause banner — always visible if paused */}
      {tenant.automation_paused && (
        <div className="rounded-xl border border-red-300 bg-red-50 p-4 flex items-center justify-between">
          <div>
            <p className="text-sm font-semibold text-red-700">⏸ Agent Paused</p>
            <p className="text-xs text-red-600 mt-0.5">
              The autonomy worker is not touching this tenant. Drafts can still be approved manually.
            </p>
          </div>
          <button
            onClick={togglePause}
            disabled={saving}
            className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
          >
            Resume Agent
          </button>
        </div>
      )}

      {/* Pause button (when not paused) */}
      {!tenant.automation_paused && tenant.automation_level !== "manual" && (
        <div className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-4 flex items-center justify-between">
          <div>
            <p className="text-sm font-semibold text-[var(--text-primary)]">Kill switch</p>
            <p className="text-xs text-[var(--text-secondary)] mt-0.5">
              Instantly stop the autonomy worker from touching this tenant. Resume any time.
            </p>
          </div>
          <button
            onClick={togglePause}
            disabled={saving}
            className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
          >
            ⏸ Pause Agent
          </button>
        </div>
      )}

      {/* Ladder header */}
      <div>
        <h3 className="text-sm font-semibold text-[var(--text-primary)]">Automation Level</h3>
        <p className="text-xs text-[var(--text-secondary)] mt-1">
          Each rung removes one user click from the campaign loop. Start manual, graduate as you trust the agent.
        </p>
      </div>

      {/* Ladder cards */}
      <div className="space-y-3">
        {AUTOMATION_LEVELS.map((lvl, idx) => {
          const isCurrent = lvl.key === tenant.automation_level;
          const isBelow = idx < currentIdx;
          return (
            <button
              key={lvl.key}
              onClick={() => handleSelect(lvl.key)}
              disabled={saving}
              className={cn(
                "w-full text-left rounded-xl border-2 p-4 transition-all",
                isCurrent
                  ? "border-indigo-500 bg-indigo-50 shadow-sm"
                  : isBelow
                  ? "border-[var(--border-color)] bg-[var(--bg-surface)] opacity-60 hover:opacity-100"
                  : "border-[var(--border-color)] bg-[var(--bg-surface)] hover:border-indigo-300",
                saving && "cursor-not-allowed opacity-50"
              )}
            >
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-3">
                  <div
                    className={cn(
                      "w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold",
                      isCurrent
                        ? "bg-indigo-500 text-white"
                        : "bg-[var(--bg-primary)] text-[var(--text-secondary)] border border-[var(--border-color)]"
                    )}
                  >
                    {idx + 1}
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-[var(--text-primary)]">{lvl.title}</p>
                    <p className="text-xs text-[var(--text-secondary)]">{lvl.tagline}</p>
                  </div>
                </div>
                {isCurrent && (
                  <span className="text-[10px] font-medium uppercase tracking-wider bg-indigo-500 text-white px-2 py-0.5 rounded-full">
                    Current
                  </span>
                )}
              </div>
              <ul className="ml-10 space-y-0.5">
                {lvl.details.map((d) => (
                  <li key={d} className="text-xs text-[var(--text-secondary)] flex gap-2">
                    <span className="text-indigo-400">•</span>
                    <span>{d}</span>
                  </li>
                ))}
              </ul>
            </button>
          );
        })}
      </div>

      {/* Confirmation modal */}
      {pendingLevel && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="bg-white rounded-xl border border-[var(--border-color)] max-w-md w-full p-6 shadow-xl">
            <h3 className="text-base font-semibold text-[var(--text-primary)]">
              Switch to {AUTOMATION_LEVELS.find((l) => l.key === pendingLevel)?.title}?
            </h3>
            <p className="text-sm text-[var(--text-secondary)] mt-2">
              {AUTOMATION_LEVELS.find((l) => l.key === pendingLevel)?.tagline}
            </p>
            <ul className="mt-4 space-y-1">
              {AUTOMATION_LEVELS.find((l) => l.key === pendingLevel)?.details.map((d) => (
                <li key={d} className="text-xs text-[var(--text-secondary)] flex gap-2">
                  <span className="text-indigo-400">•</span>
                  <span>{d}</span>
                </li>
              ))}
            </ul>
            <div className="mt-5 rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-800">
              You can switch back to a lower level (or pause) at any time. The agent never publishes anything that
              would violate budget caps, post-volume limits, or content policy.
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={() => setPendingLevel(null)}
                className="rounded-lg border border-[var(--border-color)] px-4 py-2 text-sm font-medium text-[var(--text-primary)] hover:bg-[var(--bg-surface-hover)]"
              >
                Cancel
              </button>
              <button
                onClick={confirmChange}
                disabled={saving}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {saving ? "Saving..." : "Confirm"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Controls tab ───────────────────────────────────────────────

function ControlsTab({
  controls,
  saving,
  onSave,
}: {
  controls: ExplorationControls;
  saving: boolean;
  onSave: (updates: Partial<ExplorationControls>) => void;
}) {
  const [draft, setDraft] = useState(controls);

  useEffect(() => {
    setDraft(controls);
  }, [controls]);

  const update = (field: keyof ExplorationControls, value: any) => {
    setDraft((prev) => ({ ...prev, [field]: value }));
  };

  const handleSave = () => {
    const changes: Record<string, any> = {};
    for (const key of Object.keys(draft) as (keyof ExplorationControls)[]) {
      if (JSON.stringify(draft[key]) !== JSON.stringify(controls[key])) {
        changes[key] = draft[key];
      }
    }
    if (Object.keys(changes).length > 0) {
      onSave(changes);
    }
  };

  return (
    <div className="mt-8 space-y-6">
      {/* Master switch */}
      <Card title="Learning System">
        <ToggleRow
          label="Enable exploration"
          description="Allow the learning system to experiment with post selection"
          checked={draft.enabled}
          onChange={(v) => update("enabled", v)}
        />
        <ToggleRow
          label="Shadow-only mode"
          description="Learning runs in parallel but never overrides rule-based selection"
          checked={draft.shadow_only}
          onChange={(v) => update("shadow_only", v)}
        />
        <ToggleRow
          label="Require approval for experiments"
          description="Block experiments until an operator approves"
          checked={draft.require_approval_for_experiments}
          onChange={(v) => update("require_approval_for_experiments", v)}
        />
      </Card>

      {/* Rate controls */}
      <Card title="Exploration Bounds">
        <SliderRow
          label="Max exploration rate"
          description="Upper bound on the fraction of exploratory decisions"
          value={draft.max_exploration_rate}
          min={0}
          max={0.5}
          step={0.01}
          format={(v) => `${(v * 100).toFixed(0)}%`}
          onChange={(v) => update("max_exploration_rate", v)}
        />
        <SliderRow
          label="Confidence threshold"
          description="Minimum rank correlation before the bandit activates"
          value={draft.confidence_threshold}
          min={0}
          max={1}
          step={0.05}
          format={(v) => v.toFixed(2)}
          onChange={(v) => update("confidence_threshold", v)}
        />
        <NumberRow
          label="Minimum sample size"
          description="Observations needed before exploration begins"
          value={draft.min_sample_size}
          min={0}
          onChange={(v) => update("min_sample_size", v)}
        />
      </Card>

      {/* Budget */}
      <Card title="Experiment Budget">
        <NumberRow
          label="Max experiments per week"
          description="Hard cap on distinct exploratory actions per week"
          value={draft.max_experiments_per_week}
          min={0}
          onChange={(v) => update("max_experiments_per_week", v)}
        />
        <div className="mt-4">
          <p className="text-sm font-medium text-[var(--text-primary)] mb-2">Channel limits (per week)</p>
          {Object.entries(draft.channel_limits).map(([platform, limit]) => (
            <div key={platform} className="flex items-center gap-3 mb-2">
              <span className="w-24 text-sm text-[var(--text-secondary)] capitalize">{platform}</span>
              <input
                type="number"
                min={0}
                value={limit}
                onChange={(e) => {
                  const newLimits = { ...draft.channel_limits, [platform]: parseInt(e.target.value) || 0 };
                  update("channel_limits", newLimits);
                }}
                className="w-20 rounded-md border border-[var(--border-color)] bg-[var(--bg-primary)] px-2 py-1 text-sm text-[var(--text-primary)]"
              />
            </div>
          ))}
        </div>
      </Card>

      {/* Quiet hours */}
      <Card title="Quiet Hours">
        <p className="text-sm text-[var(--text-secondary)] mb-3">
          No exploration during these hours (UTC). Leave empty to disable.
        </p>
        <div className="flex items-center gap-3">
          <label className="text-sm text-[var(--text-secondary)]">Start</label>
          <input
            type="number"
            min={0}
            max={23}
            value={draft.quiet_hours_start ?? ""}
            placeholder="--"
            onChange={(e) => update("quiet_hours_start", e.target.value === "" ? null : parseInt(e.target.value))}
            className="w-16 rounded-md border border-[var(--border-color)] bg-[var(--bg-primary)] px-2 py-1 text-sm text-[var(--text-primary)]"
          />
          <label className="text-sm text-[var(--text-secondary)]">End</label>
          <input
            type="number"
            min={0}
            max={23}
            value={draft.quiet_hours_end ?? ""}
            placeholder="--"
            onChange={(e) => update("quiet_hours_end", e.target.value === "" ? null : parseInt(e.target.value))}
            className="w-16 rounded-md border border-[var(--border-color)] bg-[var(--bg-primary)] px-2 py-1 text-sm text-[var(--text-primary)]"
          />
          <span className="text-xs text-[var(--text-secondary)]">UTC</span>
        </div>
      </Card>

      {/* Rollback */}
      <Card title="Auto-Rollback">
        <SliderRow
          label="Rollback threshold"
          description="Auto-rollback if mean reward drops by this fraction vs baseline"
          value={draft.rollback_threshold}
          min={0.05}
          max={0.5}
          step={0.01}
          format={(v) => `${(v * 100).toFixed(0)}%`}
          onChange={(v) => update("rollback_threshold", v)}
        />
        <NumberRow
          label="Baseline window"
          description="Number of recent non-exploratory observations for the baseline"
          value={draft.baseline_window}
          min={1}
          onChange={(v) => update("baseline_window", v)}
        />
      </Card>

      {/* Save */}
      <div className="flex justify-end">
        <button
          onClick={handleSave}
          disabled={saving}
          className={cn(
            "rounded-lg px-6 py-2.5 text-sm font-medium transition-colors",
            "bg-[var(--brand-gold)] text-white hover:bg-[var(--brand-gold)]/90",
            saving && "opacity-50 cursor-not-allowed"
          )}
        >
          {saving ? <ButtonSpinner label="Saving..." /> : "Save Changes"}
        </button>
      </div>
    </div>
  );
}

// ── Monitoring tab ─────────────────────────────────────────────

function MonitoringTab({
  stats,
  anomalies,
  rollback,
  onCheckRollback,
}: {
  stats: ExperimentStats | null;
  anomalies: AlertItem[];
  rollback: RollbackResult | null;
  onCheckRollback: () => void;
}) {
  return (
    <div className="mt-8 space-y-6">
      {/* Usage stats */}
      {stats && (
        <Card title="Experiment Usage">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <StatBox label="This week" value={stats.experiments_this_week} />
            <StatBox label="Budget left" value={stats.budget_remaining} />
            <StatBox label="Total observations" value={stats.total_observations} />
            <StatBox
              label="Budget used"
              value={
                stats.controls.max_experiments_per_week > 0
                  ? `${Math.round((stats.experiments_this_week / stats.controls.max_experiments_per_week) * 100)}%`
                  : "N/A"
              }
            />
          </div>
          {Object.keys(stats.platform_usage).length > 0 && (
            <div className="mt-4">
              <p className="text-xs font-medium text-[var(--text-secondary)] mb-2 uppercase tracking-wider">
                Platform usage
              </p>
              <div className="flex gap-4">
                {Object.entries(stats.platform_usage).map(([platform, count]) => (
                  <div key={platform} className="flex items-center gap-2">
                    <span className="text-sm capitalize text-[var(--text-secondary)]">{platform}</span>
                    <span className="text-sm font-medium text-[var(--text-primary)]">
                      {count}/{stats.controls.channel_limits[platform] ?? "∞"}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </Card>
      )}

      {/* Anomalies */}
      <Card title="Alerts">
        {anomalies.length === 0 ? (
          <p className="text-sm text-[var(--text-secondary)]">No anomalies detected.</p>
        ) : (
          <div className="space-y-2">
            {anomalies.map((a, i) => (
              <div
                key={i}
                className={cn(
                  "rounded-lg px-3 py-2 text-sm border",
                  a.severity === "critical"
                    ? "bg-red-50 text-red-600 border-red-500/30"
                    : a.severity === "warning"
                    ? "bg-amber-500/10 text-amber-400 border-amber-500/30"
                    : "bg-blue-500/10 text-blue-600 border-blue-500/30"
                )}
              >
                <span className="font-medium uppercase text-xs mr-2">{a.severity}</span>
                {a.message}
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Rollback check */}
      <Card title="Rollback Check">
        <p className="text-sm text-[var(--text-secondary)] mb-3">
          Evaluate whether current performance warrants an automatic rollback.
        </p>
        <button
          onClick={onCheckRollback}
          className="rounded-lg px-4 py-2 text-sm font-medium border border-[var(--border-color)] text-[var(--text-primary)] hover:bg-[var(--bg-surface-hover)] transition-colors"
        >
          Run Rollback Check
        </button>
        {rollback && (
          <div className="mt-4 rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] p-4 text-sm space-y-1">
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  "inline-block w-2.5 h-2.5 rounded-full",
                  rollback.should_rollback ? "bg-red-500" : "bg-green-500"
                )}
              />
              <span className="font-medium text-[var(--text-primary)]">
                {rollback.should_rollback ? "Rollback recommended" : "Performance OK"}
              </span>
            </div>
            <p className="text-[var(--text-secondary)]">{rollback.reason}</p>
            {rollback.baseline_reward != null && (
              <p className="text-[var(--text-secondary)]">
                Baseline: {rollback.baseline_reward.toFixed(4)} | Current: {rollback.current_reward?.toFixed(4)} | Drop: {rollback.drop_fraction != null ? `${(rollback.drop_fraction * 100).toFixed(1)}%` : "N/A"}
              </p>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}

// ── Shared components ──────────────────────────────────────────

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-5">
      <h3 className="text-sm font-semibold text-[var(--text-primary)] mb-4">{title}</h3>
      {children}
    </div>
  );
}

function ToggleRow({
  label,
  description,
  checked,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between py-2">
      <div>
        <p className="text-sm font-medium text-[var(--text-primary)]">{label}</p>
        <p className="text-xs text-[var(--text-secondary)]">{description}</p>
      </div>
      <button
        onClick={() => onChange(!checked)}
        className={cn(
          "relative inline-flex h-6 w-11 items-center rounded-full transition-colors",
          checked ? "bg-[var(--brand-gold)]" : "bg-gray-300"
        )}
      >
        <span
          className={cn(
            "inline-block h-4 w-4 rounded-full bg-white transition-transform",
            checked ? "translate-x-6" : "translate-x-1"
          )}
        />
      </button>
    </div>
  );
}

function SliderRow({
  label,
  description,
  value,
  min,
  max,
  step,
  format,
  onChange,
}: {
  label: string;
  description: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format: (v: number) => string;
  onChange: (v: number) => void;
}) {
  return (
    <div className="py-2">
      <div className="flex items-center justify-between mb-1">
        <div>
          <p className="text-sm font-medium text-[var(--text-primary)]">{label}</p>
          <p className="text-xs text-[var(--text-secondary)]">{description}</p>
        </div>
        <span className="text-sm font-mono text-[var(--brand-gold)]">{format(value)}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="w-full accent-[var(--brand-gold)]"
      />
    </div>
  );
}

function NumberRow({
  label,
  description,
  value,
  min,
  onChange,
}: {
  label: string;
  description: string;
  value: number;
  min: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex items-center justify-between py-2">
      <div>
        <p className="text-sm font-medium text-[var(--text-primary)]">{label}</p>
        <p className="text-xs text-[var(--text-secondary)]">{description}</p>
      </div>
      <input
        type="number"
        min={min}
        value={value}
        onChange={(e) => onChange(parseInt(e.target.value) || 0)}
        className="w-24 rounded-md border border-[var(--border-color)] bg-[var(--bg-primary)] px-2 py-1 text-sm text-[var(--text-primary)] text-right"
      />
    </div>
  );
}

function StatBox({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] p-3 text-center">
      <p className="text-2xl font-bold text-[var(--text-primary)]">{value}</p>
      <p className="text-xs text-[var(--text-secondary)] mt-1">{label}</p>
    </div>
  );
}
