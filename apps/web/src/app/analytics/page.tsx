"use client";

import { useState, useEffect, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { apiGet } from "@/lib/api";
import { cn } from "@/lib/utils";

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
}

interface Verdict {
  post_id: string;
  platform: string;
  composite_score: number;
  verdict: "keep" | "remix" | "stop";
  reason: string;
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
  keep: "bg-green-500/20 text-green-400",
  remix: "bg-yellow-500/20 text-yellow-400",
  stop: "bg-red-500/20 text-red-400",
};

const platformColors: Record<string, string> = {
  instagram: "#E1306C",
  tiktok: "#00f2ea",
  youtube: "#FF0000",
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

// ── Page ───────────────────────────────────────────────────────

export default function AnalyticsPage() {
  const [range, setRange] = useState<(typeof ranges)[number]>("14d");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [timeseries, setTimeseries] = useState<TimeseriesData | null>(null);
  const [scores, setScores] = useState<PostScore[]>([]);
  const [report, setReport] = useState<AnalystReport | null>(null);
  const [loading, setLoading] = useState(true);

  const days = dayMap[range];

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [ov, ts, sc, rp] = await Promise.all([
        apiGet<Overview>("/api/v1/analytics/overview"),
        apiGet<TimeseriesData>(`/api/v1/analytics/campaigns/00000000-0000-0000-0000-000000000000/timeseries?days=${days}`),
        apiGet<PostScore[]>(`/api/v1/analytics/scores?days=${days}`),
        apiGet<AnalystReport>(`/api/v1/analytics/analyst-report?days=${days}`),
      ]);
      setOverview(ov);
      setTimeseries(ts);
      setScores(sc);
      setReport(rp);
    } catch {
      /* ignore fetch errors */
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const maxScore = Math.max(...scores.map((s) => s.composite_score), 1);

  return (
    <>
      <Header title="Analytics" />

      {/* Range selector */}
      <div className="mt-8 flex gap-2">
        {ranges.map((r) => (
          <button
            key={r}
            onClick={() => setRange(r)}
            className={cn(
              "rounded-lg px-4 py-2 text-sm font-medium transition-colors",
              range === r
                ? "bg-[var(--brand-gold)] text-black"
                : "bg-[var(--bg-surface)] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)]"
            )}
          >
            {r}
          </button>
        ))}
        {timeseries?.source === "mock" && (
          <span className="ml-auto self-center rounded bg-purple-500/20 px-2 py-1 text-xs text-purple-400">
            Mock data
          </span>
        )}
      </div>

      {loading ? (
        <div className="mt-6 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
          Loading analytics...
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
          {timeseries && (
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
          {scores.length > 0 && (
            <div className="mt-8">
              <h3 className="text-sm font-semibold text-[var(--text-primary)]">
                Post Scores
              </h3>
              <div className="mt-3 space-y-2">
                {scores
                  .sort((a, b) => b.composite_score - a.composite_score)
                  .map((s) => (
                    <div
                      key={s.post_id}
                      className="flex items-center gap-4 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] px-5 py-3"
                    >
                      <span
                        className="h-2 w-2 rounded-full shrink-0"
                        style={{ backgroundColor: platformColors[s.platform] || "#888" }}
                      />
                      <span className="w-40 truncate text-sm text-[var(--text-primary)]">
                        {s.post_id}
                      </span>
                      <span className="w-20 text-xs text-[var(--text-secondary)] capitalize">
                        {s.platform}
                      </span>
                      <div className="flex-1">
                        <MiniBar
                          value={s.composite_score}
                          max={maxScore}
                          color={
                            s.composite_score >= 70
                              ? "#22c55e"
                              : s.composite_score >= 40
                                ? "#eab308"
                                : "#ef4444"
                          }
                        />
                      </div>
                      <span className="w-12 text-right text-sm font-mono font-bold text-[var(--text-primary)]">
                        {s.composite_score.toFixed(0)}
                      </span>
                      <span className="w-20 text-right text-xs text-[var(--text-secondary)]">
                        {(s.engagement_rate * 100).toFixed(1)}% eng
                      </span>
                      <span className="w-20 text-right text-xs text-[var(--text-secondary)]">
                        {(s.click_through_rate * 100).toFixed(1)}% ctr
                      </span>
                    </div>
                  ))}
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

              {/* Verdict summary */}
              <div className="mt-4 flex gap-4">
                {[
                  { label: "Keep", count: report.keep_count, color: "text-green-400" },
                  { label: "Remix", count: report.remix_count, color: "text-yellow-400" },
                  { label: "Stop", count: report.stop_count, color: "text-red-400" },
                ].map((v) => (
                  <div
                    key={v.label}
                    className="flex-1 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-4 text-center"
                  >
                    <p className="text-2xl font-bold" style={{ color: v.color.replace("text-", "").includes("green") ? "#22c55e" : v.color.includes("yellow") ? "#eab308" : "#ef4444" }}>
                      {v.count}
                    </p>
                    <p className="text-xs text-[var(--text-secondary)]">{v.label}</p>
                  </div>
                ))}
              </div>

              {/* Individual verdicts */}
              <div className="mt-4 space-y-2">
                {report.verdicts.map((v) => (
                  <div
                    key={v.post_id}
                    className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] px-5 py-3"
                  >
                    <div className="flex items-center gap-3">
                      <span
                        className={`rounded-full px-2 py-0.5 text-xs font-medium ${verdictColors[v.verdict]}`}
                      >
                        {v.verdict}
                      </span>
                      <span className="text-sm text-[var(--text-primary)]">{v.post_id}</span>
                      <span className="text-xs text-[var(--text-secondary)] capitalize">
                        {v.platform}
                      </span>
                      <span className="ml-auto text-sm font-mono font-bold text-[var(--text-primary)]">
                        {v.composite_score.toFixed(0)}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-[var(--text-secondary)]">{v.reason}</p>
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
