"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import { apiGet, apiPost, apiDelete } from "@/lib/api";
import { LoadingOverlay } from "@/components/ui/spinner";
import { useToast } from "@/components/ui/toast";
import {
  ChevronLeft,
  ChevronRight,
  Plus,
  X,
  Milestone,
  Flag,
  Star,
  Calendar as CalendarIcon,
  Upload,
  Zap,
} from "lucide-react";
import type { CalendarItem } from "@/types";
import { CalendarImportModal } from "@/components/calendar/import-modal";

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
  draft: "border-l-gray-400 bg-gray-50",
  queued: "border-l-yellow-400 bg-yellow-50/50",
  approved: "border-l-green-400 bg-green-50/50",
  scheduled: "border-l-blue-400 bg-blue-50/50",
  publishing: "border-l-purple-400 bg-purple-50/50",
  published: "border-l-emerald-500 bg-emerald-50/50",
  failed: "border-l-red-500 bg-red-50/50",
};

const itemTypeStyles: Record<string, { bg: string; icon: typeof Milestone }> = {
  milestone: { bg: "bg-amber-100 text-amber-700 border-l-amber-400", icon: Milestone },
  deadline: { bg: "bg-red-100 text-red-600 border-l-red-400", icon: Flag },
  release: { bg: "bg-indigo-100 text-indigo-600 border-l-indigo-400", icon: Star },
  reminder: { bg: "bg-gray-100 text-gray-500 border-l-gray-400", icon: CalendarIcon },
};

const VALID_ITEM_TYPES = [
  "post", "milestone", "deadline", "reminder", "release",
  "story", "reel", "email", "ad",
];

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
  return dateKey(new Date(raw));
}

