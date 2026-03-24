"use client";

import { useState, useEffect, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { apiGet, apiPost, apiDelete } from "@/lib/api";

interface Post {
  id: string;
  platform: string;
  status: string;
  content_text: string;
  media_urls: string[];
  scheduled_at: string | null;
  published_at: string | null;
  permalink: string | null;
  retry_count: number;
  last_error: string | null;
  policy_decision: string | null;
  created_at: string;
}

const statusColors: Record<string, string> = {
  draft: "bg-gray-100 text-gray-500",
  queued: "bg-yellow-100 text-yellow-600",
  approved: "bg-green-100 text-green-600",
  scheduled: "bg-blue-100 text-blue-600",
  publishing: "bg-purple-100 text-purple-600",
  published: "bg-emerald-100 text-emerald-600",
  failed: "bg-red-100 text-red-600",
};

const tabs = ["all", "draft", "queued", "scheduled", "published", "failed"] as const;

interface Channel {
  id: string;
  platform: string;
  display_name: string | null;
  is_active: boolean;
}

export default function PostsPage() {
  const [posts, setPosts] = useState<Post[]>([]);
  const [activeTab, setActiveTab] = useState<(typeof tabs)[number]>("all");
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [newPost, setNewPost] = useState({ channel_id: "", platform: "", content_text: "", media_urls: "" });
  const [createError, setCreateError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const fetchPosts = useCallback(async () => {
    setLoading(true);
    setFetchError(null);
    try {
      const query = activeTab === "all" ? "" : `?status=${activeTab}`;
      const data = await apiGet<Post[]>(`/api/v1/posts/${query}`);
      setPosts(data);
    } catch (err) {
      setPosts([]);
      setFetchError(err instanceof Error ? err.message : "Failed to load posts");
    } finally {
      setLoading(false);
    }
  }, [activeTab]);

  useEffect(() => {
    fetchPosts();
  }, [fetchPosts]);

  const handleAction = async (postId: string, action: string) => {
    try {
      const result = await apiPost<{ status?: string; policy_decision?: string; reasons?: string[] }>(
        `/api/v1/posts/${postId}/${action}`, {}
      );
      if (result?.policy_decision === "block") {
        setFetchError(`Policy blocked: ${result.reasons?.join(", ") || "Unknown reason"}`);
      } else {
        setFetchError(null);
      }
      fetchPosts();
    } catch (err) {
      setFetchError(err instanceof Error ? err.message : `Failed to ${action} post`);
    }
  };

  const actionsForStatus = (status: string): { label: string; action: string; style: string }[] => {
    switch (status) {
      case "draft":
        return [{ label: "Queue", action: "queue", style: "bg-[var(--brand-gold)] text-white" }];
      case "queued":
        return [
          { label: "Approve", action: "approve", style: "bg-green-600 text-white" },
          { label: "Reject", action: "reject", style: "bg-red-100 text-red-600" },
        ];
      case "approved":
        return [
          { label: "Publish", action: "publish", style: "bg-[var(--brand-gold)] text-white" },
          { label: "Preview", action: "preview", style: "bg-blue-600/20 text-blue-600" },
        ];
      case "scheduled":
        return [
          { label: "Preview", action: "preview", style: "bg-blue-600/20 text-blue-600" },
        ];
      case "failed":
        return [{ label: "Retry", action: "retry", style: "bg-[var(--brand-gold)] text-white" }];
      default:
        return [];
    }
  };

  return (
    <>
      <Header title="Posts" />

      <div className="mt-6 flex justify-end">
        <button
          onClick={async () => {
            if (!showCreate) {
              try {
                const chans = await apiGet<Channel[]>("/api/v1/channels");
                const active = chans.filter((c) => ["instagram", "youtube", "tiktok"].includes(c.platform));
                setChannels(active);
                if (active.length > 0) {
                  setNewPost({ channel_id: active[0].id, platform: active[0].platform, content_text: "", media_urls: "" });
                }
              } catch (err) {
                setCreateError(err instanceof Error ? err.message : "Failed to load channels");
              }
            }
            setShowCreate(!showCreate);
          }}
          className="rounded-lg bg-[var(--brand-gold)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 transition-opacity"
        >
          {showCreate ? "Cancel" : "+ New Post"}
        </button>
      </div>

      {showCreate && (
        <form
          className="mt-4 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-5 space-y-3"
          onSubmit={async (e) => {
            e.preventDefault();
            setCreateError(null);
            setCreating(true);
            try {
              await apiPost("/api/v1/posts", {
                channel_id: newPost.channel_id,
                platform: newPost.platform,
                content_text: newPost.content_text,
                media_urls: newPost.media_urls ? newPost.media_urls.split(",").map((u) => u.trim()) : [],
              });
              setShowCreate(false);
              setNewPost({ channel_id: "", platform: "", content_text: "", media_urls: "" });
              fetchPosts();
            } catch (err) {
              setCreateError(err instanceof Error ? err.message : "Failed to create post");
            } finally {
              setCreating(false);
            }
          }}
        >
          <div>
            <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">Channel</label>
            {channels.length === 0 ? (
              <p className="text-xs text-red-600">No connected channels. Connect a platform first.</p>
            ) : (
              <select
                value={newPost.channel_id}
                onChange={(e) => {
                  const ch = channels.find((c) => c.id === e.target.value);
                  setNewPost({ ...newPost, channel_id: e.target.value, platform: ch?.platform || "" });
                }}
                className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)]"
              >
                {channels.map((ch) => (
                  <option key={ch.id} value={ch.id}>
                    {ch.display_name || ch.platform} ({ch.platform})
                  </option>
                ))}
              </select>
            )}
          </div>
          <div>
            <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">Content</label>
            <textarea
              value={newPost.content_text}
              onChange={(e) => setNewPost({ ...newPost, content_text: e.target.value })}
              placeholder="Write your post content..."
              required
              rows={4}
              className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-secondary)]"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">Media URLs (comma-separated, optional)</label>
            <input
              type="text"
              value={newPost.media_urls}
              onChange={(e) => setNewPost({ ...newPost, media_urls: e.target.value })}
              placeholder="https://example.com/video.mp4"
              className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-secondary)]"
            />
          </div>
          {createError && (
            <p className="text-xs text-red-600">{createError}</p>
          )}
          <button
            type="submit"
            disabled={channels.length === 0 || creating}
            className="rounded-lg bg-[var(--brand-gold)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 transition-opacity disabled:opacity-50"
          >
            {creating ? "Creating..." : "Create Post"}
          </button>
        </form>
      )}

      {fetchError && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-50 px-4 py-3 text-sm text-red-600">
          {fetchError}
        </div>
      )}

      <div className="mt-8 flex gap-2 flex-wrap">
        {tabs.map((t) => (
          <button
            key={t}
            onClick={() => setActiveTab(t)}
            className={`rounded-lg px-4 py-2 text-sm font-medium capitalize transition-colors ${
              activeTab === t
                ? "bg-[var(--brand-gold)] text-white"
                : "bg-[var(--bg-surface)] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)]"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      <div className="mt-6 space-y-3">
        {loading ? (
          <div className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
            Loading...
          </div>
        ) : posts.length === 0 ? (
          <div className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
            No {activeTab !== "all" ? activeTab + " " : ""}posts.
          </div>
        ) : (
          posts.map((post) => (
            <div
              key={post.id}
              className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-5"
            >
              <div className="flex items-start justify-between">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-3 mb-2">
                    <span className="text-sm font-medium capitalize">{post.platform}</span>
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                        statusColors[post.status] || "bg-gray-100 text-gray-500"
                      }`}
                    >
                      {post.status}
                    </span>
                    {post.retry_count > 0 && (
                      <span className="text-xs text-[var(--text-secondary)]">
                        Retries: {post.retry_count}
                      </span>
                    )}
                  </div>

                  <p className="text-sm text-[var(--text-secondary)] line-clamp-2">
                    {post.content_text || "(no content)"}
                  </p>

                  {post.permalink && (
                    <a
                      href={post.permalink}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="mt-1 inline-block text-xs text-[var(--brand-gold)] hover:underline"
                    >
                      View post
                    </a>
                  )}

                  {post.last_error && (
                    <p className="mt-1 text-xs text-red-600 truncate">
                      Error: {post.last_error}
                    </p>
                  )}

                  <div className="mt-2 flex gap-4 text-xs text-[var(--text-secondary)]">
                    {post.scheduled_at && (
                      <span>Scheduled: {new Date(post.scheduled_at).toLocaleString()}</span>
                    )}
                    {post.published_at && (
                      <span>Published: {new Date(post.published_at).toLocaleString()}</span>
                    )}
                    <span>Created: {new Date(post.created_at).toLocaleString()}</span>
                  </div>
                </div>

                <div className="flex gap-2 ml-4 shrink-0">
                  {actionsForStatus(post.status).map((btn) => (
                    <button
                      key={btn.action}
                      onClick={() => handleAction(post.id, btn.action)}
                      className={`rounded-lg px-3 py-1.5 text-xs font-medium hover:opacity-90 ${btn.style}`}
                    >
                      {btn.label}
                    </button>
                  ))}
                  <button
                    onClick={async () => {
                      if (!confirm("Delete this post?")) return;
                      try {
                        await apiDelete(`/api/v1/posts/${post.id}`);
                        fetchPosts();
                      } catch (err) {
                        setFetchError(err instanceof Error ? err.message : "Failed to delete post");
                      }
                    }}
                    className="rounded-lg px-3 py-1.5 text-xs font-medium bg-red-100 text-red-600 hover:bg-red-200"
                  >
                    Delete
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </>
  );
}
