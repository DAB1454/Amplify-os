"use client";

import { useState, useEffect, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { apiGet } from "@/lib/api";
import { cn, formatLocal, formatLocalDate } from "@/lib/utils";

// ── Types ──────────────────────────────────────────────────────

interface Overview {
  active_patterns: number;
  feature_vectors: number;
  outcomes: number;
  learning_events: number;
  evaluation_runs: number;
  global_priors_available: number;
  low_confidence_patterns: number;
}

interface TenantPattern {
  id: string;
  pattern_type: string;
  subject_feature: string | null;
  subject_value: string | null;
  title: string;
  explanation: string;
  confidence: number;
  direction: string;
  supporting_observation_count: number;
  decay_weighted_reward: number | null;
  evidence: Record<string, unknown> | null;
  is_pinned: boolean;
  source: string;
  version: number;
}

interface GlobalPrior {
  id: string;
  feature_name: string;
  feature_value: string;
  category: string;
  mean_reward: number;
  std_dev: number | null;
  confidence_lower: number | null;
  confidence_upper: number | null;
  observation_count: number;
  tenant_count: number;
}

interface RewardPoint {
  date: string;
  avg_reward: number;
  count: number;
  min_reward: number;
  max_reward: number;
}

interface EvalRun {
  id: string;
  evaluation_type: string;
  sample_size: number;
  mae: number | null;
  rank_correlation: number | null;
  top_k_precision: number | null;
  status: string;
  created_at: string | null;
}

interface Alert {
  type: string;
  severity: string;
  entity_type: string;
  entity_id: string;
  message: string;
}

interface AuditEntry {
  id: string;
  action: string;
  entity_type: string;
  entity_id: string | null;
  details: Record<string, unknown>;
  explanation: string | null;
  created_at: string | null;
}

// ── Tabs ───────────────────────────────────────────────────────

const tabs = [
  { key: "overview", label: "Overview" },
  { key: "patterns", label: "Patterns" },
  { key: "priors", label: "Global Priors" },
  { key: "rewards", label: "Reward Trends" },
  { key: "evaluations", label: "Evaluations" },
  { key: "alerts", label: "Alerts" },
  { key: "audit", label: "Audit Log" },
] as const;

type TabKey = (typeof tabs)[number]["key"];

// ── Helpers ────────────────────────────────────────────────────

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = pct >= 70 ? "#22c55e" : pct >= 40 ? "#eab308" : "#ef4444";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-20 rounded-full bg-[var(--bg-primary)]">
        <div
          className="h-1.5 rounded-full transition-all"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      <span className="text-[10px] font-mono text-[var(--text-secondary)]">{pct}%</span>
    </div>
  );
}

function SeverityBadge({ severity }: { severity: string }) {
  const styles: Record<string, string> = {
    error: "bg-red-100 text-red-600",
    warning: "bg-yellow-100 text-yellow-600",
    info: "bg-blue-100 text-blue-600",
  };
  return (
    <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${styles[severity] || styles.info}`}>
      {severity}
    </span>
  );
}

function SourceBadge({ source }: { source: string }) {
  const styles: Record<string, { label: string; cls: string }> = {
    tenant: { label: "Tenant", cls: "bg-green-100 text-green-600" },
    global_prior: { label: "Global", cls: "bg-blue-100 text-blue-600" },
    cohort_prior: { label: "Cohort", cls: "bg-purple-100 text-purple-600" },
  };
  const s = styles[source] || { label: source, cls: "bg-gray-100 text-gray-500" };
  return (
    <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${s.cls}`}>
      {s.label}
    </span>
  );
}