function postTime(post: CalendarPost): string {
  const raw = post.published_at || post.scheduled_at;
  if (!raw) return "";
  return new Date(raw).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

type DayEntry =
  | { kind: "post"; data: CalendarPost }
  | { kind: "item"; data: CalendarItem };

export default function CalendarPage() {
  const toast = useToast();
  const [view, setView] = useState<(typeof views)[number]>("Month");
  const [posts, setPosts] = useState<CalendarPost[]>([]);
  const [calItems, setCalItems] = useState<CalendarItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [anchor, setAnchor] = useState(() => new Date());
  const [selectedPost, setSelectedPost] = useState<CalendarPost | null>(null);
  const [selectedItem, setSelectedItem] = useState<CalendarItem | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [generatingPosts, setGeneratingPosts] = useState(false);

  const today = useMemo(() => new Date(), []);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [postData, itemData] = await Promise.all([
        apiGet<CalendarPost[]>("/api/v1/posts/"),
        apiGet<CalendarItem[]>("/api/v1/calendar"),
      ]);
      setPosts(postData);
      setCalItems(itemData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load calendar");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // ESC handler
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setSelectedPost(null);
        setSelectedItem(null);
        setShowCreate(false);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  // Build date → entries map (posts + calendar items)
  const entriesByDate = useMemo(() => {
    const map: Record<string, DayEntry[]> = {};

    for (const p of posts) {
      const dk = postDate(p);
      if (!dk) continue;
      if (!map[dk]) map[dk] = [];
      map[dk].push({ kind: "post", data: p });
    }

    for (const item of calItems) {
      const dk = item.scheduled_date;
      if (!dk) continue;
      if (!map[dk]) map[dk] = [];
      map[dk].push({ kind: "item", data: item });
    }

    // Sort: items first (milestones, deadlines), then posts by time
    for (const dk of Object.keys(map)) {
      map[dk].sort((a, b) => {
        if (a.kind !== b.kind) return a.kind === "item" ? -1 : 1;
        if (a.kind === "post" && b.kind === "post") {
          const ta = a.data.published_at || a.data.scheduled_at || "";
          const tb = b.data.published_at || b.data.scheduled_at || "";
          return ta.localeCompare(tb);
        }
        if (a.kind === "item" && b.kind === "item") {
          return (a.data.scheduled_time || "").localeCompare(b.data.scheduled_time || "");
        }
        return 0;
      });
    }
    return map;
  }, [posts, calItems]);

  // Compute visible days
  const days = useMemo(() => {
    if (view === "Week") {
      const start = startOfWeek(anchor);
      return Array.from({ length: 7 }, (_, i) => addDays(start, i));
    }
    const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
    const start = startOfWeek(first);
    const result: Date[] = [];
    let d = start;
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

  // Stats
  const dayKeys = useMemo(() => new Set(days.map(dateKey)), [days]);
  const visiblePosts = useMemo(
    () => posts.filter((p) => { const dk = postDate(p); return dk && dayKeys.has(dk); }),
    [posts, dayKeys],
  );
  const visibleItems = useMemo(
    () => calItems.filter((i) => dayKeys.has(i.scheduled_date)),
    [calItems, dayKeys],
  );

  const stats = useMemo(() => {
    const published = visiblePosts.filter((p) => p.status === "published").length;
    const scheduled = visiblePosts.filter((p) => p.status === "scheduled").length;
    const failed = visiblePosts.filter((p) => p.status === "failed").length;
    return { posts: visiblePosts.length, items: visibleItems.length, published, scheduled, failed };
  }, [visiblePosts, visibleItems]);

  const handleGeneratePosts = async () => {
    setGeneratingPosts(true);
    try {
      const result = await apiPost<{ events_synced: number; skipped: number }>(
        "/api/v1/calendar/generate-posts",
        {},
      );
      toast.success(`Created ${result.events_synced} draft posts from calendar items`);
      fetchData();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to generate posts");
    } finally {
      setGeneratingPosts(false);
    }
  };

  const handleDeleteItem = async (id: string) => {
    try {
      await apiDelete(`/api/v1/calendar/${id}`);
      setCalItems((prev) => prev.filter((i) => i.id !== id));
      setSelectedItem(null);
      toast.success("Calendar item deleted");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete item");
    }
  };

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
                  : "bg-[var(--bg-surface)] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)]",
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

        {/* Actions */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-1.5 rounded-lg bg-[var(--brand-gold)] px-3 py-2 text-sm font-medium text-white hover:opacity-90 transition-opacity"
          >
            <Plus size={16} /> Add Item
          </button>
          <button
            onClick={() => setShowImport(true)}
            className="flex items-center gap-1.5 rounded-lg bg-[var(--bg-surface)] px-3 py-2 text-sm font-medium text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)] transition-colors"
          >
            <Upload size={16} /> Import CSV
          </button>
          <button
            onClick={handleGeneratePosts}
            disabled={generatingPosts}
            className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:opacity-90 transition-opacity disabled:opacity-50"
          >
            <Zap size={16} /> {generatingPosts ? "Generating..." : "Generate Posts"}
          </button>
        </div>
      </div>

      {/* Stats */}
      <div className="mt-3 flex gap-4 text-xs text-[var(--text-secondary)]">
        <span>{stats.posts} posts</span>
        {stats.items > 0 && <span className="text-amber-500">{stats.items} events</span>}
        {stats.published > 0 && <span className="text-emerald-500">{stats.published} published</span>}
        {stats.scheduled > 0 && <span className="text-blue-500">{stats.scheduled} scheduled</span>}
        {stats.failed > 0 && <span className="text-red-500">{stats.failed} failed</span>}
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
                const dayEntries = entriesByDate[dk] || [];
                const maxShow = view === "Week" ? 8 : 3;

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
                            : "text-[var(--text-secondary)]",
                        )}
                      >
                        {day.getDate()}
                      </span>
                      {dayEntries.length > 0 && (
                        <span className="text-[10px] text-[var(--text-secondary)]">
                          {dayEntries.length}
                        </span>
                      )}
                    </div>

                    {/* Entries */}
                    <div className="space-y-0.5">
                      {dayEntries.slice(0, maxShow).map((entry) => {
                        if (entry.kind === "post") {
                          const post = entry.data;
                          return (
                            <button
                              key={`p-${post.id}`}
                              onClick={() => setSelectedPost(post)}
                              className={cn(
                                "group flex w-full items-center gap-1 rounded border-l-2 px-1.5 py-0.5 text-left text-[11px] transition-all hover:shadow-sm",
                                statusStyles[post.status] || "border-l-gray-400 bg-gray-50",
                              )}
                            >
                              <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", platformColors[post.platform] || "bg-gray-400")} />
                              <span className="truncate text-[var(--text-primary)]">
                                {postTime(post)} {platformLabels[post.platform] || post.platform}
                              </span>
                            </button>
                          );
                        }
                        const item = entry.data;
                        const style = itemTypeStyles[item.item_type];
                        return (
                          <button
                            key={`i-${item.id}`}
                            onClick={() => setSelectedItem(item)}
                            className={cn(
                              "flex w-full items-center gap-1 rounded border-l-2 px-1.5 py-0.5 text-left text-[11px] transition-all hover:shadow-sm",
                              style?.bg || "border-l-gray-400 bg-gray-100 text-gray-500",
                            )}
                          >
                            {style?.icon && <style.icon className="h-3 w-3 shrink-0" />}
                            <span className="truncate font-medium">{item.title}</span>
                          </button>
                        );
                      })}
                      {dayEntries.length > maxShow && (
                        <span className="block text-center text-[10px] text-[var(--text-secondary)]">
                          +{dayEntries.length - maxShow} more
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

          {/* Calendar item detail panel */}
          {selectedItem && (
            <ItemDetailPanel
              item={selectedItem}
              onClose={() => setSelectedItem(null)}
              onDelete={handleDeleteItem}
            />
          )}
        </>
      )}

      {/* Create item modal */}
      {showCreate && (
        <CreateItemModal
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            setShowCreate(false);
            fetchData();
          }}
        />
      )}

      {/* CSV Import modal */}
      {showImport && (
        <CalendarImportModal
          onClose={() => setShowImport(false)}
          onSuccess={() => {
            setShowImport(false);
            fetchData();
          }}
        />
      )}
    </>
  );
}

