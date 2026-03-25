"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { Header } from "@/components/layout/header";
import { apiGet, apiPost, apiPut, apiDelete, getAccessToken } from "@/lib/api";
import { cn } from "@/lib/utils";
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
  release_date: string | null;
}

interface Campaign {
  id: string;
  artist_id: string;
  release_id: string | null;
  name: string;
  status: string;
  mode: string;
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
  const [releases, setReleases] = useState<Release[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const router = useRouter();
  const [formData, setFormData] = useState({
    artist_id: "",
    release_id: "",
    name: "",
    phase: "pre_release",
    start_date: "",
    end_date: "",
    mode: "ai_plan",
  });
  const [creating, setCreating] = useState(false);
  const [generatingPlanId, setGeneratingPlanId] = useState<string | null>(null);
  const [planResult, setPlanResult] = useState<{
    campaign_id: string;
    daily_actions: number;
    calendar_items_created: number;
    draft_posts_created: number;
    notes: string;
  } | null>(null);
  const [importingCampaignId, setImportingCampaignId] = useState<string | null>(null);
  const csvInputRef = useRef<HTMLInputElement>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const statusParam = activeTab === "All" ? "" : `?status=${activeTab.toLowerCase()}`;
      const [camps, arts, rels] = await Promise.all([
        apiGet<Campaign[]>(`/api/v1/campaigns/${statusParam}`),
        apiGet<Artist[]>("/api/v1/artists/"),
        apiGet<Release[]>("/api/v1/releases/"),
      ]);
      setCampaigns(camps);
      setArtists(arts);
      setReleases(rels);
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
  const releaseMap = Object.fromEntries(releases.map((r) => [r.id, r.title]));
  const artistReleases = releases.filter((r) => r.artist_id === formData.artist_id);

  const handleSubmit = async (generatePlan = false) => {
    if (!formData.name.trim() || !formData.artist_id) return;
    setCreating(true);
    setError(null);
    const selectedMode = formData.mode;
    try {
      const payload: Record<string, unknown> = {
        artist_id: formData.artist_id,
        name: formData.name,
        phase: formData.phase,
      };
      if (formData.release_id) payload.release_id = formData.release_id;
      if (formData.start_date) payload.start_date = formData.start_date;
      if (formData.end_date) payload.end_date = formData.end_date;
      payload.mode = selectedMode;

      let campaignId = editingId;

      if (editingId) {
        await apiPut(`/api/v1/campaigns/${editingId}`, payload);
      } else {
        const created = await apiPost<Campaign>("/api/v1/campaigns/", payload);
        campaignId = created.id;
      }

      setShowForm(false);
      setEditingId(null);
      setFormData({ artist_id: "", release_id: "", name: "", phase: "pre_release", start_date: "", end_date: "", mode: "ai_plan" });

      // For AI modes, redirect to detail page with auto-generate flag
      if (generatePlan && selectedMode !== "manual" && campaignId) {
        router.push(`/campaigns/${campaignId}?generate=true`);
        return;
      }

      await fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setCreating(false);
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
      release_id: c.release_id || "",
      name: c.name,
      phase: c.phase,
      start_date: c.start_date || "",
      end_date: c.end_date || "",
      mode: c.mode || "ai_plan",
    });
    setShowForm(true);
  };

  const handleGeneratePlan = async (campaignId: string) => {
    setGeneratingPlanId(campaignId);
    setPlanResult(null);
    setError(null);
    try {
      const result = await apiPost<{
        campaign_name: string;
        daily_actions: { day: string; platform: string; action_type: string; content_brief: string }[];
        calendar_items_created: number;
        draft_posts_created: number;
        notes: string;
      }>("/api/v1/ai/generate-plan", { campaign_id: campaignId });
      setPlanResult({
        campaign_id: campaignId,
        daily_actions: result.daily_actions?.length || 0,
        calendar_items_created: result.calendar_items_created,
        draft_posts_created: result.draft_posts_created,
        notes: result.notes,
      });
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Plan generation failed");
    } finally {
      setGeneratingPlanId(null);
    }
  };

