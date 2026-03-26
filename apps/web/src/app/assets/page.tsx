"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { Header } from "@/components/layout/header";
import { apiGet, apiDelete, apiPut, apiUpload } from "@/lib/api";
import { LoadingOverlay, ButtonSpinner } from "@/components/ui/spinner";
import { MediaPreview } from "@/components/ui/media-preview";

interface Asset {
  id: string;
  asset_type: string;
  name: string;
  description: string;
  file_url: string;
  file_size_bytes: number | null;
  mime_type: string | null;
  tags: string[];
  source: string;
  artist_id: string | null;
  release_id: string | null;
  campaign_id: string | null;
  created_at: string;
}

interface Artist {
  id: string;
  name: string;
}

interface Release {
  id: string;
  title: string;
}

const assetTypes = [
  { value: "all", label: "All Types" },
  { value: "image", label: "Images" },
  { value: "video", label: "Videos" },
  { value: "audio", label: "Audio" },
  { value: "album_art", label: "Album Art" },
  { value: "promo_photo", label: "Promo Photos" },
  { value: "logo", label: "Logos" },
];

const sourceLabels: Record<string, { label: string; color: string }> = {
  uploaded: { label: "Uploaded", color: "bg-blue-100 text-blue-600" },
  ai_generated: { label: "AI Generated", color: "bg-purple-100 text-purple-600" },
  imported: { label: "Imported", color: "bg-gray-100 text-gray-600" },
};

