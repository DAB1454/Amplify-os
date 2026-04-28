"use client";

import { useCallback, useEffect, useState } from "react";
import { Header } from "@/components/layout/header";
import { apiGet, apiPost } from "@/lib/api";
import { cn } from "@/lib/utils";

interface NotificationRow {
  id: string;
  event_type: string;
  severity: "info" | "success" | "warning" | "error";
  title: string;
  body: string;
  url: string | null;
  read_at: string | null;
  created_at: string;
}

const SEVERITY_DOT: Record<NotificationRow["severity"], string> = {
  info: "bg-blue-500",
  success: "bg-green-500",
  warning: "bg-yellow-500",
  error: "bg-red-500",
};

const SEVERITY_LABEL: Record<NotificationRow["severity"], string> = {
  info: "Info",
  success: "Success",
  warning: "Warning",
  error: "Error",
};

function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diff = Date.now() - then;
  const mins = Math.round(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return `${days}d ago`;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

type Filter = "all" | "unread" | "info" | "success" | "warning" | "error";

export default function NotificationsPage() {
  const [items, setItems] = useState<NotificationRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Filter>("all");
  const [hasMore, setHasMore] = useState(false);
  const [offset, setOffset] = useState(0);
  const LIMIT = 50;

  const loadItems = useCallback(
    async (append = false) => {
      setLoading(true);
      try {
        const unreadOnly = filter === "unread" ? "&unread_only=true" : "";
        const currentOffset = append ? offset : 0;
        const rows = await apiGet<NotificationRow[]>(
          `/notifications?limit=${LIMIT}&offset=${currentOffset}${unreadOnly}`,
        );
        if (append) {
          setItems((prev) => [...prev, ...rows]);
        } else {
          setItems(rows);
        }
        setHasMore(rows.length === LIMIT);
        setOffset(currentOffset + rows.length);
      } catch {
        if (!append) setItems([]);
      } finally {
        setLoading(false);
      }
    },
    [filter, offset],
  );

  useEffect(() => {
    setOffset(0);
    loadItems(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  const handleMarkRead = async (id: string) => {
    try {
      await apiPost(`/notifications/${id}/read`, {});
      setItems((prev) =>
        prev.map((n) =>
          n.id === id ? { ...n, read_at: n.read_at ?? new Date().toISOString() } : n,
        ),
      );
    } catch {
      /* noop */
    }
  };

  const handleMarkAllRead = async () => {
    try {
      await apiPost("/notifications/read-all", {});
      setItems((prev) =>
        prev.map((n) => ({ ...n, read_at: n.read_at ?? new Date().toISOString() })),
      );
    } catch {
      /* noop */
    }
  };

  const handleClick = async (n: NotificationRow) => {
    if (!n.read_at) await handleMarkRead(n.id);
    if (n.url) window.location.href = n.url;
  };

  const filtered =
    filter === "all" || filter === "unread"
      ? items
      : items.filter((n) => n.severity === filter);

  const unreadCount = items.filter((n) => !n.read_at).length;

  return (
    <>
      <Header title="Notifications" />

      {/* Toolbar */}
      <div className="mt-6 flex items-center justify-between">
        <div className="flex gap-2">
          {(["all", "unread", "info", "success", "warning", "error"] as Filter[]).map(
            (f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={cn(
                  "rounded-lg px-3 py-1.5 text-xs font-medium transition-colors",
                  filter === f
                    ? "bg-[var(--brand-gold)] text-white"
                    : "bg-[var(--bg-surface)] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)]",
                )}
              >
                {f === "all"
                  ? "All"
                  : f === "unread"
                    ? `Unread${unreadCount > 0 ? ` (${unreadCount})` : ""}`
                    : SEVERITY_LABEL[f]}
              </button>
            ),
          )}
        </div>
        {unreadCount > 0 && (
          <button
            onClick={handleMarkAllRead}
            className="text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
          >
            Mark all as read
          </button>
        )}
      </div>

      {/* List */}
      <div className="mt-4 space-y-2">
        {loading && items.length === 0 ? (
          <div className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
            Loading...
          </div>
        ) : filtered.length === 0 ? (
          <div className="rounded-xl border border-dashed border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
            {filter === "unread"
              ? "You're all caught up."
              : "No notifications yet."}
          </div>
        ) : (
          filtered.map((n) => (
            <button
              key={n.id}
              onClick={() => handleClick(n)}
              className={cn(
                "flex w-full items-start gap-4 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] px-5 py-4 text-left transition-colors hover:bg-[var(--bg-surface-hover)]",
                n.read_at && "opacity-60",
              )}
            >
              <span
                className={cn(
                  "mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full",
                  SEVERITY_DOT[n.severity],
                )}
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-3">
                  <p className="text-sm font-medium text-[var(--text-primary)]">
                    {n.title}
                  </p>
                  <div className="flex items-center gap-2 shrink-0">
                    <span className="text-[11px] text-[var(--text-secondary)]">
                      {formatRelative(n.created_at)}
                    </span>
                    {!n.read_at && (
                      <span className="h-2 w-2 rounded-full bg-[var(--brand-gold)]" />
                    )}
                  </div>
                </div>
                {n.body && (
                  <p className="mt-1 text-xs text-[var(--text-secondary)] line-clamp-2">
                    {n.body}
                  </p>
                )}
                <div className="mt-1.5 flex items-center gap-3">
                  <span className="text-[10px] text-[var(--text-secondary)]">
                    {formatDate(n.created_at)}
                  </span>
                  <span className="text-[10px] text-[var(--text-secondary)]">
                    {n.event_type}
                  </span>
                </div>
              </div>
            </button>
          ))
        )}
      </div>

      {/* Load more */}
      {hasMore && !loading && (
        <div className="mt-4 text-center">
          <button
            onClick={() => loadItems(true)}
            className="rounded-lg px-4 py-2 text-xs font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] bg-[var(--bg-surface)] hover:bg-[var(--bg-surface-hover)] transition-colors"
          >
            Load more
          </button>
        </div>
      )}
    </>
  );
}
