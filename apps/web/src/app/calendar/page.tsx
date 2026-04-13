"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import { apiGet } from "@/lib/api";
import { LoadingOverlay } from "@/components/ui/spinner";
import { ChevronLeft, ChevronRight } from "lucide-react";

interface CalendarPost {
  id: string;
  platform: string;
  status: string;
  content_text: string;
  media_urls: string[];
  scheduled_at: string | null;
  published_at: string | null;
  campaign_id: string | null;
  action_type_label: string | null;
}

const views = ["Week", "Month"] as const;

const platformColors: Record<string, string> = {
  instagram: "bg-pink-500",
  tiktok: "bg-cyan-400",
  youtube: "bg-red-500",
  facebook: "bg-blue-600",
  twitter: "bg-gray-800",
};

const platformLabels: Record<string, string> = {
  instagram: "IG",
  tiktok: "TT",
  youtube: "YT",
  facebook: "FB",
  twitter: "X",
};

const statusStyles: Record<string, string> = {
  draft: "border-l-gray-400 bg-gray-50 dark:bg-gray-800/40",
  queued: "border-l-yellow-400 bg-yellow-50/50 dark:bg-yellow-900/20",
  approved: "border-l-green-400 bg-green-50/50 dark:bg-green-900/20",
  scheduled: "border-l-blue-400 bg-blue-50/50 dark:bg-blue-900/20",
  publishing: "border-l-purple-400 bg-purple-50/50 dark:bg-purple-900/20",
  published: "border-l-emerald-500 bg-emerald-50/50 dark:bg-emerald-900/20",
  failed: "border-l-red-500 bg-red-50/50 dark:bg-red-900/20",
};

function startOfWeek(d: Date): Date {
  const day = d.getDay();
  const diff = d.getDate() - day;
  return new Date(d.getFullYear(), d.getMonth(), diff);
}

function addDays(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
}

function isSameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function dateKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function postDate(post: CalendarPost): string | null {
  const raw = post.published_at || post.scheduled_at;
  if (!raw) return null;
  const d = new Date(raw);
  return dateKey(d);
}

