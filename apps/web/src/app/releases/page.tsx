"use client";

import { useState, useEffect, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { apiGet, apiPost, apiPut, apiDelete } from "@/lib/api";
import { LoadingOverlay } from "@/components/ui/spinner";

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
        <p className="text-[var(--text-secondary)]">Manage your music releases.</p>
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
              <div
                key={r.id}
                className="flex items-center gap-4 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] px-5 py-4"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-[var(--text-primary)]">{r.title}</span>
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${typeColors[r.release_type] || "bg-gray-100 text-gray-500"}`}>
                      {r.release_type}
                    </span>
                    <span className="rounded-full bg-gray-500/20 px-2 py-0.5 text-[10px] text-gray-400">
                      {r.status}
                    </span>
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
            ))}
          </div>
        )}
      </div>
    </>
  );
}
