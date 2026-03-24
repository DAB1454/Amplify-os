"use client";

import { useState, useEffect, useCallback } from "react";
import { Header } from "@/components/layout/header";
import { apiGet, apiPost, apiPut, apiDelete } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Artist {
  id: string;
  name: string;
}

interface Campaign {
  id: string;
  artist_id: string;
  release_id: string | null;
  name: string;
  status: string;
  phase: string;
  start_date: string | null;
  end_date: string | null;
  budget: number | null;
  created_at: string;
}

const tabs = ["All", "Active", "Draft", "Paused", "Completed"] as const;

const statusColors: Record<string, string> = {
  draft: "bg-gray-100 text-gray-500",
  active: "bg-green-100 text-green-600",
  paused: "bg-yellow-100 text-yellow-600",
  completed: "bg-blue-100 text-blue-600",
};

export default function CampaignsPage() {
  const [activeTab, setActiveTab] = useState<(typeof tabs)[number]>("All");
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [artists, setArtists] = useState<Artist[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formData, setFormData] = useState({
    artist_id: "",
    name: "",
    phase: "pre_release",
    start_date: "",
    end_date: "",
  });

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const statusParam = activeTab === "All" ? "" : `?status=${activeTab.toLowerCase()}`;
      const [camps, arts] = await Promise.all([
        apiGet<Campaign[]>(`/api/v1/campaigns/${statusParam}`),
        apiGet<Artist[]>("/api/v1/artists/"),
      ]);
      setCampaigns(camps);
      setArtists(arts);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load campaigns");
    } finally {
      setLoading(false);
    }
  }, [activeTab]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const artistMap = Object.fromEntries(artists.map((a) => [a.id, a.name]));

  const handleSubmit = async () => {
    if (!formData.name.trim() || !formData.artist_id) return;
    try {
      const payload: Record<string, unknown> = {
        artist_id: formData.artist_id,
        name: formData.name,
        phase: formData.phase,
      };
      if (formData.start_date) payload.start_date = formData.start_date;
      if (formData.end_date) payload.end_date = formData.end_date;

      if (editingId) {
        await apiPut(`/api/v1/campaigns/${editingId}`, payload);
      } else {
        await apiPost("/api/v1/campaigns/", payload);
      }
      setShowForm(false);
      setEditingId(null);
      setFormData({ artist_id: "", name: "", phase: "pre_release", start_date: "", end_date: "" });
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  };

  const handleAction = async (id: string, action: "launch" | "pause") => {
    try {
      await apiPost(`/api/v1/campaigns/${id}/${action}`, {});
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : `${action} failed`);
    }
  };

  const handleEdit = (c: Campaign) => {
    setEditingId(c.id);
    setFormData({
      artist_id: c.artist_id,
      name: c.name,
      phase: c.phase,
      start_date: c.start_date || "",
      end_date: c.end_date || "",
    });
    setShowForm(true);
  };

  const handleDelete = async (id: string) => {
    try {
      await apiDelete(`/api/v1/campaigns/${id}`);
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  return (
    <>
      <Header title="Campaigns" />

      {error && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-50 px-4 py-3 text-sm text-red-600 flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-xs opacity-60 hover:opacity-100">Dismiss</button>
        </div>
      )}

      <div className="mt-8 flex items-center justify-between">
        <div className="flex gap-2">
          {tabs.map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={cn(
                "rounded-lg px-4 py-2 text-sm font-medium transition-colors",
                activeTab === tab
                  ? "bg-[var(--brand-gold)] text-white"
                  : "bg-[var(--bg-surface)] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)]"
              )}
            >
              {tab}
            </button>
          ))}
        </div>
        <button
          onClick={() => {
            setEditingId(null);
            setFormData({ artist_id: artists[0]?.id || "", name: "", phase: "pre_release", start_date: "", end_date: "" });
            setShowForm(!showForm);
          }}
          className="rounded-lg bg-[var(--brand-gold)] px-4 py-2 font-medium text-white hover:opacity-90 transition-opacity"
        >
          {showForm ? "Cancel" : "New Campaign"}
        </button>
      </div>

      {/* Form */}
      {showForm && (
        <div className="mt-4 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-5 space-y-4">
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">
            {editingId ? "Edit Campaign" : "New Campaign"}
          </h3>
          {artists.length === 0 ? (
            <p className="text-sm text-[var(--text-secondary)]">
              Create an artist first before adding a campaign.
            </p>
          ) : (
            <>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-5">
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
                  <label className="text-xs text-[var(--text-secondary)]">Name *</label>
                  <input
                    type="text"
                    value={formData.name}
                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--brand-gold)] focus:outline-none"
                    placeholder="Campaign name"
                  />
                </div>
                <div>
                  <label className="text-xs text-[var(--text-secondary)]">Phase</label>
                  <select
                    value={formData.phase}
                    onChange={(e) => setFormData({ ...formData, phase: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--brand-gold)] focus:outline-none"
                  >
                    <option value="pre_release">Pre-Release</option>
                    <option value="release_week">Release Week</option>
                    <option value="sustain">Sustain</option>
                    <option value="evergreen">Evergreen</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-[var(--text-secondary)]">Start Date</label>
                  <input
                    type="date"
                    value={formData.start_date}
                    onChange={(e) => setFormData({ ...formData, start_date: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--brand-gold)] focus:outline-none"
                  />
                </div>
                <div>
                  <label className="text-xs text-[var(--text-secondary)]">End Date</label>
                  <input
                    type="date"
                    value={formData.end_date}
                    onChange={(e) => setFormData({ ...formData, end_date: e.target.value })}
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
                  {editingId ? "Save Changes" : "Create Campaign"}
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {/* List */}
      <div className="mt-6">
        {loading ? (
          <div className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
            Loading campaigns...
          </div>
        ) : campaigns.length === 0 ? (
          <div className="rounded-xl border border-dashed border-[var(--border-color)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-secondary)]">
            No {activeTab.toLowerCase() !== "all" ? activeTab.toLowerCase() + " " : ""}campaigns yet.
          </div>
        ) : (
          <div className="space-y-2">
            {campaigns.map((c) => (
              <div
                key={c.id}
                className="flex items-center gap-4 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] px-5 py-4"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-[var(--text-primary)]">{c.name}</span>
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${statusColors[c.status] || "bg-gray-100 text-gray-500"}`}>
                      {c.status}
                    </span>
                    <span className="rounded bg-[var(--bg-primary)] px-1.5 py-0.5 text-[10px] text-[var(--text-secondary)]">
                      {c.phase.replace("_", " ")}
                    </span>
                  </div>
                  <p className="text-xs text-[var(--text-secondary)]">
                    {artistMap[c.artist_id] || "Unknown artist"}
                    {c.start_date && ` \u00b7 ${c.start_date}`}
                    {c.end_date && ` \u2013 ${c.end_date}`}
                  </p>
                </div>
                <div className="flex gap-2 shrink-0">
                  {c.status === "draft" && (
                    <button
                      onClick={() => handleAction(c.id, "launch")}
                      className="rounded-lg bg-green-600 px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
                    >
                      Launch
                    </button>
                  )}
                  {c.status === "active" && (
                    <button
                      onClick={() => handleAction(c.id, "pause")}
                      className="rounded-lg bg-yellow-600/20 px-3 py-1.5 text-xs font-medium text-yellow-600 hover:opacity-90"
                    >
                      Pause
                    </button>
                  )}
                  {c.status === "paused" && (
                    <button
                      onClick={() => handleAction(c.id, "launch")}
                      className="rounded-lg bg-green-600/20 px-3 py-1.5 text-xs font-medium text-green-600 hover:opacity-90"
                    >
                      Resume
                    </button>
                  )}
                  <button
                    onClick={() => handleEdit(c)}
                    className="text-xs text-[var(--text-secondary)] hover:text-[var(--brand-gold)]"
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => handleDelete(c.id)}
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