  const handleImportCSV = async (campaignId: string, file: File) => {
    setImportingCampaignId(campaignId);
    setError(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("campaign_id", campaignId);
      const token = getAccessToken();
      const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${apiBase}/api/v1/calendar/import`, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({ detail: "CSV import failed" }));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }
      const result = await res.json() as { imported: number; skipped: number; errors: string[] };
      setPlanResult({
        campaign_id: campaignId,
        daily_actions: 0,
        calendar_items_created: result.imported || 0,
        draft_posts_created: 0,
        notes: result.skipped ? `${result.skipped} rows skipped` : "CSV imported successfully",
      });
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "CSV import failed");
    } finally {
      setImportingCampaignId(null);
    }
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
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-50 px-4 py-3 text-sm text-red-600 break-words overflow-hidden flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-xs opacity-60 hover:opacity-100">Dismiss</button>
        </div>
      )}

      {planResult && (
        <div className="mt-4 rounded-lg border border-green-500/30 bg-green-50 px-4 py-3 text-sm text-green-700 flex items-center justify-between">
          <span>
            {planResult.daily_actions > 0
              ? `AI plan generated: ${planResult.daily_actions} actions, ${planResult.calendar_items_created} calendar items, ${planResult.draft_posts_created} draft posts created.`
              : `Imported ${planResult.calendar_items_created} calendar items.`}
            {planResult.notes && ` — ${planResult.notes.slice(0, 120)}`}
          </span>
          <button onClick={() => setPlanResult(null)} className="text-xs opacity-60 hover:opacity-100">Dismiss</button>
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
            setFormData({ artist_id: artists[0]?.id || "", release_id: "", name: "", phase: "pre_release", start_date: "", end_date: "", mode: "ai_plan" });
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
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                <div>
                  <label className="text-xs text-[var(--text-secondary)]">Artist *</label>
                  <select
                    value={formData.artist_id}
                    onChange={(e) => setFormData({ ...formData, artist_id: e.target.value, release_id: "" })}
                    className="mt-1 w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--brand-gold)] focus:outline-none"
                  >
                    <option value="">Select artist</option>
                    {artists.map((a) => (
                      <option key={a.id} value={a.id}>{a.name}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-[var(--text-secondary)]">Release</label>
                  <select
                    value={formData.release_id}
                    onChange={(e) => setFormData({ ...formData, release_id: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--brand-gold)] focus:outline-none"
                    disabled={!formData.artist_id}
                  >
                    <option value="">No release (general campaign)</option>
                    {artistReleases.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.title} ({r.release_type}{r.release_date ? ` · ${r.release_date}` : ""})
                      </option>
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
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
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
              {/* Mode Selector */}
              {!editingId && (
                <div>
                  <label className="text-xs text-[var(--text-secondary)]">Campaign Mode</label>
                  <div className="mt-2 grid grid-cols-3 gap-3">
                    {([
                      { value: "manual", label: "Manual", desc: "You create all content" },
                      { value: "ai_plan", label: "AI Plan", desc: "AI creates, you approve" },
                      { value: "autopilot", label: "Autopilot", desc: "AI creates & publishes" },
                    ] as const).map((m) => (
                      <button
                        key={m.value}
                        type="button"
                        onClick={() => setFormData({ ...formData, mode: m.value })}
                        className={cn(
                          "rounded-lg border p-3 text-left transition-all",
                          formData.mode === m.value
                            ? "border-indigo-500 bg-indigo-50 ring-1 ring-indigo-500"
                            : "border-[var(--border-color)] bg-[var(--bg-primary)] hover:border-[var(--text-secondary)]"
                        )}
                      >
                        <div className="text-sm font-medium text-[var(--text-primary)]">{m.label}</div>
                        <div className="text-xs text-[var(--text-secondary)] mt-0.5">{m.desc}</div>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => { setShowForm(false); setEditingId(null); }}
                  className="rounded-lg px-4 py-2 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                >
                  Cancel
                </button>
                {!editingId && formData.mode !== "manual" && (
                  <button
                    onClick={() => handleSubmit(true)}
                    disabled={creating || !formData.name.trim() || !formData.artist_id}
                    className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
                  >
                    {creating ? <ButtonSpinner label="Creating..." /> : `Create + ${formData.mode === "autopilot" ? "Autopilot" : "AI Plan"}`}
                  </button>
                )}
                <button
                  onClick={() => handleSubmit(false)}
                  disabled={creating || !formData.name.trim() || !formData.artist_id}
                  className="rounded-lg bg-[var(--brand-gold)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
                >
                  {creating ? <ButtonSpinner label="Saving..." /> : editingId ? "Save Changes" : "Create Campaign"}
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {/* Hidden CSV input */}
      <input
        ref={csvInputRef}
        type="file"
        accept=".csv"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file && importingCampaignId) {
            handleImportCSV(importingCampaignId, file);
          }
          if (csvInputRef.current) csvInputRef.current.value = "";
        }}
      />

      {/* List */}
      <div className="mt-6">
        {loading ? (
          <LoadingOverlay text="Loading campaigns..." />
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
                    <button onClick={() => router.push(`/campaigns/${c.id}`)} className="text-sm font-medium text-[var(--text-primary)] hover:text-indigo-600 transition-colors text-left">
                      {c.name}
                    </button>
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${statusColors[c.status] || "bg-gray-100 text-gray-500"}`}>
                      {c.status}
                    </span>
                    {c.mode && c.mode !== "manual" && (
                      <span className={cn(
                        "rounded-full px-2 py-0.5 text-[10px] font-medium",
                        c.mode === "ai_plan" ? "bg-indigo-100 text-indigo-600" : "bg-purple-100 text-purple-600"
                      )}>
                        {c.mode === "ai_plan" ? "AI Plan" : "Autopilot"}
                      </span>
                    )}
                    <span className="rounded bg-[var(--bg-primary)] px-1.5 py-0.5 text-[10px] text-[var(--text-secondary)]">
                      {c.phase.replace("_", " ")}
                    </span>
                  </div>
                  <p className="text-xs text-[var(--text-secondary)]">
                    {artistMap[c.artist_id] || "Unknown artist"}
                    {c.release_id && releaseMap[c.release_id] && ` · ${releaseMap[c.release_id]}`}
                    {c.start_date && ` · ${c.start_date}`}
                    {c.end_date && ` – ${c.end_date}`}
                  </p>
                </div>
                <div className="flex gap-2 shrink-0">
                  <button
                    onClick={() => handleGeneratePlan(c.id)}
                    disabled={generatingPlanId === c.id}
                    className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
                  >
                    {generatingPlanId === c.id ? <ButtonSpinner label="Generating..." /> : "AI Plan"}
                  </button>
                  <button
                    onClick={() => {
                      setImportingCampaignId(c.id);
                      csvInputRef.current?.click();
                    }}
                    disabled={importingCampaignId === c.id}
                    className="rounded-lg bg-blue-600/10 px-3 py-1.5 text-xs font-medium text-blue-600 hover:bg-blue-600/20 disabled:opacity-50"
                  >
                    {importingCampaignId === c.id ? <ButtonSpinner label="Importing..." /> : "Import CSV"}
                  </button>
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
