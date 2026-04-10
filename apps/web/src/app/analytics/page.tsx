"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { Header } from "@/components/layout/header";
import { apiGet } from "@/lib/api";
import { cn } from "@/lib/utils";
import { LoadingOverlay } from "@/components/ui/spinner";

// ── Types ──────────────────────────────────────────────────────

interface TimeseriesPoint {
  date: string;
  value: number;
}

interface TimeseriesData {
  series: Record<string, TimeseriesPoint[]>;
  source: string;
}

interface PostScore {
  post_id: string;
  platform: string;
  impressions: number;
  engagement_rate: number;
  click_through_rate: number;
  engagement_score: number;
  click_score: number;
  composite_score: number;
  caption?: string;
  permalink?: string;
  platform_post_id?: string;
}

interface Verdict {
  post_id: string;
  platform: string;
  composite_score: number;
  verdict: "keep" | "remix" | "stop";
  reason: string;
  caption?: string;
  permalink?: string;
}

interface AnalystReport {
  campaign_id: string;
  period_start: string;
  period_end: string;
  total_posts: number;
  keep_count: number;
  remix_count: number;
  stop_count: number;
  summary: string;
  verdicts: Verdict[];
}

interface Overview {
  total_campaigns: number;
  total_posts: number;
  published_posts: number;
}

// ── Helpers ────────────────────────────────────────────────────

const ranges = ["7d", "14d", "30d"] as const;
const dayMap: Record<string, number> = { "7d": 7, "14d": 14, "30d": 30 };

const verdictColors: Record<string, string> = {
  keep: "bg-green-100 text-green-700",
  remix: "bg-yellow-100 text-yellow-700",
  stop: "bg-red-100 text-red-700",
};

const platformColors: Record<string, string> = {
  instagram: "#E1306C",
  tiktok: "#00f2ea",
  youtube: "#FF0000",
  facebook: "#1877F2",
};

const platformIcons: Record<string, string> = {
  instagram: "IG",
  tiktok: "TT",
  youtube: "YT",
  facebook: "FB",
};

function MiniBar({ value, max, color }: { value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div className="h-2 w-full rounded-full bg-[var(--bg-primary)]">
      <div
        className="h-2 rounded-full transition-all"
        style={{ width: `${pct}%`, backgroundColor: color }}
      />
    </div>
  );
}

function SparkChart({ points, color }: { points: TimeseriesPoint[]; color: string }) {
  if (points.length === 0) return null;
  const max = Math.max(...points.map((p) => p.value));
  const min = Math.min(...points.map((p) => p.value));
  const range = max - min || 1;
  const h = 60;
  const w = 280;
  const step = w / Math.max(points.length - 1, 1);

  const pathParts = points.map((p, i) => {
    const x = i * step;
    const y = h - ((p.value - min) / range) * h;
    return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
  });

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-16" preserveAspectRatio="none">
      <path d={pathParts.join(" ")} fill="none" stroke={color} strokeWidth="2" />
    </svg>
  );
}

function PlatformBadge({ platform }: { platform: string }) {
  return (
    <span
      className="inline-flex items-center justify-center rounded-md px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-white shrink-0"
      style={{ backgroundColor: platformColors[platform] || "#888" }}
    >
      {platformIcons[platform] || platform.slice(0, 2).toUpperCase()}
    </span>
  );
}

function scoreColor(score: number) {
  if (score >= 70) return "#22c55e";
  if (score >= 40) return "#eab308";
  return "#ef4444";
}

// ── Page ───────────────────────────────────────────────────────

