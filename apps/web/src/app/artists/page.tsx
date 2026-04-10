"use client";

import { useState, useEffect, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { apiGet, apiPost, apiPut, apiDelete } from "@/lib/api";
import { cn } from "@/lib/utils";
import { LoadingOverlay, ButtonSpinner } from "@/components/ui/spinner";

interface Artist {
  id: string;
  name: string;
  slug: string;
  bio: string;
  genre: string;
  image_url: string | null;
  spotify_id: string | null;
  apple_music_url: string | null;
  spotify_artist_url: string | null;
  social_links: Record<string, string>;
  created_at: string;
}

export default function ArtistsPage() {
  const [artists, setArtists] = useState<Artist[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formData, setFormData] = useState({ name: "", bio: "", genre: "", apple_music_url: "", spotify_artist_url: "" });
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const fetchArtists = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiGet<Artist[]>("/api/v1/artists/");
      setArtists(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load artists");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchArtists();
  }, [fetchArtists]);

  const handleSubmit = async () => {
    if (!formData.name.trim()) return;
    setSaving(true);
    try {
      if (editingId) {
        await apiPut(`/api/v1/artists/${editingId}`, formData);
      } else {
        await apiPost("/api/v1/artists/", formData);
      }
      setShowForm(false);
      setEditingId(null);
      setFormData({ name: "", bio: "", genre: "", apple_music_url: "", spotify_artist_url: "" });
      fetchArtists();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const handleEdit = (artist: Artist) => {
    setEditingId(artist.id);
    setFormData({
      name: artist.name,
      bio: artist.bio,
      genre: artist.genre,
      apple_music_url: artist.apple_music_url || "",
      spotify_artist_url: artist.spotify_artist_url || "",
    });
    setShowForm(true);
  };

  const handleDelete = async (id: string) => {
    setDeletingId(id);
    try {
      await apiDelete(`/api/v1/artists/${id}`);
      fetchArtists();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <>
      <Header title="Artists" />

      {error && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-50 px-4 py-3 text-sm text-red-600 break-words overflow-hidden flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-xs opacity-60 hover:opacity-100">Dismiss</button>
        </div>
      )}

      <div className="mt-8 flex items-center justify-between">
        <p className="text-[var(--text-secondary)]">Manage your artist roster.</p>
        <button
          onClick={() => {
            setEditingId(null);
            setFormData({ name: "", bio: "", genre: "", apple_music_url: "", spotify_artist_url: "" });
            setShowForm(!showForm);
          }}
          className="rounded-lg bg-[var(--brand-gold)] px-4 py-2 font-medium text-white hover:opacity-90 transition-opacity"
        >
          {showForm ? "Cancel" : "Add Artist"}
        </button>
      </div>

      {/* Create/Edit form */}
      {showForm && (
        <div className="mt-4 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-5 space-y-4">
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">
            {editingId ? "Edit Artist" : "New Artist"}
          </h3>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div>
              <label className="text-xs text-[var(--text-secondary)]">Name *</label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                className="mt-1 w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--brand-gold)] focus:outline-none"
                placeholder="Artist name"
              />
            </div>
            <div>
              <label className="text-xs text-[var(--text-secondary)]">Genre</label>
              <input
                type="text"
                value={formData.genre}
                onChange={(e) => setFormData({ ...formData, genre: e.target.value })}
                className="mt-1 w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--brand-gold)] focus:outline-none"
                placeholder="e.g. electronic, hip-hop"
              />
            </div>
            <div>
              <label className="text-xs text-[var(--text-secondary)]">Bio</label>
              <input
                type="text"
                value={formData.bio}
                onChange={(e) => setFormData({ ...formData, bio: e.target.value })}
                className="mt-1 w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--brand-gold)] focus:outline-none"
                placeholder="Short bio"
              />
            </div>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label className="text-xs text-[var(--text-secondary)]">Apple Music Artist Page</label>
              <input
                type="url"
                value={formData.apple_music_url}
                onChange={(e) => setFormData({ ...formData, apple_music_url: e.target.value })}
                className="mt-1 w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--brand-gold)] focus:outline-none"
                placeholder="https://music.apple.com/us/artist/..."
              />
            </div>
            <div>
              <label className="text-xs text-[var(--text-secondary)]">Spotify Artist Page</label>
              <input
                type="url"
                value={formData.spotify_artist_url}
                onChange={(e) => setFormData({ ...formData, spotify_artist_url: e.target.value })}
                className="mt-1 w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--brand-gold)] focus:outline-none"
                placeholder="https://open.spotify.com/artist/..."
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
              disabled={saving}
              className="rounded-lg bg-[var(--brand-gold)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              {saving ? <ButtonSpinner label={editingId ? "Saving..." : "Creating..."} /> : editingId ? "Save Changes" : "Create Artist"}
            </button>
          </div>
        </div>
      )}

      {/* Artist list */}
      <div className="mt-6">
        {loading ? (
          <LoadingOverlay text="Loading artists..." />
        ) : artists.length === 0 ? (
          <div className="rounded-xl border border-dashed border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
            No artists yet. Add your first artist to get started.
          </div>
        ) : (
          <div className="space-y-2">
            {artists.map((artist) => (
              <div
                key={artist.id}
                className="flex items-center gap-4 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] px-5 py-4"
              >
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[var(--brand-gold)]/10 text-lg font-bold text-[var(--brand-gold)] shrink-0">
                  {artist.name.charAt(0).toUpperCase()}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-[var(--text-primary)]">{artist.name}</span>
                    {artist.genre && (
                      <span className="rounded bg-[var(--bg-primary)] px-1.5 py-0.5 text-[10px] text-[var(--text-secondary)]">
                        {artist.genre}
                      </span>
                    )}
                  </div>
                  {artist.bio && (
                    <p className="text-xs text-[var(--text-secondary)] truncate">{artist.bio}</p>
                  )}
                  {(artist.apple_music_url || artist.spotify_artist_url) && (
                    <div className="flex gap-3 mt-1">
                      {artist.apple_music_url && (
                        <a href={artist.apple_music_url} target="_blank" rel="noopener noreferrer" className="text-[10px] text-pink-500 hover:underline">Apple Music</a>
                      )}
                      {artist.spotify_artist_url && (
                        <a href={artist.spotify_artist_url} target="_blank" rel="noopener noreferrer" className="text-[10px] text-green-500 hover:underline">Spotify</a>
                      )}
                    </div>
                  )}
                </div>
                <div className="flex gap-2 shrink-0">
                  <button
                    onClick={() => handleEdit(artist)}
                    className="text-xs text-[var(--text-secondary)] hover:text-[var(--brand-gold)]"
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => handleDelete(artist.id)}
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
