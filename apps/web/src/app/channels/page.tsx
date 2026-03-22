"use client";

import { useState, useEffect, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { apiGet, apiPost } from "@/lib/api";
import { cn } from "@/lib/utils";

// ── Types ──────────────────────────────────────────────────────

interface PlatformInfo {
  platform: string;
  label: string;
  mode: "automatic" | "assisted";
  description: string;
  capabilities: string[];
}

interface Channel {
  id: string;
  artist_id: string;
  platform: string;
  integration_mode: string;
  display_name: string | null;
  platform_url: string | null;
  platform_account_id: string | null;
  is_active: boolean;
  connection_status: string;
  granted_scopes: string[];
  capabilities: Record<string, boolean> | string[];
  mode_label: string;
  mode_description: string;
  last_health_check_at: string | null;
  created_at: string | null;
}

interface AssistedTask {
  id: string;
  platform: string;
  title: string;
  description: string;
  status: string;
  priority: string;
  checklist: ChecklistItem[];
  url_validations: URLValidation[];
  due_at: string | null;
  completed_at: string | null;
  created_at: string | null;
}

interface ChecklistItem {
  key: string;
  label: string;
  is_completed: boolean;
  completed_at: string | null;
  notes: string;
}

interface URLValidation {
  url: string;
  is_valid: boolean;
  status_code: number | null;
  title: string | null;
  error: string | null;
  checked_at: string;
}

// ── Helpers ────────────────────────────────────────────────────

const platformIcons: Record<string, string> = {
  instagram: "📸",
  tiktok: "🎵",
  youtube: "▶️",
  bandcamp: "🎶",
  linktree: "🔗",
  email: "📧",
};

const platformColors: Record<string, string> = {
  instagram: "#E1306C",
  tiktok: "#00f2ea",
  youtube: "#FF0000",
  bandcamp: "#1DA0C3",
  linktree: "#43E660",
  email: "#c9a84c",
};

const statusColors: Record<string, string> = {
  pending: "bg-yellow-500/20 text-yellow-400",
  in_progress: "bg-blue-500/20 text-blue-400",
  waiting_verification: "bg-purple-500/20 text-purple-400",
  completed: "bg-green-500/20 text-green-400",
  skipped: "bg-gray-500/20 text-gray-400",
  failed: "bg-red-500/20 text-red-400",
};

function ModeBadge({ mode }: { mode: string }) {
  const isAuto = mode === "automatic";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium",
        isAuto
          ? "bg-green-500/15 text-green-400 ring-1 ring-green-500/30"
          : "bg-amber-500/15 text-amber-400 ring-1 ring-amber-500/30"
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", isAuto ? "bg-green-400" : "bg-amber-400")} />
      {isAuto ? "Automatic" : "Assisted"}
    </span>
  );
}

// ── Page ───────────────────────────────────────────────────────