export default function AnalyticsPage() {
  const [range, setRange] = useState<(typeof ranges)[number]>("14d");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [timeseries, setTimeseries] = useState<TimeseriesData | null>(null);
  const [scores, setScores] = useState<PostScore[]>([]);
  const [report, setReport] = useState<AnalystReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [platformFilter, setPlatformFilter] = useState<string>("all");
  const [verdictFilter, setVerdictFilter] = useState<string>("all");

  const days = dayMap[range];

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [ov, ts, sc, rp] = await Promise.all([
        apiGet<Overview>("/api/v1/analytics/overview"),
        apiGet<TimeseriesData>(`/api/v1/analytics/timeseries?days=${days}`),
        apiGet<PostScore[]>(`/api/v1/analytics/scores?days=${days}`),
        apiGet<AnalystReport>(`/api/v1/analytics/analyst-report?days=${days}`),
      ]);
      setOverview(ov);
      setTimeseries(ts);
      setScores(sc);
      setReport(rp);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load analytics");
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  // Unique platforms present in data
  const platforms = useMemo(() => {
    const set = new Set(scores.map((s) => s.platform));
    return Array.from(set).sort();
  }, [scores]);

  const filteredScores = useMemo(() => {
    return scores
      .filter((s) => platformFilter === "all" || s.platform === platformFilter)
      .sort((a, b) => b.composite_score - a.composite_score);
  }, [scores, platformFilter]);

  const filteredVerdicts = useMemo(() => {
    if (!report) return [];
    return report.verdicts
      .filter((v) => platformFilter === "all" || v.platform === platformFilter)
      .filter((v) => verdictFilter === "all" || v.verdict === verdictFilter)
      .sort((a, b) => b.composite_score - a.composite_score);
  }, [report, platformFilter, verdictFilter]);

  const maxScore = Math.max(...scores.map((s) => s.composite_score), 1);

  return (
    <>
      <Header title="Analytics" />

      {/* Range selector + platform filter */}
      <div className="mt-8 flex flex-wrap items-center gap-4">
        <div className="flex gap-2">
          {ranges.map((r) => (
            <button
              key={r}
              onClick={() => setRange(r)}
              className={cn(
                "rounded-lg px-4 py-2 text-sm font-medium transition-colors",
                range === r
                  ? "bg-[var(--brand-gold)] text-white"
                  : "bg-[var(--bg-surface)] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)]"
              )}
            >
              {r}
            </button>
          ))}
        </div>

        {platforms.length > 1 && (
          <div className="flex gap-1.5 ml-auto">
            <button
              onClick={() => setPlatformFilter("all")}
              className={cn(
                "rounded-lg px-3 py-1.5 text-xs font-medium transition-colors",
                platformFilter === "all"
                  ? "bg-indigo-500 text-white"
                  : "bg-[var(--bg-surface)] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)]"
              )}
            >
              All
            </button>
            {platforms.map((p) => (
              <button
                key={p}
                onClick={() => setPlatformFilter(p === platformFilter ? "all" : p)}
                className={cn(
                  "rounded-lg px-3 py-1.5 text-xs font-medium transition-colors capitalize",
                  platformFilter === p
                    ? "text-white"
                    : "bg-[var(--bg-surface)] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)]"
                )}
                style={platformFilter === p ? { backgroundColor: platformColors[p] || "#888" } : {}}
              >
                {p}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Empty-state banner */}
      {!loading && timeseries?.source === "empty" && (
        <div className="mt-4 rounded-lg border border-[var(--border-color)] bg-[var(--bg-surface)] px-4 py-3 text-sm text-[var(--text-secondary)]">
          No analytics yet. Publish posts and wait for platform metrics
          to sync — charts and scores will populate automatically.
        </div>
      )}

      {error && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-50 px-4 py-3 text-sm text-red-600 break-words overflow-hidden">
          {error}
        </div>
      )}

      {loading ? (
        <div className="mt-6">
          <LoadingOverlay text="Loading analytics..." />
        </div>
      ) : (
        <>
          {/* Overview cards */}
          {overview && (
            <div className="mt-6 grid grid-cols-3 gap-4">
              {[
                { label: "Campaigns", value: overview.total_campaigns },
                { label: "Total Posts", value: overview.total_posts },
                { label: "Published", value: overview.published_posts },
              ].map((card) => (
                <div
                  key={card.label}
                  className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-5"
                >
                  <p className="text-xs text-[var(--text-secondary)]">{card.label}</p>
                  <p className="mt-1 text-2xl font-bold text-[var(--text-primary)]">{card.value}</p>
                </div>
              ))}
            </div>
          )}

          {/* Time-series charts */}
          {timeseries && Object.keys(timeseries.series).length > 0 && (
            <div className="mt-6 grid grid-cols-3 gap-4">
              {Object.entries(timeseries.series).map(([metric, points]) => (
                <div
                  key={metric}
                  className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-5"
                >
                  <p className="text-xs font-medium uppercase tracking-wider text-[var(--text-secondary)]">
                    {metric}
                  </p>
                  <p className="mt-1 text-lg font-bold text-[var(--text-primary)]">
                    {points.reduce((sum, p) => sum + p.value, 0).toLocaleString()}
                  </p>
                  <div className="mt-3">
                    <SparkChart
                      points={points}
                      color={
                        metric === "impressions"
                          ? "#c9a84c"
                          : metric === "engagement"
                            ? "#22c55e"
                            : "#3b82f6"
                      }
                    />
                  </div>
                  <div className="mt-1 flex justify-between text-[10px] text-[var(--text-secondary)]">
                    <span>{points[0]?.date}</span>
                    <span>{points[points.length - 1]?.date}</span>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Post scores */}
          {filteredScores.length > 0 && (
            <div className="mt-8">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-[var(--text-primary)]">
                  Post Scores
                </h3>
                <span className="text-xs text-[var(--text-secondary)]">
                  {filteredScores.length} post{filteredScores.length !== 1 ? "s" : ""}
                </span>
              </div>
              <div className="mt-3 space-y-2">
                {filteredScores.map((s) => {
                  const row = (
                    <div
                      key={s.post_id}
                      className={cn(
                        "flex items-center gap-3 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] px-4 py-3 transition-colors",
                        s.permalink && "hover:border-indigo-400 cursor-pointer"
                      )}
                      onClick={() => s.permalink && window.open(s.permalink, "_blank")}
                    >
                      {/* Platform badge */}
                      <PlatformBadge platform={s.platform} />

                      {/* Caption */}
                      <div className="flex-1 min-w-0">
                        <p className="text-sm text-[var(--text-primary)] truncate">
                          {s.caption || s.post_id}
                        </p>
                        <div className="mt-1.5">
                          <MiniBar value={s.composite_score} max={maxScore} color={scoreColor(s.composite_score)} />
                        </div>
                      </div>

                      {/* Score */}
                      <span
                        className="w-10 text-center text-sm font-mono font-bold rounded-md px-1.5 py-0.5"
                        style={{
                          color: scoreColor(s.composite_score),
                          backgroundColor: `${scoreColor(s.composite_score)}15`,
                        }}
                      >
                        {s.composite_score.toFixed(0)}
                      </span>

                      {/* Metrics */}
                      <div className="flex flex-col items-end gap-0.5 shrink-0">
                        <span className="text-[11px] text-[var(--text-secondary)]">
                          {s.impressions.toLocaleString()} views
                        </span>
                        <span className="text-[11px] text-[var(--text-secondary)]">
                          {(s.engagement_rate * 100).toFixed(1)}% eng
                        </span>
                      </div>

                      {/* External link icon */}
                      {s.permalink && (
                        <svg className="w-4 h-4 text-[var(--text-secondary)] shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                        </svg>
                      )}
                    </div>
                  );
                  return row;
                })}
              </div>
            </div>
          )}

          {/* Analyst report */}
          {report && (
            <div className="mt-8">
              <h3 className="text-sm font-semibold text-[var(--text-primary)]">
                Analyst Report
              </h3>
              <p className="mt-1 text-xs text-[var(--text-secondary)]">
                {report.summary}
              </p>

              {/* Verdict summary — clickable as filters */}
              <div className="mt-4 flex gap-4">
                {[
                  { label: "Keep", key: "keep", count: report.keep_count, color: "#22c55e" },
                  { label: "Remix", key: "remix", count: report.remix_count, color: "#eab308" },
                  { label: "Stop", key: "stop", count: report.stop_count, color: "#ef4444" },
                ].map((v) => (
                  <button
                    key={v.label}
                    onClick={() => setVerdictFilter(verdictFilter === v.key ? "all" : v.key)}
                    className={cn(
                      "flex-1 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-4 text-center transition-all",
                      verdictFilter === v.key && "ring-2"
                    )}
                    style={verdictFilter === v.key ? { borderColor: v.color, ringColor: v.color } : {}}
                  >
                    <p className="text-2xl font-bold" style={{ color: v.color }}>
                      {v.count}
                    </p>
                    <p className="text-xs text-[var(--text-secondary)]">{v.label}</p>
                  </button>
                ))}
              </div>

              {/* Individual verdicts */}
              <div className="mt-4 space-y-2">
                {filteredVerdicts.map((v) => (
                  <div
                    key={v.post_id}
                    className={cn(
                      "rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] px-4 py-3 transition-colors",
                      v.permalink && "hover:border-indigo-400 cursor-pointer"
                    )}
                    onClick={() => v.permalink && window.open(v.permalink, "_blank")}
                  >
                    <div className="flex items-center gap-3">
                      <span
                        className={`rounded-full px-2 py-0.5 text-xs font-medium ${verdictColors[v.verdict]}`}
                      >
                        {v.verdict}
                      </span>
                      <PlatformBadge platform={v.platform} />
                      <span className="flex-1 text-sm text-[var(--text-primary)] truncate">
                        {v.caption || v.post_id}
                      </span>
                      <span
                        className="text-sm font-mono font-bold rounded-md px-1.5 py-0.5"
                        style={{
                          color: scoreColor(v.composite_score),
                          backgroundColor: `${scoreColor(v.composite_score)}15`,
                        }}
                      >
                        {v.composite_score.toFixed(0)}
                      </span>
                      {v.permalink && (
                        <svg className="w-4 h-4 text-[var(--text-secondary)] shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                        </svg>
                      )}
                    </div>
                    <p className="mt-1.5 text-xs text-[var(--text-secondary)]">{v.reason}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </>
  );
}
