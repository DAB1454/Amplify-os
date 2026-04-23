"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { Header } from "@/components/layout/header";
import { apiGet, apiPost } from "@/lib/api";
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
  total_views: number;
  total_likes: number;
  total_comments: number;
  total_shares: number;
}

interface Channel {
  id: string;
  platform: string;
  display_name: string | null;
  avatar_url: string | null;
  is_active: boolean;
}

// ── Helpers ────────────────────────────────────────────────────

const ranges = ["7d", "14d", "30d"] as const;
const dayMap: Record<string, number> = { "7d": 7, "14d": 14, "30d": 30 };

const verdictColors: Record<string, string> = {
  keep: "bg-green-100 text-green-700",
  remix: "bg-yellow-100 text-yellow-700",
  stop: "bg-red-100 text-red-700",
};

const verdictLabels: Record<string, string> = {
  keep: "Repeat",
  remix: "Remix",
  stop: "Retire",
};

const platformColors: Record<string, string> = {
  instagram: "#E1306C",
  tiktok: "#00f2ea",
  youtube: "#FF0000",
  facebook: "#1877F2",
  twitter: "#000000",
};

const platformIcons: Record<string, string> = {
  instagram: "IG",
  tiktok: "TT",
  youtube: "YT",
  facebook: "FB",
  twitter: "X",
};

const FORMAT_OPTIONS = [
  { value: "", label: "Keep original format" },
  { value: "reel", label: "Reel / Short" },
  { value: "story", label: "Story" },
  { value: "post", label: "Feed post" },
  { value: "carousel", label: "Carousel" },
  { value: "short", label: "YouTube Short" },
  { value: "video", label: "Full video" },
  { value: "lyric_video", label: "Lyric video" },
];

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

// ── Repurpose Popover ─────────────────────────────────────────

