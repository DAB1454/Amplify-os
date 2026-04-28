"use client";

import { Bell } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { apiGet, apiPost } from "@/lib/api";

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

interface UnreadCount {
  unread: number;
}

// Poll interval for the badge. 30s is a fine compromise between
// "feels live" and "don't hammer the API from every tab".
const POLL_MS = 30_000;

const SEVERITY_DOT: Record<NotificationRow["severity"], string> = {
  info: "bg-blue-500",
  success: "bg-green-500",
  warning: "bg-yellow-500",
  error: "bg-red-500",
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

export function NotificationsBell() {
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<NotificationRow[]>([]);
  const [loading, setLoading] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const refreshCount = useCallback(async () => {
    try {
      const res = await apiGet<UnreadCount>("/notifications/unread-count");
      setUnread(res.unread);
    } catch {
      // Silently ignore — user probably isn't logged in yet or API blipped.
    }
  }, []);

  const loadItems = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await apiGet<NotificationRow[]>("/notifications?limit=20");
      setItems(rows);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshCount();
    const id = window.setInterval(refreshCount, POLL_MS);
    return () => window.clearInterval(id);
  }, [refreshCount]);

  // Close dropdown on outside click.
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [open]);

  const handleToggle = () => {
    const next = !open;
    setOpen(next);
    if (next) loadItems();
  };

  const handleMarkAll = async () => {
    try {
      await apiPost("/notifications/read-all", {});
      setItems((prev) =>
        prev.map((n) => ({ ...n, read_at: n.read_at ?? new Date().toISOString() })),
      );
      setUnread(0);
    } catch {
      /* noop */
    }
  };

  const handleClickItem = async (n: NotificationRow) => {
    if (!n.read_at) {
      try {
        await apiPost(`/notifications/${n.id}/read`, {});
        setItems((prev) =>
          prev.map((x) =>
            x.id === n.id ? { ...x, read_at: new Date().toISOString() } : x,
          ),
        );
        setUnread((c) => Math.max(0, c - 1));
      } catch {
        /* noop */
      }
    }
    if (n.url) {
      window.location.href = n.url;
    }
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        onClick={handleToggle}
        className="relative rounded-lg p-2 text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)] transition-colors"
        aria-label="Notifications"
      >
        <Bell className="h-5 w-5" />
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-[1rem] items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-semibold text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-2 w-96 origin-top-right rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] shadow-xl">
          <div className="flex items-center justify-between border-b border-[var(--border)] px-4 py-2">
            <h3 className="text-sm font-semibold">Notifications</h3>
            {unread > 0 && (
              <button
                onClick={handleMarkAll}
                className="text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
              >
                Mark all read
              </button>
            )}
          </div>
          <div className="max-h-96 overflow-y-auto">
            {loading ? (
              <div className="px-4 py-6 text-center text-sm text-[var(--text-secondary)]">
                Loading…
              </div>
            ) : items.length === 0 ? (
              <div className="px-4 py-8 text-center text-sm text-[var(--text-secondary)]">
                You&apos;re all caught up.
              </div>
            ) : (
              items.map((n) => (
                <button
                  key={n.id}
                  onClick={() => handleClickItem(n)}
                  className={`flex w-full gap-3 border-b border-[var(--border)] px-4 py-3 text-left transition-colors hover:bg-[var(--bg-surface-hover)] ${
                    n.read_at ? "opacity-60" : ""
                  }`}
                >
                  <span
                    className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${SEVERITY_DOT[n.severity]}`}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline justify-between gap-2">
                      <p className="truncate text-sm font-medium">{n.title}</p>
                      <span className="shrink-0 text-[11px] text-[var(--text-secondary)]">
                        {formatRelative(n.created_at)}
                      </span>
                    </div>
                    {n.body && (
                      <p className="mt-0.5 line-clamp-2 text-xs text-[var(--text-secondary)]">
                        {n.body}
                      </p>
                    )}
                  </div>
                </button>
              ))
            )}
          </div>
          <Link
            href="/notifications"
            onClick={() => setOpen(false)}
            className="block border-t border-[var(--border)] px-4 py-2.5 text-center text-xs font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
          >
            View all notifications
          </Link>
        </div>
      )}
    </div>
  );
}