function formatBytes(bytes: number | null): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function AssetsPage() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [typeFilter, setTypeFilter] = useState("all");
  const [showUpload, setShowUpload] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [artists, setArtists] = useState<Artist[]>([]);
  const [releases, setReleases] = useState<Release[]>([]);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editTags, setEditTags] = useState("");
  const [editType, setEditType] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Upload form state
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [uploadName, setUploadName] = useState("");
  const [uploadType, setUploadType] = useState("image");
  const [uploadDesc, setUploadDesc] = useState("");
  const [uploadTags, setUploadTags] = useState("");
  const [uploadArtist, setUploadArtist] = useState("");
  const [uploadRelease, setUploadRelease] = useState("");

  const fetchAssets = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (typeFilter !== "all") params.set("asset_type", typeFilter);
      const query = params.toString() ? `?${params}` : "";
      const data = await apiGet<Asset[]>(`/api/v1/assets${query}`);
      setAssets(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load assets");
    } finally {
      setLoading(false);
    }
  }, [typeFilter]);

  useEffect(() => {
    fetchAssets();
  }, [fetchAssets]);

  // Load artists and releases for the upload form
  useEffect(() => {
    if (showUpload) {
      apiGet<Artist[]>("/api/v1/artists").then(setArtists).catch(() => {});
      apiGet<Release[]>("/api/v1/releases").then(setReleases).catch(() => {});
    }
  }, [showUpload]);

  const handleUpload = async () => {
    if (uploadFiles.length === 0) return;
    setUploading(true);
    setError(null);
    try {
      for (const file of uploadFiles) {
        const params = new URLSearchParams();
        params.set("name", uploadName || file.name);
        params.set("asset_type", uploadType);
        if (uploadDesc) params.set("description", uploadDesc);
        if (uploadTags) params.set("tags", uploadTags);
        if (uploadArtist) params.set("artist_id", uploadArtist);
        if (uploadRelease) params.set("release_id", uploadRelease);

        await apiUpload<Asset>(`/api/v1/assets/upload?${params}`, file);
      }
      setShowUpload(false);
      setUploadFiles([]);
      setUploadName("");
      setUploadDesc("");
      setUploadTags("");
      setUploadType("image");
      setUploadArtist("");
      setUploadRelease("");
      fetchAssets();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this asset?")) return;
    setDeletingId(id);
    try {
      await apiDelete(`/api/v1/assets/${id}`);
      fetchAssets();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setDeletingId(null);
    }
  };

  const handleEditSave = async (id: string) => {
    try {
      await apiPut(`/api/v1/assets/${id}`, {
        name: editName,
        description: editDesc,
        asset_type: editType,
        tags: editTags.split(",").map((t) => t.trim()).filter(Boolean),
      });
      setEditingId(null);
      fetchAssets();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed");
    }
  };

  const startEdit = (asset: Asset) => {
    setEditingId(asset.id);
    setEditName(asset.name);
    setEditDesc(asset.description);
    setEditTags(asset.tags.join(", "));
    setEditType(asset.asset_type);
  };

  async function downloadFile(url: string, filename: string) {
    try {
      const response = await fetch(url);
      const blob = await response.blob();
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(blobUrl);
    } catch {
      window.open(url, "_blank");
    }
  }

  return (
    <>
      <Header title="Asset Library" />

      {/* Toolbar */}
      <div className="mt-6 flex items-center justify-between gap-4 flex-wrap">
        {/* Type filter tabs */}
        <div className="flex gap-2 flex-wrap">
          {assetTypes.map((t) => (
            <button
              key={t.value}
              onClick={() => setTypeFilter(t.value)}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                typeFilter === t.value
                  ? "bg-[var(--brand-gold)] text-white"
                  : "bg-[var(--bg-surface)] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)]"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        <button
          onClick={() => setShowUpload(!showUpload)}
          className="rounded-lg bg-[var(--brand-gold)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 transition-opacity"
        >
          {showUpload ? "Cancel" : "+ Upload Assets"}
        </button>
      </div>

      {/* Upload Form */}
      {showUpload && (
        <div className="mt-4 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-5 space-y-4">
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">Upload to Asset Library</h3>

          {/* File picker */}
          <div>
            <input
              ref={fileInputRef}
              type="file"
              accept="video/*,audio/*,image/*"
              multiple
              className="hidden"
              onChange={(e) => {
                const files = Array.from(e.target.files || []);
                setUploadFiles((prev) => [...prev, ...files]);
                if (fileInputRef.current) fileInputRef.current.value = "";
              }}
            />
            <div className="flex flex-wrap gap-2 mb-2">
              {uploadFiles.map((f, i) => (
                <span key={i} className="inline-flex items-center gap-1 rounded-lg bg-[var(--bg-primary)] border border-[var(--border-color)] px-2 py-1 text-xs">
                  <span className="truncate max-w-[200px]">{f.name}</span>
                  <span className="text-[var(--text-secondary)]">({formatBytes(f.size)})</span>
                  <button type="button" onClick={() => setUploadFiles((prev) => prev.filter((_, j) => j !== i))} className="ml-1 text-red-500 hover:text-red-700">&times;</button>
                </span>
              ))}
            </div>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="rounded-lg border border-dashed border-[var(--border-color)] px-4 py-6 text-sm text-[var(--text-secondary)] hover:border-[var(--brand-gold)] hover:text-[var(--brand-gold)] transition-colors w-full text-center"
            >
              Click to select files (images, videos, audio)
            </button>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">Name</label>
              <input
                value={uploadName}
                onChange={(e) => setUploadName(e.target.value)}
                placeholder="Auto-uses filename if empty"
                className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-secondary)]"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">Type</label>
              <select
                value={uploadType}
                onChange={(e) => setUploadType(e.target.value)}
                className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)]"
              >
                <option value="image">Image</option>
                <option value="video">Video</option>
                <option value="audio">Audio</option>
                <option value="album_art">Album Art</option>
                <option value="promo_photo">Promo Photo</option>
                <option value="logo">Logo</option>
              </select>
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">Description</label>
            <input
              value={uploadDesc}
              onChange={(e) => setUploadDesc(e.target.value)}
              placeholder="What is this asset?"
              className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-secondary)]"
            />
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">Tags (comma-separated)</label>
              <input
                value={uploadTags}
                onChange={(e) => setUploadTags(e.target.value)}
                placeholder="promo, cover, live"
                className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-secondary)]"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">Artist</label>
              <select
                value={uploadArtist}
                onChange={(e) => setUploadArtist(e.target.value)}
                className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)]"
              >
                <option value="">None</option>
                {artists.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1">Release</label>
              <select
                value={uploadRelease}
                onChange={(e) => setUploadRelease(e.target.value)}
                className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)]"
              >
                <option value="">None</option>
                {releases.map((r) => (
                  <option key={r.id} value={r.id}>{r.title}</option>
                ))}
              </select>
            </div>
          </div>

          <button
            onClick={handleUpload}
            disabled={uploadFiles.length === 0 || uploading}
            className="rounded-lg bg-[var(--brand-gold)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {uploading ? <ButtonSpinner label="Uploading..." /> : `Upload ${uploadFiles.length} file${uploadFiles.length !== 1 ? "s" : ""}`}
          </button>
        </div>
      )}

      {error && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-50 px-4 py-3 text-sm text-red-600 flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-xs opacity-60 hover:opacity-100">Dismiss</button>
        </div>
      )}

      {/* Asset count */}
      {!loading && (
        <p className="mt-4 text-xs text-[var(--text-secondary)]">
          {assets.length} asset{assets.length !== 1 ? "s" : ""}
          {typeFilter !== "all" && ` (${assetTypes.find((t) => t.value === typeFilter)?.label})`}
        </p>
      )}

      {/* Asset Grid */}
      <div className="mt-4">
        {loading ? (
          <LoadingOverlay text="Loading assets..." />
        ) : assets.length === 0 ? (
          <div className="rounded-xl border border-dashed border-[var(--border-color)] bg-[var(--bg-surface)] p-12 text-center">
            <p className="text-[var(--text-secondary)]">
              No assets yet. Upload images, videos, and audio to build your content library.
            </p>
            <p className="mt-2 text-xs text-[var(--text-secondary)]">
              The AI engine will use these assets when generating content for your campaigns.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {assets.map((asset) => (
              <div
                key={asset.id}
                className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] overflow-hidden"
              >
                {/* Preview */}
                <MediaPreview urls={[asset.file_url]} compact />

                {/* Info */}
                <div className="p-4 space-y-2">
                  {editingId === asset.id ? (
                    <div className="space-y-2">
                      <input
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-2 py-1 text-sm text-[var(--text-primary)]"
                      />
                      <input
                        value={editDesc}
                        onChange={(e) => setEditDesc(e.target.value)}
                        placeholder="Description"
                        className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-2 py-1 text-xs text-[var(--text-primary)] placeholder:text-[var(--text-secondary)]"
                      />
                      <input
                        value={editTags}
                        onChange={(e) => setEditTags(e.target.value)}
                        placeholder="Tags (comma-separated)"
                        className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-2 py-1 text-xs text-[var(--text-primary)] placeholder:text-[var(--text-secondary)]"
                      />
                      <select
                        value={editType}
                        onChange={(e) => setEditType(e.target.value)}
                        className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-2 py-1 text-xs text-[var(--text-primary)]"
                      >
                        <option value="image">Image</option>
                        <option value="video">Video</option>
                        <option value="audio">Audio</option>
                        <option value="album_art">Album Art</option>
                        <option value="promo_photo">Promo Photo</option>
                        <option value="logo">Logo</option>
                      </select>
                      <div className="flex gap-2">
                        <button onClick={() => handleEditSave(asset.id)} className="rounded px-2 py-1 text-xs font-medium bg-indigo-600 text-white hover:opacity-90">Save</button>
                        <button onClick={() => setEditingId(null)} className="rounded px-2 py-1 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]">Cancel</button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="flex items-start justify-between">
                        <div className="min-w-0">
                          <h3 className="text-sm font-medium text-[var(--text-primary)] truncate">{asset.name}</h3>
                          {asset.description && (
                            <p className="text-xs text-[var(--text-secondary)] line-clamp-2 mt-0.5">{asset.description}</p>
                          )}
                        </div>
                        <div className="flex gap-1 shrink-0 ml-2">
                          {(() => {
                            const src = sourceLabels[asset.source] || sourceLabels.uploaded;
                            return (
                              <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${src.color}`}>
                                {src.label}
                              </span>
                            );
                          })()}
                        </div>
                      </div>

                      {/* Tags */}
                      {asset.tags.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                          {asset.tags.map((tag) => (
                            <span key={tag} className="rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] text-indigo-600">
                              {tag}
                            </span>
                          ))}
                        </div>
                      )}

                      {/* Meta */}
                      <div className="flex items-center gap-2 text-[10px] text-[var(--text-secondary)]">
                        <span className="capitalize">{asset.asset_type.replace("_", " ")}</span>
                        {asset.file_size_bytes && <span>{formatBytes(asset.file_size_bytes)}</span>}
                        <span>{new Date(asset.created_at).toLocaleDateString()}</span>
                      </div>

                      {/* Actions */}
                      <div className="flex gap-2 pt-1">
                        <button
                          onClick={() => downloadFile(asset.file_url, asset.name)}
                          className="rounded-lg bg-indigo-600/10 px-3 py-1 text-xs font-medium text-indigo-600 hover:bg-indigo-600/20 transition-colors"
                        >
                          Download
                        </button>
                        <button
                          onClick={() => startEdit(asset)}
                          className="rounded-lg bg-[var(--bg-primary)] px-3 py-1 text-xs font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] border border-[var(--border-color)] transition-colors"
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => handleDelete(asset.id)}
                          disabled={deletingId === asset.id}
                          className="rounded-lg px-3 py-1 text-xs font-medium bg-red-50 text-red-600 hover:bg-red-100 disabled:opacity-50 transition-colors"
                        >
                          {deletingId === asset.id ? "..." : "Delete"}
                        </button>
                      </div>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