export default function ChannelsPage() {
  const [tab, setTab] = useState<"channels" | "tasks">("channels");
  const [platforms, setPlatforms] = useState<PlatformInfo[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [tasks, setTasks] = useState<AssistedTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedTask, setExpandedTask] = useState<string | null>(null);
  const [connectBanner, setConnectBanner] = useState<{ platform: string; status: string; message?: string } | null>(null);
  const [artistId, setArtistId] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [plats, chans, taskList, artists] = await Promise.all([
        apiGet<PlatformInfo[]>("/api/v1/channels/platforms"),
        apiGet<Channel[]>("/api/v1/channels"),
        apiGet<AssistedTask[]>("/api/v1/assisted-tasks"),
        apiGet<{ id: string }[]>("/api/v1/artists"),
      ]);
      setPlatforms(plats);
      setChannels(chans);
      setTasks(taskList);
      if (artists.length > 0) setArtistId(artists[0].id);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  // Handle OAuth callback URL params
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const connected = params.get("connected");
    const status = params.get("status");
    if (connected && status) {
      setConnectBanner({
        platform: connected,
        status,
        message: params.get("message") || undefined,
      });
      // Clear params from URL
      window.history.replaceState({}, "", window.location.pathname);
      // Refresh data to show new connection
      if (status === "success") fetchAll();
    }
  }, [fetchAll]);

  const autoChannels = channels.filter((c) => c.integration_mode === "automatic");
  const assistedChannels = channels.filter((c) => c.integration_mode === "assisted");
  const activeTasks = tasks.filter((t) => t.status !== "completed" && t.status !== "skipped");
  const completedTasks = tasks.filter((t) => t.status === "completed" || t.status === "skipped");

  return (
    <>
      <Header title="Channels & Integrations" />

      {/* Tabs */}
      <div className="mt-8 flex gap-2 border-b border-[var(--border-color)]">
        {[
          { key: "channels" as const, label: "Channels", count: channels.length },
          { key: "tasks" as const, label: "Assisted Tasks", count: activeTasks.length },
        ].map((t) => (
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
            {t.count > 0 && (
              <span className="ml-2 rounded-full bg-[var(--bg-surface)] px-2 py-0.5 text-xs">
                {t.count}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* OAuth callback banner */}
      {connectBanner && (
        <div
          className={cn(
            "mt-4 rounded-lg px-4 py-3 text-sm flex items-center justify-between",
            connectBanner.status === "success"
              ? "bg-green-500/15 text-green-400 border border-green-500/30"
              : "bg-red-500/15 text-red-400 border border-red-500/30"
          )}
        >
          <span>
            {connectBanner.status === "success"
              ? `Successfully connected ${connectBanner.platform}!`
              : `Failed to connect ${connectBanner.platform}: ${connectBanner.message || "unknown error"}`}
          </span>
          <button
            onClick={() => setConnectBanner(null)}
            className="ml-4 text-xs opacity-60 hover:opacity-100"
          >
            Dismiss
          </button>
        </div>
      )}

      {loading ? (
        <div className="mt-6 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
          Loading...
        </div>
      ) : tab === "channels" ? (
        <>
          {/* Integration mode legend */}
          <div className="mt-6 flex gap-6 text-xs text-[var(--text-secondary)]">
            <div className="flex items-center gap-2">
              <ModeBadge mode="automatic" />
              <span>Full API — auto-publish, metrics sync, comment moderation</span>
            </div>
            <div className="flex items-center gap-2">
              <ModeBadge mode="assisted" />
              <span>Manual workflow — checklists, reminders, URL validation</span>
            </div>
          </div>

          {/* Available platforms */}
          <h3 className="mt-8 text-sm font-semibold text-[var(--text-primary)]">
            Available Platforms
          </h3>
          <div className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-4">
            {platforms.map((p) => {
              const connected = channels.filter((c) => c.platform === p.platform);
              const isAutomatic = p.mode === "automatic";
              return (
                <div
                  key={p.platform}
                  className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-4"
                >
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-2">
                      <span
                        className="flex h-8 w-8 items-center justify-center rounded-lg text-lg"
                        style={{ backgroundColor: `${platformColors[p.platform] || "#888"}20` }}
                      >
                        {platformIcons[p.platform] || "🔌"}
                      </span>
                      <div>
                        <p className="text-sm font-medium text-[var(--text-primary)]">{p.label}</p>
                        <ModeBadge mode={p.mode} />
                      </div>
                    </div>
                    {connected.length > 0 && (
                      <span className="rounded-full bg-green-500/20 px-2 py-0.5 text-xs text-green-400">
                        {connected.length} connected
                      </span>
                    )}
                  </div>
                  <p className="mt-2 text-xs text-[var(--text-secondary)]">{p.description}</p>
                  {p.capabilities.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {p.capabilities.map((cap) => (
                        <span
                          key={cap}
                          className="rounded bg-[var(--bg-primary)] px-1.5 py-0.5 text-[10px] text-[var(--text-secondary)]"
                        >
                          {cap.replace("_", " ")}
                        </span>
                      ))}
                    </div>
                  )}
                  {isAutomatic && (
                    <button
                      onClick={async () => {
                        try {
                          // TODO: artist dropdown if tenant has multiple artists
                          const resp = await apiGet<{ auth_url: string | null; dry_run?: boolean; message?: string }>(
                            `/api/v1/channels/connect/${p.platform}?artist_id=${artistId}`
                          );
                          if (resp.dry_run) {
                            setConnectBanner({ platform: p.platform, status: "error", message: resp.message || "Dry-run mode — OAuth not configured" });
                          } else if (resp.auth_url) {
                            window.location.href = resp.auth_url;
                          }
                        } catch {
                          setConnectBanner({ platform: p.platform, status: "error", message: "Failed to start OAuth flow" });
                        }
                      }}
                      className="mt-3 w-full rounded-lg bg-[var(--brand-gold)] px-3 py-2 text-xs font-medium text-gray-950 hover:opacity-90 transition-opacity"
                    >
                      Connect {p.label}
                    </button>
                  )}
                </div>
              );
            })}
          </div>

          {/* Connected channels — Automatic */}
          {autoChannels.length > 0 && (
            <>
              <h3 className="mt-8 text-sm font-semibold text-[var(--text-primary)]">
                Automatic Integrations
                <span className="ml-2 text-xs font-normal text-[var(--text-secondary)]">
                  — publish, sync, and moderate automatically
                </span>
              </h3>
              <div className="mt-3 space-y-2">
                {autoChannels.map((ch) => (
                  <ChannelCard key={ch.id} channel={ch} onRefresh={fetchAll} />
                ))}
              </div>
            </>
          )}

          {/* Connected channels — Assisted */}
          {assistedChannels.length > 0 && (
            <>
              <h3 className="mt-8 text-sm font-semibold text-[var(--text-primary)]">
                Assisted Integrations
                <span className="ml-2 text-xs font-normal text-[var(--text-secondary)]">
                  — manual workflow with checklists and reminders
                </span>
              </h3>
              <div className="mt-3 space-y-2">
                {assistedChannels.map((ch) => (
                  <ChannelCard key={ch.id} channel={ch} onRefresh={fetchAll} />
                ))}
              </div>
            </>
          )}

          {channels.length === 0 && (
            <div className="mt-6 rounded-xl border border-dashed border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
              No channels connected yet. Choose a platform above to get started.
            </div>
          )}
        </>
      ) : (
        /* ── Assisted Tasks Tab ──────────────────────────────── */
        <>
          {/* Quick actions */}
          <div className="mt-6 flex flex-wrap gap-2">
            {[
              { label: "Bandcamp Release", template: "bandcamp-release", icon: "🎶" },
              { label: "Bandcamp Update", template: "bandcamp-update", icon: "🎶" },
              { label: "Linktree Sync", template: "linktree-sync", icon: "🔗" },
              { label: "Linktree Release", template: "linktree-release", icon: "🔗" },
            ].map((action) => (
              <button
                key={action.template}
                onClick={async () => {
                  try {
                    await apiPost(`/api/v1/assisted-tasks/templates/${action.template}`, {});
                    fetchAll();
                  } catch { /* ignore */ }
                }}
                className="flex items-center gap-1.5 rounded-lg border border-[var(--border-color)] bg-[var(--bg-surface)] px-3 py-2 text-xs font-medium text-[var(--text-primary)] hover:bg-[var(--bg-surface-hover)] transition-colors"
              >
                <span>{action.icon}</span>
                New {action.label} Task
              </button>
            ))}
          </div>

          {/* Active tasks */}
          {activeTasks.length > 0 && (
            <>
              <h3 className="mt-6 text-sm font-semibold text-[var(--text-primary)]">
                Active Tasks
              </h3>
              <div className="mt-3 space-y-2">
                {activeTasks.map((task) => (
                  <TaskCard
                    key={task.id}
                    task={task}
                    expanded={expandedTask === task.id}
                    onToggle={() => setExpandedTask(expandedTask === task.id ? null : task.id)}
                    onRefresh={fetchAll}
                  />
                ))}
              </div>
            </>
          )}

          {/* Completed tasks */}
          {completedTasks.length > 0 && (
            <>
              <h3 className="mt-8 text-sm font-semibold text-[var(--text-secondary)]">
                Completed ({completedTasks.length})
              </h3>
              <div className="mt-3 space-y-2">
                {completedTasks.map((task) => (
                  <TaskCard
                    key={task.id}
                    task={task}
                    expanded={expandedTask === task.id}
                    onToggle={() => setExpandedTask(expandedTask === task.id ? null : task.id)}
                    onRefresh={fetchAll}
                  />
                ))}
              </div>
            </>
          )}

          {tasks.length === 0 && (
            <div className="mt-6 rounded-xl border border-dashed border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
              No assisted tasks yet. Use the buttons above to create a workflow task.
            </div>
          )}
        </>
      )}
    </>
  );
}

// ── Sub-components ─────────────────────────────────────────────

const statusDotColors: Record<string, string> = {
  connected: "bg-green-400",
  expired: "bg-yellow-400",
  needs_refresh: "bg-yellow-400",
  not_connected: "bg-gray-500",
  disconnected: "bg-gray-500",
  revoked: "bg-red-400",
  error: "bg-red-400",
};

function CapabilityBadge({ name, enabled }: { name: string; enabled: boolean }) {
  return (
    <span
      className={cn(
        "rounded px-1.5 py-0.5 text-[10px]",
        enabled
          ? "bg-green-500/15 text-green-400"
          : "bg-gray-500/15 text-gray-500"
      )}
    >
      {name.replace("can_", "")}
    </span>
  );
}

function ChannelCard({ channel, onRefresh }: { channel: Channel; onRefresh: () => void }) {
  const status = channel.connection_status || (channel.is_active ? "connected" : "disconnected");
  const showReconnect = status === "expired" || status === "revoked" || status === "error";
  const isAutomatic = channel.integration_mode === "automatic";

  // Capabilities can be either a dict or array depending on whether OAuth has been done
  const capDict = !Array.isArray(channel.capabilities) ? channel.capabilities : {};

  return (
    <div className="flex items-center gap-4 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] px-5 py-3">
      <span
        className="flex h-8 w-8 items-center justify-center rounded-lg text-lg shrink-0"
        style={{ backgroundColor: `${platformColors[channel.platform] || "#888"}20` }}
      >
        {platformIcons[channel.platform] || "🔌"}
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-[var(--text-primary)] truncate">
            {channel.display_name || channel.platform_url || channel.platform}
          </span>
          <ModeBadge mode={channel.integration_mode} />
        </div>
        <p className="text-xs text-[var(--text-secondary)] truncate">
          {channel.mode_description}
        </p>
        {/* Capability badges for automatic channels */}
        {isAutomatic && Object.keys(capDict).length > 0 && (
          <div className="mt-1 flex gap-1">
            {Object.entries(capDict).map(([name, enabled]) => (
              <CapabilityBadge key={name} name={name} enabled={!!enabled} />
            ))}
          </div>
        )}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {channel.platform_url && (
          <a
            href={channel.platform_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-[var(--brand-gold)] hover:underline"
          >
            Open
          </a>
        )}
        {isAutomatic && (
          <>
            <button
              onClick={async () => {
                try {
                  await apiGet(`/api/v1/channels/${channel.id}/health`);
                  onRefresh();
                } catch { /* ignore */ }
              }}
              className="text-[10px] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
              title="Check Health"
            >
              Check
            </button>
            {showReconnect && (
              <button
                onClick={async () => {
                  try {
                    const resp = await apiGet<{ auth_url: string | null }>(
                      `/api/v1/channels/connect/${channel.platform}?artist_id=${channel.artist_id || "00000000-0000-0000-0000-000000000000"}`
                    );
                    if (resp.auth_url) window.location.href = resp.auth_url;
                  } catch { /* ignore */ }
                }}
                className="text-[10px] text-yellow-400 hover:text-yellow-300"
              >
                Reconnect
              </button>
            )}
            <button
              onClick={async () => {
                try {
                  await apiPost(`/api/v1/channels/${channel.id}/disconnect`, {});
                  onRefresh();
                } catch { /* ignore */ }
              }}
              className="text-[10px] text-red-400/60 hover:text-red-400"
              title="Disconnect"
            >
              Disconnect
            </button>
          </>
        )}
        <span
          className={cn(
            "h-2 w-2 rounded-full",
            statusDotColors[status] || "bg-gray-500"
          )}
          title={status}
        />
      </div>
    </div>
  );
}

function TaskCard({
  task,
  expanded,
  onToggle,
  onRefresh,
}: {
  task: AssistedTask;
  expanded: boolean;
  onToggle: () => void;
  onRefresh: () => void;
}) {
  const completed = task.checklist.filter((c) => c.is_completed).length;
  const total = task.checklist.length;
  const progress = total > 0 ? (completed / total) * 100 : 0;

  const handleToggleItem = async (key: string, current: boolean) => {
    try {
      await apiPost(
        `/api/v1/assisted-tasks/${task.id}/checklist/${key}/toggle?completed=${!current}`,
        {}
      );
      onRefresh();
    } catch { /* ignore */ }
  };

  return (
    <div className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)]">
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-4 px-5 py-3 text-left"
      >
        <span
          className="flex h-8 w-8 items-center justify-center rounded-lg text-lg shrink-0"
          style={{ backgroundColor: `${platformColors[task.platform] || "#888"}20` }}
        >
          {platformIcons[task.platform] || "📋"}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-[var(--text-primary)] truncate">
              {task.title}
            </span>
            <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium", statusColors[task.status] || "")}>
              {task.status.replace("_", " ")}
            </span>
          </div>
          {total > 0 && (
            <div className="mt-1 flex items-center gap-2">
              <div className="h-1.5 flex-1 rounded-full bg-[var(--bg-primary)]">
                <div
                  className="h-1.5 rounded-full bg-green-500 transition-all"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <span className="text-xs text-[var(--text-secondary)] shrink-0">
                {completed}/{total}
              </span>
            </div>
          )}
        </div>
        <span className="text-xs text-[var(--text-secondary)]">
          {expanded ? "▲" : "▼"}
        </span>
      </button>

      {expanded && (
        <div className="border-t border-[var(--border-color)] px-5 py-4">
          {task.description && (
            <p className="text-xs text-[var(--text-secondary)] mb-3">{task.description}</p>
          )}

          {/* Checklist */}
          {task.checklist.length > 0 && (
            <div className="space-y-1.5">
              {task.checklist.map((item) => (
                <label
                  key={item.key}
                  className="flex items-center gap-2 cursor-pointer group"
                >
                  <input
                    type="checkbox"
                    checked={item.is_completed}
                    onChange={() => handleToggleItem(item.key, item.is_completed)}
                    className="h-4 w-4 rounded border-[var(--border-color)] accent-[var(--brand-gold)]"
                  />
                  <span
                    className={cn(
                      "text-sm transition-colors",
                      item.is_completed
                        ? "text-[var(--text-secondary)] line-through"
                        : "text-[var(--text-primary)] group-hover:text-[var(--brand-gold)]"
                    )}
                  >
                    {item.label}
                  </span>
                </label>
              ))}
            </div>
          )}

          {/* URL validations */}
          {task.url_validations.length > 0 && (
            <div className="mt-4">
              <p className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider mb-2">
                URL Validations
              </p>
              <div className="space-y-1">
                {task.url_validations.map((v, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs">
                    <span className={v.is_valid ? "text-green-400" : "text-red-400"}>
                      {v.is_valid ? "✓" : "✗"}
                    </span>
                    <a
                      href={v.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[var(--brand-gold)] hover:underline truncate"
                    >
                      {v.url}
                    </a>
                    {v.title && (
                      <span className="text-[var(--text-secondary)] truncate">— {v.title}</span>
                    )}
                    {v.error && (
                      <span className="text-red-400 truncate">({v.error})</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Due date */}
          {task.due_at && (
            <p className="mt-3 text-xs text-[var(--text-secondary)]">
              Due: {new Date(task.due_at).toLocaleDateString()}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
