"use client";

import { useState, useEffect, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { apiGet, apiPost, apiPut, apiDelete, apiUpload } from "@/lib/api";
import { useRef } from "react";
import { LoadingOverlay, ButtonSpinner, Spinner } from "@/components/ui/spinner";
import { formatLocal } from "@/lib/utils";

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
  const [mediaFiles, setMediaFiles] = useState<{ file: File; url: string | null; uploading: boolean }[]>([]);
  const [audioFile, setAudioFile] = useState<{ file: File; url: string | null } | null>(null);
  const [merging, setMerging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const audioInputRef = useRef<HTMLInputElement>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [schedulingPostId, setSchedulingPostId] = useState<string | null>(null);
  const [scheduleDateTime, setScheduleDateTime] = useState("");
  const [editingPostId, setEditingPostId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState("");
  const [editSaving, setEditSaving] = useState(false);
  const [previewPostId, setPreviewPostId] = useState<string | null>(null);
  const [previewData, setPreviewData] = useState<Record<string, unknown> | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

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
    setActionLoading(`${postId}-${action}`);
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
    } finally {
      setActionLoading(null);
    }
  };

  const handleSchedule = async (postId: string) => {
    if (!scheduleDateTime) return;
    setActionLoading(`${postId}-schedule`);
    try {
      // Convert local datetime to ISO string with timezone
      const localDate = new Date(scheduleDateTime);
      await apiPost(`/api/v1/posts/${postId}/schedule`, {
        scheduled_at: localDate.toISOString(),
      });
      setSchedulingPostId(null);
      setScheduleDateTime("");
      setFetchError(null);
      fetchPosts();
    } catch (err) {
      setFetchError(err instanceof Error ? err.message : "Failed to schedule post");
    } finally {
      setActionLoading(null);
    }
  };

  const handleEditSave = async (postId: string) => {
    setEditSaving(true);
    try {
      await apiPut(`/api/v1/posts/${postId}`, { content_text: editContent });
      setEditingPostId(null);
      setEditContent("");
      setFetchError(null);
      fetchPosts();
    } catch (err) {
      setFetchError(err instanceof Error ? err.message : "Failed to save edit");
    } finally {
      setEditSaving(false);
    }
  };

  const handlePreview = async (postId: string) => {
    setPreviewPostId(postId);
    setPreviewLoading(true);
    setPreviewData(null);
    try {
      const data = await apiPost<Record<string, unknown>>(`/api/v1/posts/${postId}/preview`, {});
      setPreviewData(data);
    } catch (err) {
      setPreviewData({ error: err instanceof Error ? err.message : "Preview failed" });
    } finally {
      setPreviewLoading(false);
    }
  };

  const actionsForStatus = (status: string): { label: string; action: string; style: string }[] => {
    switch (status) {
      case "draft":
        return [
          { label: "Edit", action: "edit", style: "bg-indigo-100 text-indigo-600" },
          { label: "Queue", action: "queue", style: "bg-[var(--brand-gold)] text-white" },
          { label: "Schedule", action: "schedule", style: "bg-blue-600 text-white" },
        ];
      case "queued":
        return [
          { label: "Edit", action: "edit", style: "bg-indigo-100 text-indigo-600" },
          { label: "Preview", action: "preview", style: "bg-blue-600/20 text-blue-600" },
          { label: "Approve", action: "approve", style: "bg-green-600 text-white" },
          { label: "Reject", action: "reject", style: "bg-red-100 text-red-600" },
        ];
      case "approved":
        return [
          { label: "Publish Now", action: "publish", style: "bg-[var(--brand-gold)] text-white" },
          { label: "Schedule", action: "schedule", style: "bg-blue-600 text-white" },
          { label: "Preview", action: "preview", style: "bg-blue-600/20 text-blue-600" },
        ];
      case "scheduled":
        return [
          { label: "Publish Now", action: "publish", style: "bg-[var(--brand-gold)] text-white" },
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
              // Upload any pending files first
              const uploadedUrls: string[] = [];
              for (const mf of mediaFiles) {
                if (mf.url) {
                  uploadedUrls.push(mf.url);
                } else {
                  const result = await apiUpload<{ url: string }>("/api/v1/media/upload", mf.file);
                  uploadedUrls.push(result.url);
                }
              }

              // If audio file is attached, upload it and merge with the first video
              if (audioFile && uploadedUrls.length > 0) {
                setMerging(true);
                let audioUrl = audioFile.url;
                if (!audioUrl) {
                  const audioResult = await apiUpload<{ url: string }>("/api/v1/media/upload", audioFile.file);
                  audioUrl = audioResult.url;
                }
                // Merge audio with the first video file
                const mergeResult = await apiPost<{ url: string }>(`/api/v1/media/merge?video_url=${encodeURIComponent(uploadedUrls[0])}&audio_url=${encodeURIComponent(audioUrl)}`, {});
                uploadedUrls[0] = mergeResult.url;
                setMerging(false);
              }

              // Also include any manually entered URLs
              const manualUrls = newPost.media_urls ? newPost.media_urls.split(",").map((u) => u.trim()).filter(Boolean) : [];

              await apiPost("/api/v1/posts", {
                channel_id: newPost.channel_id,
                platform: newPost.platform,
                content_text: newPost.content_text,
                media_urls: [...uploadedUrls, ...manualUrls],
              });
              setShowCreate(false);
              setNewPost({ channel_id: "", platform: "", content_text: "", media_urls: "" });
              setMediaFiles([]);
              setAudioFile(null);
              fetchPosts();
            } catch (err) {
              setMerging(false);
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
            <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">Media Files</label>
            <input
              ref={fileInputRef}
              type="file"
              accept="video/*,audio/*,image/*"
              multiple
              className="hidden"
              onChange={(e) => {
                const files = Array.from(e.target.files || []);
                setMediaFiles((prev) => [
                  ...prev,
                  ...files.map((f) => ({ file: f, url: null, uploading: false })),
                ]);
                if (fileInputRef.current) fileInputRef.current.value = "";
              }}
            />
            <div className="flex flex-wrap gap-2 mb-2">
              {mediaFiles.map((mf, i) => (
                <span key={i} className="inline-flex items-center gap-1 rounded-lg bg-[var(--bg-primary)] border border-[var(--border-color)] px-2 py-1 text-xs">
                  <span className="truncate max-w-[200px]">
                    {mf.file.type.startsWith("video/") ? "🎬" : mf.file.type.startsWith("audio/") ? "🎵" : "🖼️"}{" "}
                    {mf.file.name}
                  </span>
                  <span className="text-[var(--text-secondary)]">
                    ({(mf.file.size / (1024 * 1024)).toFixed(1)} MB)
                  </span>
                  <button
                    type="button"
                    onClick={() => setMediaFiles((prev) => prev.filter((_, j) => j !== i))}
                    className="ml-1 text-red-500 hover:text-red-700"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="rounded-lg border border-dashed border-[var(--border-color)] px-4 py-2 text-sm text-[var(--text-secondary)] hover:border-[var(--brand-gold)] hover:text-[var(--brand-gold)] transition-colors"
            >
              + Add Video or Image
            </button>
          </div>
          <div>
            <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">
              Audio Track <span className="text-[var(--text-secondary)] font-normal">(optional — merges with video)</span>
            </label>
            <input
              ref={audioInputRef}
              type="file"
              accept="audio/*"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) setAudioFile({ file, url: null });
                if (audioInputRef.current) audioInputRef.current.value = "";
              }}
            />
            {audioFile ? (
              <span className="inline-flex items-center gap-1 rounded-lg bg-[var(--bg-primary)] border border-[var(--border-color)] px-2 py-1 text-xs">
                <span className="truncate max-w-[200px]">
                  🎵 {audioFile.file.name}
                </span>
                <span className="text-[var(--text-secondary)]">
                  ({(audioFile.file.size / (1024 * 1024)).toFixed(1)} MB)
                </span>
                <button
                  type="button"
                  onClick={() => setAudioFile(null)}
                  className="ml-1 text-red-500 hover:text-red-700"
                >
                  ×
                </button>
              </span>
            ) : (
              <button
                type="button"
                onClick={() => audioInputRef.current?.click()}
                className="rounded-lg border border-dashed border-[var(--border-color)] px-4 py-2 text-sm text-[var(--text-secondary)] hover:border-[var(--brand-gold)] hover:text-[var(--brand-gold)] transition-colors"
              >
                + Add Audio Track
              </button>
            )}
          </div>
          <div>
            <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">Or paste media URLs (comma-separated)</label>
            <input
              type="text"
              value={newPost.media_urls}
              onChange={(e) => setNewPost({ ...newPost, media_urls: e.target.value })}
              placeholder="https://example.com/video.mp4"
              className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-secondary)]"
            />
          </div>
          {createError && (
            <p className="text-xs text-red-600 break-words whitespace-pre-wrap">{createError}</p>
          )}
          <button
            type="submit"
            disabled={channels.length === 0 || creating}
            className="rounded-lg bg-[var(--brand-gold)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 transition-opacity disabled:opacity-50"
          >
            {merging ? <ButtonSpinner label="Merging audio..." /> : creating ? <ButtonSpinner label="Creating..." /> : "Create Post"}
          </button>
        </form>
      )}

      {fetchError && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-50 px-4 py-3 text-sm text-red-600 break-words whitespace-pre-wrap overflow-hidden">
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
          <LoadingOverlay text="Loading posts..." />
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
                    <p className="mt-1 text-xs text-red-600 break-words whitespace-pre-wrap">
                      Error: {post.last_error}
                    </p>
                  )}

                  <div className="mt-2 flex gap-4 text-xs text-[var(--text-secondary)]">
                    {post.scheduled_at && (
                      <span>Scheduled: {formatLocal(post.scheduled_at)}</span>
                    )}
                    {post.published_at && (
                      <span>Published: {formatLocal(post.published_at)}</span>
                    )}
                    <span>Created: {formatLocal(post.created_at)}</span>
                  </div>
                </div>

                <div className="flex flex-col gap-2 ml-4 shrink-0 items-end">
                  <div className="flex gap-2">
                  {actionsForStatus(post.status).map((btn) =>
                    btn.action === "schedule" ? (
                      <button
                        key="schedule"
                        onClick={() => {
                          if (schedulingPostId === post.id) {
                            setSchedulingPostId(null);
                          } else {
                            setSchedulingPostId(post.id);
                            const tomorrow = new Date();
                            tomorrow.setDate(tomorrow.getDate() + 1);
                            tomorrow.setHours(10, 0, 0, 0);
                            const pad = (n: number) => n.toString().padStart(2, "0");
                            setScheduleDateTime(
                              `${tomorrow.getFullYear()}-${pad(tomorrow.getMonth() + 1)}-${pad(tomorrow.getDate())}T${pad(tomorrow.getHours())}:${pad(tomorrow.getMinutes())}`
                            );
                          }
                        }}
                        className={`rounded-lg px-3 py-1.5 text-xs font-medium hover:opacity-90 ${btn.style}`}
                      >
                        Schedule
                      </button>
                    ) : btn.action === "edit" ? (
                      <button
                        key="edit"
                        onClick={() => {
                          if (editingPostId === post.id) {
                            setEditingPostId(null);
                          } else {
                            setEditingPostId(post.id);
                            setEditContent(post.content_text || "");
                          }
                        }}
                        className={`rounded-lg px-3 py-1.5 text-xs font-medium hover:opacity-90 ${btn.style}`}
                      >
                        {editingPostId === post.id ? "Cancel Edit" : "Edit"}
                      </button>
                    ) : btn.action === "preview" ? (
                      <button
                        key="preview"
                        onClick={() => handlePreview(post.id)}
                        disabled={previewLoading && previewPostId === post.id}
                        className={`rounded-lg px-3 py-1.5 text-xs font-medium hover:opacity-90 disabled:opacity-50 ${btn.style}`}
                      >
                        {previewLoading && previewPostId === post.id ? <ButtonSpinner label="Loading..." /> : "Preview"}
                      </button>
                    ) : (
                      <button
                        key={btn.action}
                        onClick={() => handleAction(post.id, btn.action)}
                        disabled={actionLoading === `${post.id}-${btn.action}`}
                        className={`rounded-lg px-3 py-1.5 text-xs font-medium hover:opacity-90 disabled:opacity-50 ${btn.style}`}
                      >
                        {actionLoading === `${post.id}-${btn.action}` ? <ButtonSpinner label={`${btn.label}...`} /> : btn.label}
                      </button>
                    )
                  )}
                  </div>
                  {/* Schedule datetime picker */}
                  {schedulingPostId === post.id && (
                    <div className="flex items-center gap-2 mt-1">
                      <input
                        type="datetime-local"
                        value={scheduleDateTime}
                        onChange={(e) => setScheduleDateTime(e.target.value)}
                        className="rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-2 py-1 text-xs text-[var(--text-primary)]"
                      />
                      <button
                        onClick={() => handleSchedule(post.id)}
                        disabled={!scheduleDateTime || actionLoading === `${post.id}-schedule`}
                        className="rounded-lg bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
                      >
                        {actionLoading === `${post.id}-schedule` ? <ButtonSpinner label="Scheduling..." /> : "Confirm"}
                      </button>
                      <button
                        onClick={() => setSchedulingPostId(null)}
                        className="text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                      >
                        Cancel
                      </button>
                    </div>
                  )}
                  {/* Inline edit */}
                  {editingPostId === post.id && (
                    <div className="mt-2 w-full">
                      <textarea
                        value={editContent}
                        onChange={(e) => setEditContent(e.target.value)}
                        rows={4}
                        className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)]"
                      />
                      <div className="flex gap-2 mt-2">
                        <button
                          onClick={() => handleEditSave(post.id)}
                          disabled={editSaving || editContent === post.content_text}
                          className="rounded-lg bg-indigo-600 px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
                        >
                          {editSaving ? <ButtonSpinner label="Saving..." /> : "Save"}
                        </button>
                        <button
                          onClick={() => setEditingPostId(null)}
                          className="text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                  <button
                    onClick={async () => {
                      if (!confirm("Delete this post?")) return;
                      setDeletingId(post.id);
                      try {
                        const result = await apiDelete<{ status: string; platform_deleted: boolean | null; message: string | null }>(`/api/v1/posts/${post.id}`);
                        if (result?.message) {
                          alert(result.message);
                        }
                        fetchPosts();
                      } catch (err) {
                        setFetchError(err instanceof Error ? err.message : "Failed to delete post");
                      } finally {
                        setDeletingId(null);
                      }
                    }}
                    disabled={deletingId === post.id}
                    className="rounded-lg px-3 py-1.5 text-xs font-medium bg-red-100 text-red-600 hover:bg-red-200 disabled:opacity-50"
                  >
                    {deletingId === post.id ? <ButtonSpinner label="Deleting..." spinnerClass="text-red-600" /> : "Delete"}
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Preview modal */}
      {previewPostId && previewData && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-lg rounded-2xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-6 shadow-xl">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-[var(--text-primary)]">Post Preview</h3>
              <button
                onClick={() => { setPreviewPostId(null); setPreviewData(null); }}
                className="text-[var(--text-secondary)] hover:text-[var(--text-primary)] text-lg"
              >
                &times;
              </button>
            </div>
            {previewData.error ? (
              <p className="text-sm text-red-600">{String(previewData.error)}</p>
            ) : (() => {
              const pv = (previewData.preview || {}) as Record<string, unknown>;
              const mediaUrls = Array.isArray(pv.media_urls) ? (pv.media_urls as string[]) : [];
              return (
                <div className="space-y-3">
                  <div>
                    <span className="text-xs font-medium text-[var(--text-secondary)]">Platform</span>
                    <p className="text-sm capitalize text-[var(--text-primary)]">
                      {String(pv.platform || "—")}
                    </p>
                  </div>
                  <div>
                    <span className="text-xs font-medium text-[var(--text-secondary)]">Content</span>
                    <p className="mt-1 rounded-lg bg-[var(--bg-primary)] p-3 text-sm text-[var(--text-primary)] whitespace-pre-wrap">
                      {String(pv.content || "(no content)")}
                    </p>
                  </div>
                  {mediaUrls.length > 0 && (
                    <div>
                      <span className="text-xs font-medium text-[var(--text-secondary)]">Media</span>
                      <div className="mt-1 flex flex-wrap gap-2">
                        {mediaUrls.map((url, i) => (
                          <a key={i} href={url} target="_blank" rel="noopener noreferrer" className="text-xs text-[var(--brand-gold)] hover:underline truncate max-w-[200px]">
                            {url.split("/").pop()}
                          </a>
                        ))}
                      </div>
                    </div>
                  )}
                  {pv.destination_url ? (
                    <div>
                      <span className="text-xs font-medium text-[var(--text-secondary)]">Link</span>
                      <p className="text-sm text-[var(--brand-gold)]">
                        {String(pv.destination_url)}
                      </p>
                    </div>
                  ) : null}
                  <div className="flex items-center gap-2 text-xs text-[var(--text-secondary)]">
                    <span>Policy: {String(previewData.policy_decision || "—")}</span>
                    <span>Status: {String(previewData.status || "—")}</span>
                  </div>
                </div>
              );
            })()}
            <button
              onClick={() => { setPreviewPostId(null); setPreviewData(null); }}
              className="mt-4 w-full rounded-lg bg-[var(--bg-primary)] px-4 py-2 text-sm font-medium text-[var(--text-primary)] hover:bg-[var(--bg-surface-hover)] transition-colors"
            >
              Close
            </button>
          </div>
        </div>
      )}
    </>
  );
}