function RepurposeButton({
  postId,
  sourcePlatform,
  channels,
}: {
  postId: string;
  sourcePlatform: string;
  channels: Channel[];
}) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [format, setFormat] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  // Only show channels on OTHER platforms
  const targets = channels.filter((c) => c.platform !== sourcePlatform && c.is_active);

  if (targets.length === 0) return null;

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const submit = async () => {
    if (selected.size === 0) return;
    setBusy(true);
    setResult(null);
    try {
      const res = await apiPost<{ count: number }>(`/api/v1/posts/${postId}/repurpose`, {
        channel_ids: Array.from(selected),
        action_type_label: format || undefined,
      });
      setResult(`Created ${res.count} draft${res.count !== 1 ? "s" : ""}`);
      setSelected(new Set());
      setTimeout(() => { setOpen(false); setResult(null); }, 1500);
    } catch (err) {
      setResult(err instanceof Error ? err.message : "Failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={(e) => { e.stopPropagation(); setOpen(!open); }}
        className="rounded-md px-2 py-1 text-[11px] font-medium bg-indigo-50 text-indigo-600 hover:bg-indigo-100 transition-colors shrink-0"
        title="Cross-post to other channels"
      >
        Repurpose
      </button>

      {open && (
        <div
          className="absolute right-0 top-full mt-1 z-50 w-72 rounded-xl border border-[var(--border-color)] bg-white shadow-lg p-3"
          onClick={(e) => e.stopPropagation()}
        >
          <p className="text-xs font-semibold text-[var(--text-primary)] mb-2">
            Cross-post to other channels
          </p>

          {/* Channel checkboxes */}
          <div className="space-y-1.5 max-h-40 overflow-y-auto">
            {targets.map((ch) => (
              <label
                key={ch.id}
                className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-gray-50 cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={selected.has(ch.id)}
                  onChange={() => toggle(ch.id)}
                  className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                />
                <PlatformBadge platform={ch.platform} />
                <span className="text-xs text-[var(--text-primary)] truncate">
                  {ch.display_name || ch.platform}
                </span>
              </label>
            ))}
          </div>

          {/* Format override */}
          <div className="mt-2 pt-2 border-t border-[var(--border-color)]">
            <label className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider">
              Format
            </label>
            <select
              value={format}
              onChange={(e) => setFormat(e.target.value)}
              className="mt-0.5 w-full rounded-md border border-[var(--border-color)] bg-white px-2 py-1.5 text-xs text-[var(--text-primary)]"
            >
              {FORMAT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>

          {/* Submit */}
          <button
            onClick={submit}
            disabled={selected.size === 0 || busy}
            className={cn(
              "mt-2 w-full rounded-lg px-3 py-2 text-xs font-medium text-white transition-colors",
              selected.size === 0 || busy
                ? "bg-gray-300 cursor-not-allowed"
                : "bg-indigo-500 hover:bg-indigo-600"
            )}
          >
            {busy ? "Creating..." : result || `Create ${selected.size} draft${selected.size !== 1 ? "s" : ""}`}
          </button>

          {result && !busy && (
            <p className="mt-1 text-[11px] text-center text-green-600">{result}</p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────

export default function AnalyticsPage() {
  const [range, setRange] = useState<(typeof ranges)[number]>("14d");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [timeseries, setTimeseries] = useState<TimeseriesData | null>(null);
  const [scores, setScores] = useState<PostScore[]>([]);
  const [report, setReport] = useState<AnalystReport | null>(null);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [platformFilter, setPlatformFilter] = useState<string>("all");
  const [verdictFilter, setVerdictFilter] = useState<string>("all");

  const days = dayMap[range];

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [ov, ts, sc, rp, ch] = await Promise.all([
        apiGet<Overview>("/api/v1/analytics/overview"),
        apiGet<TimeseriesData>(`/api/v1/analytics/timeseries?days=${days}`),
        apiGet<PostScore[]>(`/api/v1/analytics/scores?days=${days}`),
        apiGet<AnalystReport>(`/api/v1/analytics/analyst-report?days=${days}`),
        apiGet<Channel[]>("/api/v1/channels"),
      ]);
      setOverview(ov);
      setTimeseries(ts);
      setScores(sc);
      setReport(rp);
      setChannels(ch);
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
            <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-7">
              {[
                { label: "Campaigns", value: overview.total_campaigns },
                { label: "Total Posts", value: overview.total_posts },
                { label: "Published", value: overview.published_posts },
                { label: "Total Views", value: overview.total_views?.toLocaleString() || "0" },
                { label: "Likes", value: overview.total_likes?.toLocaleString() || "0" },
                { label: "Comments", value: overview.total_comments?.toLocaleString() || "0" },
                { label: "Shares", value: overview.total_shares?.toLocaleString() || "0" },
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
          {timeseries && Object.keys(timeseries.series).length > 0 && (() => {
            const cardOrder = ["views", "engagement"];
            const cardLabels: Record<string, string> = { views: "Views", engagement: "Engagement" };
            const cardColors: Record<string, string> = { views: "#c9a84c", engagement: "#22c55e" };
            const ordered = cardOrder.filter((m) => timeseries.series[m]?.length > 0);
            return (
              <div className="mt-6 grid grid-cols-2 gap-4">
                {ordered.map((metric) => {
                  const points = timeseries.series[metric];
                  return (
                    <div
                      key={metric}
                      className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-5"
                    >
                      <p className="text-xs font-medium uppercase tracking-wider text-[var(--text-secondary)]">
                        {cardLabels[metric] || metric}
                      </p>
                      <p className="mt-1 text-lg font-bold text-[var(--text-primary)]">
                        {points.reduce((sum: number, p: { value: number }) => sum + p.value, 0).toLocaleString()}
                      </p>
                      <div className="mt-3">
                        <SparkChart
                          points={points}
                          color={cardColors[metric] || "#3b82f6"}
                        />
                      </div>
                      <div className="mt-1 flex justify-between text-[10px] text-[var(--text-secondary)]">
                        <span>{points[0]?.date}</span>
                        <span>{points[points.length - 1]?.date}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            );
          })()}

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
                {filteredScores.map((s) => (
                  <div
                    key={s.post_id}
                    className={cn(
                      "flex items-center gap-3 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] px-4 py-3 transition-colors",
                      s.permalink && "hover:border-indigo-400 cursor-pointer"
                    )}
                    onClick={() => s.permalink && window.open(s.permalink, "_blank")}
                  >
                    <PlatformBadge platform={s.platform} />

                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-[var(--text-primary)] truncate">
                        {s.caption || s.post_id}
                      </p>
                      <div className="mt-1.5">
                        <MiniBar value={s.composite_score} max={maxScore} color={scoreColor(s.composite_score)} />
                      </div>
                    </div>

                    <span
                      className="w-10 text-center text-sm font-mono font-bold rounded-md px-1.5 py-0.5"
                      style={{
                        color: scoreColor(s.composite_score),
                        backgroundColor: `${scoreColor(s.composite_score)}15`,
                      }}
                    >
                      {s.composite_score.toFixed(0)}
                    </span>

                    <div className="flex flex-col items-end gap-0.5 shrink-0">
                      <span className="text-[11px] text-[var(--text-secondary)]">
                        {s.impressions.toLocaleString()} views
                      </span>
                      <span className="text-[11px] text-[var(--text-secondary)]">
                        {(s.engagement_rate * 100).toFixed(1)}% eng
                      </span>
                    </div>

                    {/* Repurpose button — only on higher-scoring posts with 2+ channels */}
                    {channels.length > 1 && s.composite_score >= 40 && (
                      <RepurposeButton
                        postId={s.post_id}
                        sourcePlatform={s.platform}
                        channels={channels}
                      />
                    )}

                    {s.permalink && (
                      <svg className="w-4 h-4 text-[var(--text-secondary)] shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                      </svg>
                    )}
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
              <p className="mt-1 text-[11px] text-[var(--text-secondary)] italic">
                These verdicts feed into the next campaign — the planner copies winning patterns and avoids losing ones automatically.
              </p>

              {/* Verdict summary — clickable as filters */}
              <div className="mt-4 flex gap-4">
                {[
                  { label: "Repeat", sublabel: "Use this style again", key: "keep", count: report.keep_count, color: "#22c55e" },
                  { label: "Remix", sublabel: "Try a different angle", key: "remix", count: report.remix_count, color: "#eab308" },
                  { label: "Retire", sublabel: "Deprioritize this format", key: "stop", count: report.stop_count, color: "#ef4444" },
                ].map((v) => (
                  <button
                    key={v.label}
                    onClick={() => setVerdictFilter(verdictFilter === v.key ? "all" : v.key)}
                    className={cn(
                      "flex-1 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-4 text-center transition-all",
                      verdictFilter === v.key && "ring-2"
                    )}
                    style={verdictFilter === v.key ? { borderColor: v.color, boxShadow: `0 0 0 2px ${v.color}33` } : {}}
                  >
                    <p className="text-2xl font-bold" style={{ color: v.color }}>
                      {v.count}
                    </p>
                    <p className="text-xs text-[var(--text-secondary)]">{v.label}</p>
                    <p className="text-[10px] text-[var(--text-secondary)] opacity-60 mt-0.5">{v.sublabel}</p>
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
                        {verdictLabels[v.verdict] || v.verdict}
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
