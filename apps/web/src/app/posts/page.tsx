"use client";

import { useState, useEffect, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { apiGet, apiPost, apiPut, apiDelete, apiUpload } from "@/lib/api";
import { useRef } from "react";
import { LoadingOverlay, ButtonSpinner, Spinner } from "@/components/ui/spinner";
import { MediaPreview, DownloadAllButton } from "@/components/ui/media-preview";
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
  action_type_label: string | null;
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
  const [platformFilter, setPlatformFilter] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [newPost, setNewPost] = useState({ channel_id: "", platform: "", content_text: "", media_urls: "", action_type_label: "", destination_url: "", scheduled_at: "" });
  // Video generation options for lyric_video / ai_video types
  const [videoDuration, setVideoDuration] = useState(30);
  const [videoAspect, setVideoAspect] = useState("9:16");
  const [videoLyrics, setVideoLyrics] = useState("");
  const [aiVideoPrompt, setAiVideoPrompt] = useState("");
  const [aiVideoScenes, setAiVideoScenes] = useState(6);
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
  const [editMediaUrls, setEditMediaUrls] = useState<string[]>([]);
  const [editSaving, setEditSaving] = useState(false);
  const [generatingMedia, setGeneratingMedia] = useState<string | null>(null);
  const [previewPostId, setPreviewPostId] = useState<string | null>(null);
  const [previewData, setPreviewData] = useState<Record<string, unknown> | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [aiLoading, setAiLoading] = useState(false);
  const [showAssetPicker, setShowAssetPicker] = useState(false);
  const [libraryAssets, setLibraryAssets] = useState<{id: string; name: string; asset_type: string; file_url: string; mime_type: string | null}[]>([]);
  const [selectedAssetUrls, setSelectedAssetUrls] = useState<string[]>([]);
  const [assetsLoading, setAssetsLoading] = useState(false);

  const fetchPosts = useCallback(async () => {
    setLoading(true);
    setFetchError(null);
    try {
      const params = new URLSearchParams();
      if (activeTab !== "all") params.set("status", activeTab);
      if (platformFilter) params.set("platform", platformFilter);
      const query = params.toString() ? `?${params.toString()}` : "";
      const data = await apiGet<Post[]>(`/api/v1/posts/${query}`);
      // Published/failed: newest first. Everything else: oldest first (next-to-publish on top).
      const newestFirst = activeTab === "published" || activeTab === "failed";
      data.sort((a, b) => {
        const dateA = a.published_at || a.scheduled_at || a.created_at;
        const dateB = b.published_at || b.scheduled_at || b.created_at;
        const diff = new Date(dateA).getTime() - new Date(dateB).getTime();
        return newestFirst ? -diff : diff;
      });
      setPosts(data);
    } catch (err) {
      setPosts([]);
      setFetchError(err instanceof Error ? err.message : "Failed to load posts");
    } finally {
      setLoading(false);
    }
  }, [activeTab, platformFilter]);

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

  const handleAiCaption = async (target: "create" | "edit", platform: string) => {
    setAiLoading(true);
    try {
      const result = await apiPost<{ variants: { body: string; hashtags: string[] }[] }>(
        "/api/v1/ai/generate-caption",
        {
          platform,
          artist_name: "Drew Baird",
          release_title: "For Love of Country",
          key_message: target === "create" ? newPost.content_text || "New music" : editContent || "New music",
          tone: "authentic",
        }
      );
      const best = result.variants?.[0];
      if (best) {
        const caption = best.body + (best.hashtags?.length ? "\n\n" + best.hashtags.join(" ") : "");
        if (target === "create") {
          setNewPost((prev) => ({ ...prev, content_text: caption }));
        } else {
          setEditContent(caption);
        }
      }
    } catch (err) {
      setFetchError(err instanceof Error ? err.message : "AI caption generation failed");
    } finally {
      setAiLoading(false);
    }
  };

  const handleEditSave = async (postId: string) => {
    setEditSaving(true);
    try {
      const updates: Record<string, unknown> = { content_text: editContent };
      if (editMediaUrls.length > 0) updates.media_urls = editMediaUrls;
      await apiPut(`/api/v1/posts/${postId}`, updates);
      setEditingPostId(null);
      setEditContent("");
      setEditMediaUrls([]);
      setFetchError(null);
      fetchPosts();
    } catch (err) {
      setFetchError(err instanceof Error ? err.message : "Failed to save edit");
    } finally {
      setEditSaving(false);
    }
  };

  const handleGenerateMedia = async (postId: string, opts?: { forceImageUrl?: string; forceAudioUrl?: string; lyricVideo?: boolean }) => {
    setGeneratingMedia(postId);
    setFetchError(null);
    try {
      const body: Record<string, unknown> = {};
      if (opts?.forceImageUrl) body.force_image_url = opts.forceImageUrl;
      if (opts?.forceAudioUrl) body.force_audio_url = opts.forceAudioUrl;
      if (opts?.lyricVideo) body.generate_lyric_video = true;
      await apiPost(`/api/v1/posts/${postId}/generate-media`, body, 120000);
      fetchPosts();
    } catch (err) {
      setFetchError(err instanceof Error ? err.message : "Media generation failed");
    } finally {
      setGeneratingMedia(null);
    }
  };

  const handleSaveAndRegenerate = async (postId: string, actionType?: string) => {
    await handleEditSave(postId);
    const imageUrl = editMediaUrls.find(u => /\.(jpg|jpeg|png|webp)/i.test(u));
    const audioUrl = editMediaUrls.find(u => /\.(mp3|wav|aac|flac)/i.test(u));
    const isLyric = (actionType || "").toLowerCase().includes("lyric");
    await handleGenerateMedia(postId, { forceImageUrl: imageUrl, forceAudioUrl: audioUrl, lyricVideo: isLyric });
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
    const edit = { label: "Edit", action: "edit", style: "bg-indigo-100 text-indigo-600" };
    const del = { label: "Delete", action: "delete", style: "bg-red-100 text-red-600" };
    switch (status) {
      case "draft":
        return [
          edit,
          { label: "Queue", action: "queue", style: "bg-[var(--brand-gold)] text-white" },
          { label: "Schedule", action: "schedule", style: "bg-blue-600 text-white" },
          del,
        ];
      case "queued":
        return [
          edit,
          { label: "Preview", action: "preview", style: "bg-blue-600/20 text-blue-600" },
          { label: "Approve", action: "approve", style: "bg-green-600 text-white" },
          { label: "Reject", action: "reject", style: "bg-red-100 text-red-600" },
          del,
        ];
      case "approved":
        return [
          edit,
          { label: "Publish Now", action: "publish", style: "bg-[var(--brand-gold)] text-white" },
          { label: "Schedule", action: "schedule", style: "bg-blue-600 text-white" },
          { label: "Preview", action: "preview", style: "bg-blue-600/20 text-blue-600" },
          del,
        ];
      case "scheduled":
        return [
          edit,
          { label: "Publish Now", action: "publish", style: "bg-[var(--brand-gold)] text-white" },
          { label: "Preview", action: "preview", style: "bg-blue-600/20 text-blue-600" },
          del,
        ];
      case "publishing":
        return [
          edit,
          { label: "Re-publish", action: "retry", style: "bg-[var(--brand-gold)] text-white" },
          { label: "Reset to Draft", action: "reset_draft", style: "bg-gray-100 text-gray-600" },
          del,
        ];
      case "failed":
        return [
          edit,
          { label: "Retry", action: "retry", style: "bg-[var(--brand-gold)] text-white" },
          { label: "Mark Published", action: "mark_published", style: "bg-emerald-100 text-emerald-600" },
          del,
        ];
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
                  setNewPost({ channel_id: active[0].id, platform: active[0].platform, content_text: "", media_urls: "", action_type_label: "", destination_url: "", scheduled_at: "" });
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

              // Also include any manually entered URLs and selected library assets
              const manualUrls = newPost.media_urls ? newPost.media_urls.split(",").map((u) => u.trim()).filter(Boolean) : [];

              const payload: Record<string, unknown> = {
                channel_id: newPost.channel_id,
                platform: newPost.platform,
                content_text: newPost.content_text,
                media_urls: [...uploadedUrls, ...selectedAssetUrls, ...manualUrls],
              };
              if (newPost.action_type_label) payload.action_type_label = newPost.action_type_label;
              if (newPost.destination_url) payload.destination_url = newPost.destination_url;
              if (newPost.scheduled_at) payload.scheduled_at = new Date(newPost.scheduled_at).toISOString();
              const created = await apiPost<{id: string}>("/api/v1/posts", payload);

              // Auto-trigger video generation if lyric_video or ai_video was selected
              const actionType = newPost.action_type_label;
              if (actionType === "lyric_video" && created?.id) {
                try {
                  await apiPost("/api/v1/ai/generate-lyric-video", {
                    post_id: created.id,
                    lyrics: videoLyrics || undefined,
                    duration_seconds: videoDuration,
                    aspect_ratio: videoAspect,
                    artist_name: "",
                  }, 120000);
                } catch (vidErr) {
                  setCreateError(`Post created, but video generation failed: ${vidErr instanceof Error ? vidErr.message : "Unknown error"}`);
                }
              } else if (actionType === "ai_video" && created?.id) {
                try {
                  await apiPost("/api/v1/ai/generate-ai-video", {
                    post_id: created.id,
                    prompt: aiVideoPrompt || undefined,
                    aspect_ratio: videoAspect,
                    duration_seconds: videoDuration,
                    num_scenes: aiVideoScenes,
                  }, 300000);
                } catch (vidErr) {
                  setCreateError(`Post created, but AI video generation failed: ${vidErr instanceof Error ? vidErr.message : "Unknown error"}`);
                }
              }

              setShowCreate(false);
              setNewPost({ channel_id: "", platform: "", content_text: "", media_urls: "", action_type_label: "", destination_url: "", scheduled_at: "" });
              setVideoLyrics("");
              setAiVideoPrompt("");
              setMediaFiles([]);
              setAudioFile(null);
              setSelectedAssetUrls([]);
              setShowAssetPicker(false);
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
                  setNewPost({ ...newPost, channel_id: e.target.value, platform: ch?.platform || "", action_type_label: "" });
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
          {/* Post Type + Schedule row */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">Post Type</label>
              <select
                value={newPost.action_type_label}
                onChange={(e) => {
                  const val = e.target.value;
                  setNewPost({ ...newPost, action_type_label: val });
                  // Auto-set aspect ratio based on platform
                  if (val === "lyric_video" || val === "ai_video") {
                    setVideoAspect(newPost.platform === "youtube" ? "16:9" : "9:16");
                  }
                }}
                className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)]"
              >
                {newPost.platform === "instagram" ? (
                  <>
                    <option value="">Auto-detect</option>
                    <option value="static">Static Image Post</option>
                    <option value="carousel">Carousel (multiple images)</option>
                    <option value="reel">Reel (short video)</option>
                    <option value="story">Story</option>
                    <option value="lyric_video">Lyric Video</option>
                    <option value="ai_video">AI Video ($)</option>
                  </>
                ) : newPost.platform === "tiktok" ? (
                  <>
                    <option value="">Auto-detect</option>
                    <option value="video">Video</option>
                    <option value="slideshow">Slideshow</option>
                    <option value="lyric_video">Lyric Video</option>
                    <option value="ai_video">AI Video ($)</option>
                  </>
                ) : newPost.platform === "youtube" ? (
                  <>
                    <option value="">Auto-detect</option>
                    <option value="short">YouTube Short</option>
                    <option value="video">Full Video</option>
                    <option value="lyric_video">Lyric Video</option>
                    <option value="ai_video">AI Video ($)</option>
                  </>
                ) : (
                  <>
                    <option value="">Default</option>
                    <option value="static">Image</option>
                    <option value="video">Video</option>
                    <option value="lyric_video">Lyric Video</option>
                    <option value="ai_video">AI Video ($)</option>
                  </>
                )}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">Schedule (optional)</label>
              <input
                type="datetime-local"
                value={newPost.scheduled_at}
                onChange={(e) => setNewPost({ ...newPost, scheduled_at: e.target.value })}
                className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)]"
              />
            </div>
          </div>

          {/* Lyric Video settings */}
          {newPost.action_type_label === "lyric_video" && (
            <div className="rounded-lg border border-purple-200 bg-purple-50/50 p-4 space-y-3">
              <p className="text-xs font-medium text-purple-700">Lyric Video Settings</p>
              <p className="text-[10px] text-purple-600">
                Lyrics and audio are auto-pulled from the matching track in your release. Override lyrics below if needed.
              </p>
              <textarea
                value={videoLyrics}
                onChange={(e) => setVideoLyrics(e.target.value)}
                placeholder="Optional — leave blank to auto-pull lyrics from track"
                rows={3}
                className="w-full rounded-lg border border-[var(--border-color)] bg-white px-3 py-2 text-sm placeholder:text-[var(--text-secondary)]"
              />
              <div className="flex gap-4">
                <div>
                  <label className="text-[10px] text-[var(--text-secondary)]">Duration</label>
                  <select value={videoDuration} onChange={(e) => setVideoDuration(Number(e.target.value))} className="ml-1 rounded border border-[var(--border-color)] bg-white px-2 py-1 text-xs">
                    <option value={15}>15s</option>
                    <option value={30}>30s</option>
                    <option value={60}>60s</option>
                  </select>
                </div>
                <div>
                  <label className="text-[10px] text-[var(--text-secondary)]">Aspect</label>
                  <select value={videoAspect} onChange={(e) => setVideoAspect(e.target.value)} className="ml-1 rounded border border-[var(--border-color)] bg-white px-2 py-1 text-xs">
                    <option value="9:16">9:16 (Reel/TikTok)</option>
                    <option value="1:1">1:1 (Feed)</option>
                    <option value="16:9">16:9 (YouTube)</option>
                  </select>
                </div>
              </div>
            </div>
          )}

          {/* AI Video settings */}
          {newPost.action_type_label === "ai_video" && (
            <div className="rounded-lg border border-pink-200 bg-pink-50/50 p-4 space-y-3">
              <p className="text-xs font-medium text-pink-700">AI Video Settings (Paid)</p>
              <textarea
                value={aiVideoPrompt}
                onChange={(e) => setAiVideoPrompt(e.target.value)}
                placeholder={"Describe the video scene (or leave blank to auto-generate from lyrics)...\nExample: Cinematic shots of a country road at sunset, fireflies, old barn, golden light"}
                rows={3}
                className="w-full rounded-lg border border-[var(--border-color)] bg-white px-3 py-2 text-sm placeholder:text-[var(--text-secondary)]"
              />
              <div className="flex gap-4">
                <div>
                  <label className="text-[10px] text-[var(--text-secondary)]">Scenes</label>
                  <select value={aiVideoScenes} onChange={(e) => setAiVideoScenes(Number(e.target.value))} className="ml-1 rounded border border-[var(--border-color)] bg-white px-2 py-1 text-xs">
                    <option value={4}>4 (~$1.00)</option>
                    <option value={6}>6 (~$1.50)</option>
                    <option value={8}>8 (~$2.00)</option>
                  </select>
                </div>
                <div>
                  <label className="text-[10px] text-[var(--text-secondary)]">Duration</label>
                  <select value={videoDuration} onChange={(e) => setVideoDuration(Number(e.target.value))} className="ml-1 rounded border border-[var(--border-color)] bg-white px-2 py-1 text-xs">
                    <option value={15}>15s</option>
                    <option value={30}>30s</option>
                    <option value={60}>60s</option>
                  </select>
                </div>
                <div>
                  <label className="text-[10px] text-[var(--text-secondary)]">Aspect</label>
                  <select value={videoAspect} onChange={(e) => setVideoAspect(e.target.value)} className="ml-1 rounded border border-[var(--border-color)] bg-white px-2 py-1 text-xs">
                    <option value="9:16">9:16 (Reel/TikTok)</option>
                    <option value="1:1">1:1 (Feed)</option>
                    <option value="16:9">16:9 (YouTube)</option>
                  </select>
                </div>
              </div>
              <p className="text-[10px] text-pink-600">
                AI-generated video clips are stitched with your audio. Takes 2-5 minutes. Cost charged to your Replicate account.
              </p>
            </div>
          )}

          <div>
            <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">
              Destination URL <span className="font-normal">(optional — link in bio, Linktree, etc.)</span>
            </label>
            <input
              type="url"
              value={newPost.destination_url}
              onChange={(e) => setNewPost({ ...newPost, destination_url: e.target.value })}
              placeholder="https://linktr.ee/yourname"
              className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-secondary)]"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">Content</label>
            <textarea
              value={newPost.content_text}
              onChange={(e) => setNewPost({ ...newPost, content_text: e.target.value })}
              placeholder="Write your post content or click AI Caption..."
              required
              rows={4}
              className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-secondary)]"
            />
            <button
              type="button"
              onClick={() => handleAiCaption("create", newPost.platform || "instagram")}
              disabled={aiLoading}
              className="mt-1 rounded-lg bg-indigo-600/10 px-3 py-1.5 text-xs font-medium text-indigo-600 hover:bg-indigo-600/20 disabled:opacity-50"
            >
              {aiLoading ? <ButtonSpinner label="Generating..." /> : "AI Caption"}
            </button>
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
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="rounded-lg border border-dashed border-[var(--border-color)] px-4 py-2 text-sm text-[var(--text-secondary)] hover:border-[var(--brand-gold)] hover:text-[var(--brand-gold)] transition-colors"
              >
                + Upload File
              </button>
              <button
                type="button"
                onClick={async () => {
                  if (showAssetPicker) {
                    setShowAssetPicker(false);
                    return;
                  }
                  setAssetsLoading(true);
                  try {
                    const assets = await apiGet<{id: string; name: string; asset_type: string; file_url: string; mime_type: string | null}[]>("/api/v1/assets");
                    setLibraryAssets(assets);
                  } catch (_err) {
                    setLibraryAssets([]);
                  } finally {
                    setAssetsLoading(false);
                    setShowAssetPicker(true);
                  }
                }}
                className="rounded-lg border border-dashed border-indigo-300 px-4 py-2 text-sm text-indigo-600 hover:border-indigo-500 hover:bg-indigo-50 transition-colors"
              >
                {assetsLoading ? <Spinner className="w-4 h-4" /> : showAssetPicker ? "Hide Library" : "Choose from Library"}
              </button>
            </div>

            {/* Asset Library Picker */}
            {showAssetPicker && (
              <div className="mt-2 rounded-lg border border-indigo-200 bg-indigo-50/30 p-3">
                <p className="text-xs font-medium text-indigo-700 mb-2">
                  Asset Library {libraryAssets.length > 0 && <span className="font-normal text-indigo-500">({libraryAssets.length} assets)</span>}
                </p>
                {libraryAssets.length === 0 ? (
                  <p className="text-xs text-[var(--text-secondary)]">No assets in library. Upload some first.</p>
                ) : (
                  <div className="grid grid-cols-4 gap-2 max-h-48 overflow-y-auto">
                    {libraryAssets.map((asset) => {
                      const isSelected = selectedAssetUrls.includes(asset.file_url);
                      const isImage = ["image", "album_art", "promo_photo", "logo"].includes(asset.asset_type);
                      const isVideo = ["video", "lyric_video", "ai_video"].includes(asset.asset_type);
                      const isAudio = asset.asset_type === "audio";
                      return (
                        <button
                          key={asset.id}
                          type="button"
                          onClick={() => {
                            if (isSelected) {
                              setSelectedAssetUrls((prev) => prev.filter((u) => u !== asset.file_url));
                            } else {
                              setSelectedAssetUrls((prev) => [...prev, asset.file_url]);
                            }
                          }}
                          className={`relative rounded-lg border-2 p-1 text-left transition-all ${
                            isSelected
                              ? "border-indigo-500 bg-indigo-100"
                              : "border-transparent hover:border-indigo-300 bg-white"
                          }`}
                        >
                          {isImage && (
                            <img
                              src={asset.file_url}
                              alt={asset.name}
                              className="w-full h-16 object-cover rounded"
                              loading="lazy"
                            />
                          )}
                          {isVideo && (
                            <div className="w-full h-16 rounded bg-purple-100 flex items-center justify-center">
                              <span className="text-lg">🎬</span>
                            </div>
                          )}
                          {isAudio && (
                            <div className="w-full h-16 rounded bg-blue-100 flex items-center justify-center">
                              <span className="text-lg">🎵</span>
                            </div>
                          )}
                          {!isImage && !isVideo && !isAudio && (
                            <div className="w-full h-16 rounded bg-gray-100 flex items-center justify-center">
                              <span className="text-lg">📎</span>
                            </div>
                          )}
                          <p className="mt-1 text-[9px] text-[var(--text-secondary)] truncate">{asset.name}</p>
                          {isSelected && (
                            <div className="absolute top-0 right-0 bg-indigo-500 text-white rounded-full w-5 h-5 flex items-center justify-center text-[10px] font-bold -mt-1 -mr-1">
                              ✓
                            </div>
                          )}
                        </button>
                      );
                    })}
                  </div>
                )}
                {selectedAssetUrls.length > 0 && (
                  <p className="mt-2 text-[10px] text-indigo-600 font-medium">
                    {selectedAssetUrls.length} asset{selectedAssetUrls.length !== 1 ? "s" : ""} selected
                  </p>
                )}
              </div>
            )}
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
            {merging ? <ButtonSpinner label="Merging audio..." /> : creating ? <ButtonSpinner label={newPost.action_type_label === "lyric_video" ? "Creating + Generating Video..." : newPost.action_type_label === "ai_video" ? "Creating + Generating AI Video..." : "Creating..."} /> : newPost.action_type_label === "lyric_video" ? "Create + Generate Lyric Video" : newPost.action_type_label === "ai_video" ? "Create + Generate AI Video" : "Create Post"}
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

      {/* Platform filter */}
      <div className="mt-3 flex items-center gap-2 flex-wrap">
        <span className="text-xs font-medium text-[var(--text-secondary)]">Platform:</span>
        {[null, "instagram", "tiktok", "youtube"].map((p) => (
          <button
            key={p ?? "all"}
            onClick={() => setPlatformFilter(p)}
            className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
              platformFilter === p
                ? "bg-indigo-600 text-white"
                : "bg-[var(--bg-surface)] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)] border border-[var(--border-color)]"
            }`}
          >
            {p ? p.charAt(0).toUpperCase() + p.slice(1) : "All"}
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

                  {/* Media Preview + Download */}
                  {post.media_urls && post.media_urls.length > 0 && (
                    <div className="mt-2">
                      <MediaPreview urls={post.media_urls} compact />
                      <div className="mt-1">
                        <DownloadAllButton urls={post.media_urls} />
                      </div>
                    </div>
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
                    ) : btn.action === "retry" ? (
                      <button
                        key="retry"
                        onClick={async () => {
                          setActionLoading(`${post.id}-retry`);
                          try {
                            await apiPost(`/api/v1/posts/${post.id}/retry`, { republish: true });
                            setFetchError(null);
                            fetchPosts();
                          } catch (err) {
                            setFetchError(err instanceof Error ? err.message : "Retry failed");
                          } finally {
                            setActionLoading(null);
                          }
                        }}
                        disabled={actionLoading === `${post.id}-retry`}
                        className={`rounded-lg px-3 py-1.5 text-xs font-medium hover:opacity-90 disabled:opacity-50 ${btn.style}`}
                      >
                        {actionLoading === `${post.id}-retry` ? <ButtonSpinner label="Retrying..." /> : btn.label}
                      </button>
                    ) : btn.action === "reset_draft" ? (
                      <button
                        key="reset_draft"
                        onClick={async () => {
                          setActionLoading(`${post.id}-reset_draft`);
                          try {
                            await apiPut(`/api/v1/posts/${post.id}`, { status: "draft", last_error: null });
                            setFetchError(null);
                            fetchPosts();
                          } catch (err) {
                            setFetchError(err instanceof Error ? err.message : "Reset failed");
                          } finally {
                            setActionLoading(null);
                          }
                        }}
                        disabled={actionLoading === `${post.id}-reset_draft`}
                        className={`rounded-lg px-3 py-1.5 text-xs font-medium hover:opacity-90 disabled:opacity-50 ${btn.style}`}
                      >
                        {actionLoading === `${post.id}-reset_draft` ? <ButtonSpinner label="Resetting..." /> : btn.label}
                      </button>
                    ) : btn.action === "delete" ? (
                      <button
                        key="delete"
                        onClick={async () => {
                          if (!confirm("Delete this post?")) return;
                          setActionLoading(`${post.id}-delete`);
                          try {
                            await apiDelete(`/api/v1/posts/${post.id}`);
                            setFetchError(null);
                            fetchPosts();
                          } catch (err) {
                            setFetchError(err instanceof Error ? err.message : "Delete failed");
                          } finally {
                            setActionLoading(null);
                          }
                        }}
                        disabled={actionLoading === `${post.id}-delete`}
                        className={`rounded-lg px-3 py-1.5 text-xs font-medium hover:opacity-90 disabled:opacity-50 ${btn.style}`}
                      >
                        {actionLoading === `${post.id}-delete` ? <ButtonSpinner label="Deleting..." /> : btn.label}
                      </button>
                    ) : btn.action === "mark_published" ? (
                      <button
                        key="mark_published"
                        onClick={async () => {
                          setActionLoading(`${post.id}-mark_published`);
                          try {
                            await apiPut(`/api/v1/posts/${post.id}`, {
                              status: "published",
                              last_error: null,
                              published_at: new Date().toISOString(),
                            });
                            setFetchError(null);
                            fetchPosts();
                          } catch (err) {
                            setFetchError(err instanceof Error ? err.message : "Failed to update status");
                          } finally {
                            setActionLoading(null);
                          }
                        }}
                        disabled={actionLoading === `${post.id}-mark_published`}
                        className={`rounded-lg px-3 py-1.5 text-xs font-medium hover:opacity-90 disabled:opacity-50 ${btn.style}`}
                      >
                        {actionLoading === `${post.id}-mark_published` ? <ButtonSpinner label="Updating..." /> : "Mark Published"}
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
                            setEditMediaUrls(post.media_urls || []);
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
                    <div className="mt-2 w-full space-y-2">
                      <textarea
                        value={editContent}
                        onChange={(e) => setEditContent(e.target.value)}
                        rows={4}
                        className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)]"
                      />
                      {/* Media editor */}
                      {editMediaUrls.length > 0 && (
                        <div>
                          <label className="text-[10px] font-medium text-[var(--text-secondary)]">
                            Media ({editMediaUrls.length} file{editMediaUrls.length !== 1 ? "s" : ""})
                            {editMediaUrls.length > 10 && <span className="text-red-500 ml-1">— IG carousel max is 10, remove {editMediaUrls.length - 10}</span>}
                          </label>
                          <div className="flex gap-1.5 flex-wrap mt-1">
                            {editMediaUrls.map((url, i) => {
                              const isVideo = /\.(mp4|mov|webm)/i.test(url);
                              return (
                                <div key={i} className="relative group">
                                  {isVideo ? (
                                    <video src={url} className="w-14 h-14 rounded object-cover" muted />
                                  ) : (
                                    <img src={url} alt="" className="w-14 h-14 rounded object-cover" />
                                  )}
                                  <button
                                    type="button"
                                    onClick={() => setEditMediaUrls(prev => prev.filter((_, idx) => idx !== i))}
                                    className="absolute -top-2 -right-2 w-5 h-5 rounded-full bg-red-500 text-white text-[10px] font-bold flex items-center justify-center shadow-sm"
                                  >
                                    X
                                  </button>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      )}
                      <div className="flex gap-2 flex-wrap">
                        <button
                          onClick={() => handleEditSave(post.id)}
                          disabled={editSaving}
                          className="rounded-lg bg-indigo-600 px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
                        >
                          {editSaving ? <ButtonSpinner label="Saving..." /> : "Save"}
                        </button>
                        <button
                          onClick={() => handleSaveAndRegenerate(post.id, post.action_type_label || undefined)}
                          disabled={editSaving || generatingMedia === post.id}
                          className="rounded-lg bg-green-600 px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
                          title="Save changes and regenerate video with selected image and audio"
                        >
                          {generatingMedia === post.id ? <ButtonSpinner label="Generating..." /> : "Save & Regenerate"}
                        </button>
                        <button
                          onClick={() => handleAiCaption("edit", post.platform || "instagram")}
                          disabled={aiLoading}
                          className="rounded-lg bg-indigo-600/10 px-3 py-1 text-xs font-medium text-indigo-600 hover:bg-indigo-600/20 disabled:opacity-50"
                        >
                          {aiLoading ? <ButtonSpinner label="Generating..." /> : "AI Caption"}
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
                      <MediaPreview urls={mediaUrls} />
                      <div className="mt-1">
                        <DownloadAllButton urls={mediaUrls} />
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
