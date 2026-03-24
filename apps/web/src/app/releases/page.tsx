"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { Header } from "@/components/layout/header";
import { apiGet, apiPost, apiPut, apiDelete, apiUpload } from "@/lib/api";
import { LoadingOverlay, ButtonSpinner } from "@/components/ui/spinner";

interface Artist {
  id: string;
  name: string;
}

interface Release {
  id: string;
  artist_id: string;
  title: string;
  release_type: string;
  status: string;
  release_date: string | null;
  upc: string | null;
  artwork_url: string | null;
  created_at: string;
}

interface Track {
  id: string;
  release_id: string;
  title: string;
  track_number: number;
  duration_seconds: number | null;
  isrc: string | null;
  audio_url: string | null;
  lyrics: string | null;
  is_single: boolean;
}

const typeColors: Record<string, string> = {
  single: "bg-blue-100 text-blue-600",
  ep: "bg-purple-100 text-purple-600",
  album: "bg-[var(--brand-gold)]/20 text-[var(--brand-gold)]",
  compilation: "bg-gray-100 text-gray-500",
};

export default function ReleasesPage() {
  const [releases, setReleases] = useState<Release[]>([]);
  const [artists, setArtists] = useState<Artist[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [expandedRelease, setExpandedRelease] = useState<string | null>(null);
  const [tracks, setTracks] = useState<Record<string, Track[]>>({});
  const [formData, setFormData] = useState({
    artist_id: "",
    title: "",
    release_type: "single",
    release_date: "",
  });

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [rels, arts] = await Promise.all([
        apiGet<Release[]>("/api/v1/releases/"),
        apiGet<Artist[]>("/api/v1/artists/"),
      ]);
      setReleases(rels);
      setArtists(arts);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load releases");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const fetchTracks = async (releaseId: string) => {
    try {
      const trackList = await apiGet<Track[]>(`/api/v1/releases/${releaseId}/tracks/`);
      setTracks((prev) => ({ ...prev, [releaseId]: trackList }));
    } catch {
      // Non-fatal — tracks section just stays empty
    }
  };

  const toggleRelease = (releaseId: string) => {
    if (expandedRelease === releaseId) {
      setExpandedRelease(null);
    } else {
      setExpandedRelease(releaseId);
      if (!tracks[releaseId]) fetchTracks(releaseId);
    }
  };

  const artistMap = Object.fromEntries(artists.map((a) => [a.id, a.name]));

  const handleSubmit = async () => {
    if (!formData.title.trim() || !formData.artist_id) return;
    try {
      const payload: Record<string, unknown> = {
        title: formData.title,
        release_type: formData.release_type,
        artist_id: formData.artist_id,
      };
      if (formData.release_date) payload.release_date = formData.release_date;

      if (editingId) {
        await apiPut(`/api/v1/releases/${editingId}`, payload);
      } else {
        await apiPost("/api/v1/releases/", payload);
      }
      setShowForm(false);
      setEditingId(null);
      setFormData({ artist_id: "", title: "", release_type: "single", release_date: "" });
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  };

  const handleEdit = (r: Release) => {
    setEditingId(r.id);
    setFormData({
      artist_id: r.artist_id,
      title: r.title,
      release_type: r.release_type,
      release_date: r.release_date || "",
    });
    setShowForm(true);
  };

  const handleDelete = async (id: string) => {
    try {
      await apiDelete(`/api/v1/releases/${id}`);
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  return (
    <>
      <Header title="Releases" />

      {error && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-50 px-4 py-3 text-sm text-red-600 break-words overflow-hidden flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-xs opacity-60 hover:opacity-100">Dismiss</button>
        </div>
      )}

      <div className="mt-8 flex items-center justify-between">
        <p className="text-[var(--text-secondary)]">Manage your music releases and tracks.</p>
        <button
          onClick={() => {
            setEditingId(null);
            setFormData({ artist_id: artists[0]?.id || "", title: "", release_type: "single", release_date: "" });
            setShowForm(!showForm);
          }}
          className="rounded-lg bg-[var(--brand-gold)] px-4 py-2 font-medium text-white hover:opacity-90 transition-opacity"
        >
          {showForm ? "Cancel" : "Add Release"}
        </button>
      </div>

      {/* Form */}
      {showForm && (
        <div className="mt-4 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-5 space-y-4">
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">
            {editingId ? "Edit Release" : "New Release"}
          </h3>
          {artists.length === 0 ? (
            <p className="text-sm text-[var(--text-secondary)]">
              Create an artist first before adding a release.
            </p>
          ) : (
            <>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
                <div>
                  <label className="text-xs text-[var(--text-secondary)]">Artist *</label>
                  <select
                    value={formData.artist_id}
                    onChange={(e) => setFormData({ ...formData, artist_id: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--brand-gold)] focus:outline-none"
                  >
                    <option value="">Select artist</option>
                    {artists.map((a) => (
                      <option key={a.id} value={a.id}>{a.name}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-[var(--text-secondary)]">Title *</label>
                  <input
                    type="text"
                    value={formData.title}
                    onChange={(e) => setFormData({ ...formData, title: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--brand-gold)] focus:outline-none"
                    placeholder="Release title"
                  />
                </div>
                <div>
                  <label className="text-xs text-[var(--text-secondary)]">Type</label>
                  <select
                    value={formData.release_type}
                    onChange={(e) => setFormData({ ...formData, release_type: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--brand-gold)] focus:outline-none"
                  >
                    <option value="single">Single</option>
                    <option value="ep">EP</option>
                    <option value="album">Album</option>
                    <option value="compilation">Compilation</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-[var(--text-secondary)]">Release Date</label>
                  <input
                    type="date"
                    value={formData.release_date}
                    onChange={(e) => setFormData({ ...formData, release_date: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--brand-gold)] focus:outline-none"
                  />
                </div>
              </div>
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => { setShowForm(false); setEditingId(null); }}
                  className="rounded-lg px-4 py-2 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSubmit}
                  className="rounded-lg bg-[var(--brand-gold)] px-4 py-2 text-sm font-medium text-white hover:opacity-90"
                >
                  {editingId ? "Save Changes" : "Create Release"}
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {/* List */}
      <div className="mt-6">
        {loading ? (
          <LoadingOverlay text="Loading releases..." />
        ) : releases.length === 0 ? (
          <div className="rounded-xl border border-dashed border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
            No releases yet. Add your first release to get started.
          </div>
        ) : (
          <div className="space-y-2">
            {releases.map((r) => (
              <div key={r.id} className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)]">
                <div className="flex items-center gap-4 px-5 py-4">
                  <button
                    onClick={() => toggleRelease(r.id)}
                    className="text-xs text-[var(--text-secondary)]"
                  >
                    {expandedRelease === r.id ? "▼" : "▶"}
                  </button>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => toggleRelease(r.id)}
                        className="text-sm font-medium text-[var(--text-primary)] hover:text-[var(--brand-gold)] transition-colors"
                      >
                        {r.title}
                      </button>
                      <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${typeColors[r.release_type] || "bg-gray-100 text-gray-500"}`}>
                        {r.release_type}
                      </span>
                      <span className="rounded-full bg-gray-500/20 px-2 py-0.5 text-[10px] text-gray-400">
                        {r.status}
                      </span>
                      {tracks[r.id] && (
                        <span className="text-[10px] text-[var(--text-secondary)]">
                          {tracks[r.id].length} track{tracks[r.id].length !== 1 ? "s" : ""}
                          {" · "}
                          {tracks[r.id].filter((t) => t.audio_url).length} with audio
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-[var(--text-secondary)]">
                      {artistMap[r.artist_id] || "Unknown artist"}
                      {r.release_date && ` \u00b7 ${r.release_date}`}
                    </p>
                  </div>
                  <div className="flex gap-2 shrink-0">
                    <button
                      onClick={() => handleEdit(r)}
                      className="text-xs text-[var(--text-secondary)] hover:text-[var(--brand-gold)]"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => handleDelete(r.id)}
                      className="text-xs text-red-600/60 hover:text-red-600"
                    >
                      Delete
                    </button>
                  </div>
                </div>

                {/* Expanded track list */}
                {expandedRelease === r.id && (
                  <div className="border-t border-[var(--border-color)] px-5 py-4">
                    <TrackList
                      releaseId={r.id}
                      tracks={tracks[r.id] || []}
                      onRefresh={() => fetchTracks(r.id)}
                      onError={(msg) => setError(msg)}
                    />
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}

// ── Track List Component ──────────────────────────────────────────

function TrackList({
  releaseId,
  tracks,
  onRefresh,
  onError,
}: {
  releaseId: string;
  tracks: Track[];
  onRefresh: () => void;
  onError: (msg: string) => void;
}) {
  const [showAddTrack, setShowAddTrack] = useState(false);
  const [newTrack, setNewTrack] = useState({ title: "", track_number: tracks.length + 1, isrc: "" });
  const [saving, setSaving] = useState(false);
  const [uploadingTrackId, setUploadingTrackId] = useState<string | null>(null);
  const audioInputRef = useRef<HTMLInputElement>(null);
  const [pendingTrackId, setPendingTrackId] = useState<string | null>(null);

  const handleAddTrack = async () => {
    if (!newTrack.title.trim()) return;
    setSaving(true);
    try {
      await apiPost(`/api/v1/releases/${releaseId}/tracks/`, {
        title: newTrack.title,
        track_number: newTrack.track_number,
        isrc: newTrack.isrc || null,
      });
      setShowAddTrack(false);
      setNewTrack({ title: "", track_number: tracks.length + 2, isrc: "" });
      onRefresh();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to add track");
    } finally {
      setSaving(false);
    }
  };

  const handleUploadAudio = async (trackId: string, file: File) => {
    setUploadingTrackId(trackId);
    try {
      const result = await apiUpload<{ url: string }>("/api/v1/media/upload", file);
      await apiPut(`/api/v1/releases/${releaseId}/tracks/${trackId}`, {
        audio_url: result.url,
      });
      onRefresh();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Audio upload failed");
    } finally {
      setUploadingTrackId(null);
    }
  };

  const handleDeleteTrack = async (trackId: string) => {
    try {
      await apiDelete(`/api/v1/releases/${releaseId}/tracks/${trackId}`);
      onRefresh();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to delete track");
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-xs font-semibold text-[var(--text-secondary)] uppercase tracking-wider">
          Tracks
        </h4>
        <button
          onClick={() => {
            setNewTrack({ title: "", track_number: tracks.length + 1, isrc: "" });
            setShowAddTrack(!showAddTrack);
          }}
          className="text-xs text-[var(--brand-gold)] hover:underline"
        >
          {showAddTrack ? "Cancel" : "+ Add Track"}
        </button>
      </div>

      {/* Hidden file input for audio upload */}
      <input
        ref={audioInputRef}
        type="file"
        accept="audio/*"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file && pendingTrackId) {
            handleUploadAudio(pendingTrackId, file);
          }
          if (audioInputRef.current) audioInputRef.current.value = "";
          setPendingTrackId(null);
        }}
      />

      {/* Add track form */}
      {showAddTrack && (
        <div className="mb-3 flex gap-2 items-end">
          <div className="w-16">
            <label className="text-[10px] text-[var(--text-secondary)]">#</label>
            <input
              type="number"
              min={1}
              value={newTrack.track_number}
              onChange={(e) => setNewTrack({ ...newTrack, track_number: parseInt(e.target.value) || 1 })}
              className="mt-0.5 w-full rounded border border-[var(--border-color)] bg-[var(--bg-primary)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
            />
          </div>
          <div className="flex-1">
            <label className="text-[10px] text-[var(--text-secondary)]">Title *</label>
            <input
              type="text"
              value={newTrack.title}
              onChange={(e) => setNewTrack({ ...newTrack, title: e.target.value })}
              placeholder="Track title"
              className="mt-0.5 w-full rounded border border-[var(--border-color)] bg-[var(--bg-primary)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
            />
          </div>
          <div className="w-32">
            <label className="text-[10px] text-[var(--text-secondary)]">ISRC</label>
            <input
              type="text"
              value={newTrack.isrc}
              onChange={(e) => setNewTrack({ ...newTrack, isrc: e.target.value })}
              placeholder="Optional"
              className="mt-0.5 w-full rounded border border-[var(--border-color)] bg-[var(--bg-primary)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
            />
          </div>
          <button
            onClick={handleAddTrack}
            disabled={saving || !newTrack.title.trim()}
            className="rounded bg-[var(--brand-gold)] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {saving ? <ButtonSpinner label="Adding..." /> : "Add"}
          </button>
        </div>
      )}

      {/* Track list */}
      {tracks.length === 0 ? (
        <p className="text-xs text-[var(--text-secondary)] italic">No tracks yet. Add tracks to this release.</p>
      ) : (
        <div className="space-y-1.5">
          {tracks.map((track) => (
            <div
              key={track.id}
              className="flex items-center gap-3 rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2"
            >
              <span className="w-6 text-center text-xs text-[var(--text-secondary)] font-mono">
                {track.track_number}
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-sm text-[var(--text-primary)] truncate">{track.title}</p>
                <div className="flex items-center gap-2 text-[10px] text-[var(--text-secondary)]">
                  {track.isrc && <span>ISRC: {track.isrc}</span>}
                  {track.duration_seconds && (
                    <span>{Math.floor(track.duration_seconds / 60)}:{(track.duration_seconds % 60).toString().padStart(2, "0")}</span>
                  )}
                  {track.is_single && (
                    <span className="rounded bg-blue-100 px-1 py-0.5 text-blue-600">Single</span>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {track.audio_url ? (
                  <span className="inline-flex items-center gap-1 rounded bg-green-50 px-2 py-0.5 text-[10px] text-green-600">
                    <span>🎵</span> Audio uploaded
                  </span>
                ) : uploadingTrackId === track.id ? (
                  <ButtonSpinner label="Uploading..." />
                ) : (
                  <button
                    onClick={() => {
                      setPendingTrackId(track.id);
                      audioInputRef.current?.click();
                    }}
                    className="rounded border border-dashed border-[var(--border-color)] px-2 py-0.5 text-[10px] text-[var(--text-secondary)] hover:border-[var(--brand-gold)] hover:text-[var(--brand-gold)] transition-colors"
                  >
                    + Upload Audio
                  </button>
                )}
                {track.audio_url && (
                  <button
                    onClick={() => {
                      setPendingTrackId(track.id);
                      audioInputRef.current?.click();
                    }}
                    className="text-[10px] text-[var(--text-secondary)] hover:text-[var(--brand-gold)]"
                  >
                    Replace
                  </button>
                )}
                <button
                  onClick={() => handleDeleteTrack(track.id)}
                  className="text-[10px] text-red-600/60 hover:text-red-600"
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