// ── Post Detail Panel ─────────────────────────────────────────────

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
              "bg-gray-100 text-gray-500",
            )}>
              {post.status}
            </span>
          </div>
          <button onClick={onClose} className="text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
            <X size={18} />
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
            href="/posts"
            className="rounded-lg bg-[var(--brand-gold)] px-4 py-2 text-sm font-medium text-white hover:opacity-90"
          >
            View in Posts
          </a>
        </div>
      </div>
    </div>
  );
}

// ── Calendar Item Detail Panel ────────────────────────────────────

function ItemDetailPanel({
  item,
  onClose,
  onDelete,
}: {
  item: CalendarItem;
  onClose: () => void;
  onDelete: (id: string) => void;
}) {
  const dateStr = new Date(item.scheduled_date + "T00:00:00").toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-xl border border-[var(--border-color)] bg-[var(--bg-primary)] p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className={cn(
              "rounded-full px-2 py-0.5 text-xs font-medium",
              item.item_type === "milestone" ? "bg-amber-100 text-amber-700" :
              item.item_type === "deadline" ? "bg-red-100 text-red-600" :
              item.item_type === "release" ? "bg-indigo-100 text-indigo-600" :
              "bg-gray-100 text-gray-500",
            )}>
              {item.item_type}
            </span>
            {item.is_completed && (
              <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs text-green-600">
                completed
              </span>
            )}
          </div>
          <button onClick={onClose} className="text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
            <X size={18} />
          </button>
        </div>

        <h3 className="mt-3 text-lg font-semibold text-[var(--text-primary)]">{item.title}</h3>
        <p className="mt-1 text-xs text-[var(--text-secondary)]">
          {dateStr}
          {item.scheduled_time && ` at ${item.scheduled_time.slice(0, 5)}`}
        </p>

        {item.description && (
          <p className="mt-3 text-sm leading-relaxed text-[var(--text-primary)] whitespace-pre-wrap">
            {item.description}
          </p>
        )}

        <div className="mt-4 flex justify-between">
          <button
            onClick={() => onDelete(item.id)}
            className="rounded-lg px-3 py-2 text-sm font-medium text-red-500 hover:bg-red-50 transition-colors"
          >
            Delete
          </button>
          <button
            onClick={onClose}
            className="rounded-lg bg-[var(--bg-surface)] px-4 py-2 text-sm font-medium text-[var(--text-primary)] hover:bg-[var(--bg-surface-hover)]"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Create Item Modal ─────────────────────────────────────────────

function CreateItemModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const toast = useToast();
  const [title, setTitle] = useState("");
  const [itemType, setItemType] = useState("milestone");
  const [scheduledDate, setScheduledDate] = useState(
    new Date().toISOString().split("T")[0],
  );
  const [scheduledTime, setScheduledTime] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;

    setSubmitting(true);
    try {
      await apiPost("/api/v1/calendar", {
        title: title.trim(),
        item_type: itemType,
        scheduled_date: scheduledDate,
        scheduled_time: scheduledTime || null,
        description,
      });
      toast.success("Calendar item created");
      onCreated();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to create item");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-xl border border-[var(--border-color)] bg-[var(--bg-primary)] p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold text-[var(--text-primary)]">Add Calendar Item</h3>
          <button onClick={onClose} className="text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          <div>
            <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">Title</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--brand-gold)] focus:outline-none"
              placeholder="Album release, deadline, milestone..."
              required
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">Type</label>
              <select
                value={itemType}
                onChange={(e) => setItemType(e.target.value)}
                className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)]"
              >
                {VALID_ITEM_TYPES.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">Date</label>
              <input
                type="date"
                value={scheduledDate}
                onChange={(e) => setScheduledDate(e.target.value)}
                className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)]"
                required
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">
              Time <span className="text-[var(--text-secondary)]">(optional)</span>
            </label>
            <input
              type="time"
              value={scheduledTime}
              onChange={(e) => setScheduledTime(e.target.value)}
              className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)]"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">
              Description <span className="text-[var(--text-secondary)]">(optional)</span>
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] resize-none focus:border-[var(--brand-gold)] focus:outline-none"
              placeholder="Additional details..."
            />
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg px-4 py-2 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || !title.trim()}
              className="rounded-lg bg-[var(--brand-gold)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              {submitting ? "Creating..." : "Create"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