function DirectionBadge({ direction }: { direction: string }) {
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
        direction === "winning"
          ? "bg-green-100 text-green-600"
          : "bg-red-100 text-red-600"
      }`}
    >
      {direction}
    </span>
  );
}

function MiniSparkline({ points }: { points: RewardPoint[] }) {
  if (points.length < 2) return null;
  const values = points.map((p) => p.avg_reward);
  const max = Math.max(...values);
  const min = Math.min(...values);
  const range = max - min || 0.01;
  const h = 48;
  const w = 280;
  const step = w / Math.max(points.length - 1, 1);

  const pathParts = values.map((v, i) => {
    const x = i * step;
    const y = h - ((v - min) / range) * h;
    return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
  });

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-12" preserveAspectRatio="none">
      <path d={pathParts.join(" ")} fill="none" stroke="#c9a84c" strokeWidth="2" />
    </svg>
  );
}

// ── Stat card ──────────────────────────────────────────────────

function StatCard({ label, value, alert }: { label: string; value: number | string; alert?: boolean }) {
  return (
    <div className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-5">
      <p className="text-xs text-[var(--text-secondary)]">{label}</p>
      <p className={cn("mt-1 text-2xl font-bold", alert ? "text-red-600" : "text-[var(--brand-gold)]")}>
        {value}
      </p>
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────

export default function IntelligencePage() {
  const [tab, setTab] = useState<TabKey>("overview");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Data
  const [overview, setOverview] = useState<Overview | null>(null);
  const [patterns, setPatterns] = useState<TenantPattern[]>([]);
  const [patternsTotal, setPatternsTotal] = useState(0);
  const [priors, setPriors] = useState<GlobalPrior[]>([]);
  const [priorsTotal, setPriorsTotal] = useState(0);
  const [rewardTrend, setRewardTrend] = useState<RewardPoint[]>([]);
  const [evalRuns, setEvalRuns] = useState<EvalRun[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([]);

  // Filters
  const [patternType, setPatternType] = useState("");
  const [priorCategory, setPriorCategory] = useState("");
  const [rewardDays, setRewardDays] = useState(30);
  const [auditDays, setAuditDays] = useState(30);
  const [suspiciousOnly, setSuspiciousOnly] = useState(false);

  const fetchTab = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      switch (tab) {
        case "overview": {
          const data = await apiGet<Overview>("/api/v1/intelligence/overview");
          setOverview(data);
          break;
        }
        case "patterns": {
          const qp = patternType ? `?pattern_type=${patternType}` : "";
          const data = await apiGet<{ patterns: TenantPattern[]; total: number }>(
            `/api/v1/intelligence/patterns${qp}`
          );
          setPatterns(data.patterns);
          setPatternsTotal(data.total);
          break;
        }
        case "priors": {
          const qp = priorCategory ? `?category=${priorCategory}` : "";
          const data = await apiGet<{ priors: GlobalPrior[]; total: number }>(
            `/api/v1/intelligence/global-priors${qp}`
          );
          setPriors(data.priors);
          setPriorsTotal(data.total);
          break;
        }
        case "rewards": {
          const data = await apiGet<{ trend: RewardPoint[] }>(
            `/api/v1/intelligence/reward-trends?days=${rewardDays}`
          );
          setRewardTrend(data.trend);
          break;
        }
        case "evaluations": {
          const data = await apiGet<{ runs: EvalRun[] }>("/api/v1/intelligence/evaluations");
          setEvalRuns(data.runs);
          break;
        }
        case "alerts": {
          const data = await apiGet<{ alerts: Alert[] }>("/api/v1/intelligence/alerts");
          setAlerts(data.alerts);
          break;
        }
        case "audit": {
          const qs = new URLSearchParams({ days: String(auditDays) });
          if (suspiciousOnly) qs.set("suspicious_only", "true");
          const data = await apiGet<{ entries: AuditEntry[] }>(
            `/api/v1/intelligence/audit?${qs}`
          );
          setAuditEntries(data.entries);
          break;
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load intelligence data");
    } finally {
      setLoading(false);
    }
  }, [tab, patternType, priorCategory, rewardDays, auditDays, suspiciousOnly]);

  useEffect(() => {
    fetchTab();
  }, [fetchTab]);

  return (
    <>
      <Header title="Intelligence" />

      {/* Tab bar */}
      <div className="mt-6 flex gap-1 overflow-x-auto border-b border-[var(--border-color)]">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={cn(
              "shrink-0 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px",
              tab === t.key
                ? "border-[var(--brand-gold)] text-[var(--brand-gold)]"
                : "border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-50 px-4 py-3 text-sm text-red-600 break-words overflow-hidden">
          {error}
        </div>
      )}

      {loading ? (
        <div className="mt-8 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
          Loading...
        </div>
      ) : (
        <div className="mt-6">
          {/* ── Overview ──────────────────────────────────── */}
          {tab === "overview" && overview && (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
              <StatCard label="Active Patterns" value={overview.active_patterns} />
              <StatCard label="Feature Vectors" value={overview.feature_vectors} />
              <StatCard label="Outcomes" value={overview.outcomes} />
              <StatCard label="Learning Events" value={overview.learning_events} />
              <StatCard label="Evaluation Runs" value={overview.evaluation_runs} />
              <StatCard label="Global Priors" value={overview.global_priors_available} />
              <StatCard
                label="Low Confidence"
                value={overview.low_confidence_patterns}
                alert={overview.low_confidence_patterns > 0}
              />
            </div>
          )}

          {/* ── Patterns ─────────────────────────────────── */}
          {tab === "patterns" && (
            <>
              <div className="mb-4 flex items-center gap-3">
                <select
                  value={patternType}
                  onChange={(e) => setPatternType(e.target.value)}
                  className="rounded-lg border border-[var(--border-color)] bg-[var(--bg-surface)] px-3 py-2 text-sm text-[var(--text-primary)]"
                >
                  <option value="">All types</option>
                  <option value="timing">Timing</option>
                  <option value="content">Content</option>
                  <option value="platform">Platform</option>
                  <option value="cta">CTA</option>
                  <option value="hook">Hook</option>
                  <option value="format">Format</option>
                </select>
                <span className="text-xs text-[var(--text-secondary)]">
                  {patternsTotal} total patterns
                </span>
              </div>
              <div className="space-y-2">
                {patterns.length === 0 ? (
                  <div className="rounded-xl border border-dashed border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
                    No patterns found. Publish more posts to build learning signals.
                  </div>
                ) : (
                  patterns.map((p) => (
                    <div
                      key={p.id}
                      className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] px-5 py-4"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <p className="text-sm font-medium text-[var(--text-primary)]">{p.title}</p>
                            <DirectionBadge direction={p.direction} />
                            <SourceBadge source={p.source} />
                            {p.is_pinned && (
                              <span className="rounded-full bg-[var(--brand-gold)]/20 px-2 py-0.5 text-[10px] font-medium text-[var(--brand-gold)]">
                                Pinned
                              </span>
                            )}
                          </div>
                          <p className="mt-1 text-xs text-[var(--text-secondary)]">{p.explanation}</p>
                        </div>
                        <div className="shrink-0 text-right">
                          <ConfidenceBar value={p.confidence} />
                          <p className="mt-1 text-[10px] text-[var(--text-secondary)]">
                            {p.supporting_observation_count} observations &middot; v{p.version}
                          </p>
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </>
          )}

          {/* ── Global Priors ────────────────────────────── */}
          {tab === "priors" && (
            <>
              <div className="mb-4 flex items-center gap-3">
                <select
                  value={priorCategory}
                  onChange={(e) => setPriorCategory(e.target.value)}
                  className="rounded-lg border border-[var(--border-color)] bg-[var(--bg-surface)] px-3 py-2 text-sm text-[var(--text-primary)]"
                >
                  <option value="">All categories</option>
                  <option value="posting_window">Posting Window</option>
                  <option value="cta_type">CTA Type</option>
                  <option value="format_preference">Format Preference</option>
                  <option value="hook_family">Hook Family</option>
                </select>
                <span className="text-xs text-[var(--text-secondary)]">
                  {priorsTotal} global priors
                </span>
              </div>
              <div className="space-y-2">
                {priors.length === 0 ? (
                  <div className="rounded-xl border border-dashed border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
                    No global priors available yet.
                  </div>
                ) : (
                  priors.map((p) => (
                    <div
                      key={p.id}
                      className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] px-5 py-4"
                    >
                      <div className="flex items-center justify-between">
                        <div>
                          <div className="flex items-center gap-2">
                            <p className="text-sm font-medium text-[var(--text-primary)]">
                              {p.feature_name} = {p.feature_value}
                            </p>
                            <span className="rounded-full bg-blue-100 px-2 py-0.5 text-[10px] font-medium text-blue-600">
                              {p.category || "uncategorized"}
                            </span>
                          </div>
                          <p className="mt-1 text-xs text-[var(--text-secondary)]">
                            Mean reward: {p.mean_reward.toFixed(4)}
                            {p.confidence_lower != null && p.confidence_upper != null && (
                              <> &middot; CI [{p.confidence_lower.toFixed(3)}, {p.confidence_upper.toFixed(3)}]</>
                            )}
                          </p>
                        </div>
                        <div className="text-right">
                          <p className="text-sm font-mono font-bold text-[var(--brand-gold)]">
                            {p.observation_count}
                          </p>
                          <p className="text-[10px] text-[var(--text-secondary)]">
                            observations &middot; {p.tenant_count} tenants
                          </p>
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </>
          )}

          {/* ── Reward Trends ────────────────────────────── */}
          {tab === "rewards" && (
            <>
              <div className="mb-4 flex items-center gap-3">
                {[7, 14, 30, 90].map((d) => (
                  <button
                    key={d}
                    onClick={() => setRewardDays(d)}
                    className={cn(
                      "rounded-lg px-3 py-1.5 text-xs font-medium transition-colors",
                      rewardDays === d
                        ? "bg-[var(--brand-gold)] text-white"
                        : "bg-[var(--bg-surface)] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)]"
                    )}
                  >
                    {d}d
                  </button>
                ))}
              </div>
              {rewardTrend.length > 0 ? (
                <div className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-5">
                  <MiniSparkline points={rewardTrend} />
                  <div className="mt-3 flex justify-between text-[10px] text-[var(--text-secondary)]">
                    <span>{rewardTrend[0]?.date}</span>
                    <span>{rewardTrend[rewardTrend.length - 1]?.date}</span>
                  </div>
                  <div className="mt-4 space-y-1">
                    {rewardTrend.slice(-7).reverse().map((p) => (
                      <div
                        key={p.date}
                        className="flex items-center justify-between text-xs"
                      >
                        <span className="text-[var(--text-secondary)]">{p.date}</span>
                        <span className="font-mono text-[var(--text-primary)]">
                          avg {p.avg_reward.toFixed(4)}
                        </span>
                        <span className="text-[var(--text-secondary)]">
                          [{p.min_reward.toFixed(3)} — {p.max_reward.toFixed(3)}]
                        </span>
                        <span className="text-[var(--text-secondary)]">{p.count} posts</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
                  No reward data in this period.
                </div>
              )}
            </>
          )}

          {/* ── Evaluations ──────────────────────────────── */}
          {tab === "evaluations" && (
            <div className="space-y-2">
              {evalRuns.length === 0 ? (
                <div className="rounded-xl border border-dashed border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
                  No evaluation runs yet.
                </div>
              ) : (
                evalRuns.map((r) => (
                  <div
                    key={r.id}
                    className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] px-5 py-4"
                  >
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-[var(--text-primary)]">
                          {r.evaluation_type} evaluation
                        </p>
                        <p className="text-xs text-[var(--text-secondary)]">
                          {r.sample_size} samples &middot; {r.created_at ? formatLocalDate(r.created_at) : "—"}
                        </p>
                      </div>
                      <div className="flex items-center gap-4 text-right">
                        {r.mae != null && (
                          <div>
                            <p className="text-xs text-[var(--text-secondary)]">MAE</p>
                            <p className="text-sm font-mono font-bold text-[var(--text-primary)]">
                              {r.mae.toFixed(4)}
                            </p>
                          </div>
                        )}
                        {r.rank_correlation != null && (
                          <div>
                            <p className="text-xs text-[var(--text-secondary)]">Rank Corr</p>
                            <p className="text-sm font-mono font-bold text-[var(--text-primary)]">
                              {r.rank_correlation.toFixed(4)}
                            </p>
                          </div>
                        )}
                        {r.top_k_precision != null && (
                          <div>
                            <p className="text-xs text-[var(--text-secondary)]">Top-K</p>
                            <p className="text-sm font-mono font-bold text-[var(--text-primary)]">
                              {(r.top_k_precision * 100).toFixed(1)}%
                            </p>
                          </div>
                        )}
                        <span
                          className={cn(
                            "rounded-full px-2 py-0.5 text-[10px] font-medium",
                            r.status === "completed" ? "bg-green-100 text-green-600" : "bg-gray-100 text-gray-500"
                          )}
                        >
                          {r.status}
                        </span>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* ── Alerts ───────────────────────────────────── */}
          {tab === "alerts" && (
            <div className="space-y-2">
              {alerts.length === 0 ? (
                <div className="rounded-xl border border-dashed border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
                  No alerts — intelligence system looks healthy.
                </div>
              ) : (
                alerts.map((a, i) => (
                  <div
                    key={`${a.entity_id}-${i}`}
                    className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] px-5 py-4"
                  >
                    <div className="flex items-start gap-3">
                      <SeverityBadge severity={a.severity} />
                      <div className="min-w-0 flex-1">
                        <p className="text-sm text-[var(--text-primary)]">{a.message}</p>
                        <p className="mt-1 text-[10px] text-[var(--text-secondary)]">
                          {a.type} &middot; {a.entity_type}:{a.entity_id}
                        </p>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* ── Audit Log ────────────────────────────────── */}
          {tab === "audit" && (
            <>
              <div className="mb-4 flex items-center gap-3">
                <select
                  value={auditDays}
                  onChange={(e) => setAuditDays(Number(e.target.value))}
                  className="rounded-lg border border-[var(--border-color)] bg-[var(--bg-surface)] px-3 py-2 text-sm text-[var(--text-primary)]"
                >
                  <option value={7}>Last 7 days</option>
                  <option value={30}>Last 30 days</option>
                  <option value={90}>Last 90 days</option>
                </select>
                <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                  <input
                    type="checkbox"
                    checked={suspiciousOnly}
                    onChange={(e) => setSuspiciousOnly(e.target.checked)}
                    className="rounded"
                  />
                  Suspicious only
                </label>
              </div>
              <div className="space-y-1">
                {auditEntries.length === 0 ? (
                  <div className="rounded-xl border border-dashed border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
                    No audit entries in this period.
                  </div>
                ) : (
                  auditEntries.map((e) => (
                    <div
                      key={e.id}
                      className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] px-5 py-3"
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span className="rounded bg-[var(--bg-primary)] px-2 py-0.5 text-[10px] font-mono text-[var(--text-secondary)]">
                            {e.action}
                          </span>
                          <span className="text-xs text-[var(--text-secondary)]">
                            {e.entity_type}{e.entity_id ? `:${e.entity_id.slice(0, 8)}` : ""}
                          </span>
                        </div>
                        <span className="text-[10px] text-[var(--text-secondary)]">
                          {e.created_at ? formatLocal(e.created_at) : "—"}
                        </span>
                      </div>
                      {e.explanation && (
                        <p className="mt-1 text-xs text-[var(--text-secondary)]">{e.explanation}</p>
                      )}
                    </div>
                  ))
                )}
              </div>
            </>
          )}
        </div>
      )}
    </>
  );
}