function postTime(post: CalendarPost): string {
  const raw = post.published_at || post.scheduled_at;
  if (!raw) return "";
  const d = new Date(raw);
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export default function CalendarPage() {
  const [view, setView] = useState<(typeof views)[number]>("Month");
  const [posts, setPosts] = useState<CalendarPost[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [anchor, setAnchor] = useState(() => new Date());
  const [selectedPost, setSelectedPost] = useState<CalendarPost | null>(null);

  const today = useMemo(() => new Date(), []);

  const fetchPosts = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiGet<CalendarPost[]>("/api/v1/posts/");
      setPosts(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load posts");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPosts();
  }, [fetchPosts]);

  // Build date → posts map
  const postsByDate = useMemo(() => {
    const map: Record<string, CalendarPost[]> = {};
    for (const p of posts) {
      const dk = postDate(p);
      if (!dk) continue;
      if (!map[dk]) map[dk] = [];
      map[dk].push(p);
    }
    // Sort each day by time
    for (const dk of Object.keys(map)) {
      map[dk].sort((a, b) => {
        const ta = a.published_at || a.scheduled_at || "";
        const tb = b.published_at || b.scheduled_at || "";
        return ta.localeCompare(tb);
      });
    }
    return map;
  }, [posts]);

  // Compute visible days
  const days = useMemo(() => {
    if (view === "Week") {
      const start = startOfWeek(anchor);
      return Array.from({ length: 7 }, (_, i) => addDays(start, i));
    }
    // Month: show full weeks that cover the month
    const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
    const start = startOfWeek(first);
    const result: Date[] = [];
    let d = start;
    // At least 5 weeks, up to 6 if needed to cover the month
    while (result.length < 42) {
      result.push(d);
      d = addDays(d, 1);
      if (result.length >= 35 && d.getMonth() !== anchor.getMonth()) break;
    }
    return result;
  }, [view, anchor]);

  function navigate(dir: -1 | 1) {
    if (view === "Week") {
      setAnchor((a) => addDays(a, dir * 7));
    } else {
      setAnchor((a) => new Date(a.getFullYear(), a.getMonth() + dir, 1));
    }
  }

  function goToday() {
    setAnchor(new Date());
  }

  const headerLabel = view === "Week"
    ? `${days[0]?.toLocaleDateString("en-US", { month: "short", day: "numeric" })} – ${days[6]?.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}`
    : anchor.toLocaleDateString("en-US", { month: "long", year: "numeric" });

  // Stats for the header
  const visiblePosts = useMemo(() => {
    const dayKeys = new Set(days.map(dateKey));
    return posts.filter((p) => {
      const dk = postDate(p);
      return dk && dayKeys.has(dk);
    });
  }, [days, posts]);

  const stats = useMemo(() => {
    const published = visiblePosts.filter((p) => p.status === "published").length;
    const scheduled = visiblePosts.filter((p) => p.status === "scheduled" || p.status === "approved").length;
    const failed = visiblePosts.filter((p) => p.status === "failed").length;
    return { total: visiblePosts.length, published, scheduled, failed };
  }, [visiblePosts]);

  return (
    <>
      <Header title="Calendar" />

      {/* Controls */}
      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          {views.map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={cn(
                "rounded-lg px-4 py-2 text-sm font-medium transition-colors",
                view === v
                  ? "bg-[var(--brand-gold)] text-white"
                  : "bg-[var(--bg-surface)] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)]"
              )}
            >
              {v}
            </button>
          ))}
          <button
            onClick={goToday}
            className="ml-2 rounded-lg bg-[var(--bg-surface)] px-3 py-2 text-sm font-medium text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)]"
          >
            Today
          </button>
        </div>

        <div className="flex items-center gap-3">
          <button onClick={() => navigate(-1)} className="rounded-lg p-2 hover:bg-[var(--bg-surface)]">
            <ChevronLeft size={18} className="text-[var(--text-secondary)]" />
          </button>
          <span className="min-w-[200px] text-center text-sm font-semibold text-[var(--text-primary)]">
            {headerLabel}
          </span>
          <button onClick={() => navigate(1)} className="rounded-lg p-2 hover:bg-[var(--bg-surface)]">
            <ChevronRight size={18} className="text-[var(--text-secondary)]" />
          </button>
        </div>

        {/* Summary stats */}
        <div className="flex gap-4 text-xs text-[var(--text-secondary)]">
          <span>{stats.total} posts</span>
          {stats.published > 0 && <span className="text-emerald-500">{stats.published} published</span>}
          {stats.scheduled > 0 && <span className="text-blue-500">{stats.scheduled} scheduled</span>}
          {stats.failed > 0 && <span className="text-red-500">{stats.failed} failed</span>}
        </div>
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-50 px-4 py-3 text-sm text-red-600">
          {error}
        </div>
      )}

      {loading ? (
        <div className="mt-8"><LoadingOverlay text="Loading calendar..." /></div>
      ) : (
        <>
          {/* Calendar Grid */}
          <div className="mt-4 overflow-hidden rounded-xl border border-[var(--border-color)]">
            {/* Day headers */}
            <div className="grid grid-cols-7 border-b border-[var(--border-color)] bg-[var(--bg-surface)]">
              {["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((d) => (
                <div key={d} className="px-2 py-2 text-center text-xs font-medium text-[var(--text-secondary)]">
                  {d}
                </div>
              ))}
            </div>

            {/* Day cells */}
            <div className="grid grid-cols-7">
              {days.map((day, i) => {
                const dk = dateKey(day);
                const isCurrentMonth = day.getMonth() === anchor.getMonth();
                const isToday = isSameDay(day, today);
                const isPast = day < today && !isToday;
                const dayPosts = postsByDate[dk] || [];

                return (
                  <div
                    key={dk + i}
                    className={cn(
                      "min-h-[100px] border-b border-r border-[var(--border-color)] p-1.5",
                      view === "Week" && "min-h-[200px]",
                      !isCurrentMonth && view === "Month" && "bg-[var(--bg-surface)]/50 opacity-40",
                      isPast && "opacity-60",
                    )}
                  >
                    {/* Date number */}
                    <div className="mb-1 flex items-center justify-between">
                      <span
                        className={cn(
                          "inline-flex h-6 w-6 items-center justify-center rounded-full text-xs",
                          isToday
                            ? "bg-[var(--brand-gold)] font-bold text-white"
                            : "text-[var(--text-secondary)]"
                        )}
                      >
                        {day.getDate()}
                      </span>
                      {dayPosts.length > 0 && (
                        <span className="text-[10px] text-[var(--text-secondary)]">
                          {dayPosts.length}
                        </span>
                      )}
                    </div>

                    {/* Post pills */}
                    <div className="space-y-0.5">
                      {dayPosts.slice(0, view === "Week" ? 8 : 3).map((post) => (
                        <button
                          key={post.id}
                          onClick={() => setSelectedPost(post)}
                          className={cn(
                            "group flex w-full items-center gap-1 rounded border-l-2 px-1.5 py-0.5 text-left text-[11px] transition-all hover:shadow-sm",
                            statusStyles[post.status] || "border-l-gray-400 bg-gray-50 dark:bg-gray-800/40",
                          )}
                        >
                          <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", platformColors[post.platform] || "bg-gray-400")} />
                          <span className="truncate text-[var(--text-primary)]">
                            {postTime(post)} {platformLabels[post.platform] || post.platform}
                          </span>
                        </button>
                      ))}
                      {dayPosts.length > (view === "Week" ? 8 : 3) && (
                        <span className="block text-center text-[10px] text-[var(--text-secondary)]">
                          +{dayPosts.length - (view === "Week" ? 8 : 3)} more
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Post detail panel */}
          {selectedPost && (
            <PostDetailPanel
              post={selectedPost}
              onClose={() => setSelectedPost(null)}
            />
          )}
        </>
      )}
    </>
  );
}

function PostDetailPanel({ post, onClose }: { post: CalendarPost; onClose: () => void }) {
  const time = post.published_at || post.scheduled_at;
  const formattedTime = time
    ? new Date(time).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    : "Unscheduled";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-xl border border-[var(--border-color)] bg-[var(--bg-primary)] p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className={cn("h-2.5 w-2.5 rounded-full", platformColors[post.platform] || "bg-gray-400")} />
            <span className="text-sm font-semibold text-[var(--text-primary)] capitalize">
              {post.platform === "twitter" ? "X" : post.platform}
            </span>
            <span className={cn(
              "rounded-full px-2 py-0.5 text-xs font-medium",
              post.status === "published" ? "bg-emerald-100 text-emerald-600" :
              post.status === "failed" ? "bg-red-100 text-red-600" :
              post.status === "scheduled" ? "bg-blue-100 text-blue-600" :
              "bg-gray-100 text-gray-500"
            )}>
              {post.status}
            </span>
          </div>
          <button onClick={onClose} className="text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
            &times;
          </button>
        </div>

        <p className="mt-2 text-xs text-[var(--text-secondary)]">{formattedTime}</p>

        {post.action_type_label && (
          <span className="mt-2 inline-block rounded bg-[var(--bg-surface)] px-2 py-0.5 text-xs text-[var(--text-secondary)]">
            {post.action_type_label}
          </span>
        )}

        <p className="mt-3 text-sm leading-relaxed text-[var(--text-primary)] whitespace-pre-wrap">
          {post.content_text || "(no caption)"}
        </p>

        {post.media_urls?.length > 0 && (
          <div className="mt-3 flex gap-2 overflow-x-auto">
            {post.media_urls.slice(0, 4).map((url, i) => (
              <div key={i} className="h-16 w-16 shrink-0 overflow-hidden rounded-lg bg-[var(--bg-surface)]">
                {url.match(/\.(mp4|mov|webm)/i) ? (
                  <video src={url} className="h-full w-full object-cover" muted />
                ) : (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={url} alt="" className="h-full w-full object-cover" />
                )}
              </div>
            ))}
          </div>
        )}

        <div className="mt-4 flex justify-end">
          <a
            href={`/posts`}
            className="rounded-lg bg-[var(--brand-gold)] px-4 py-2 text-sm font-medium text-white hover:opacity-90"
          >
            View in Posts
          </a>
        </div>
      </div>
    </div>
  );
}
