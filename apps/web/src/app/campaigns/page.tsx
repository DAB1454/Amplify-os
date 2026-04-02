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

interface Channel {
  id: string;
  platform: string;
  display_name: string | null;
  is_active: boolean;
  artist_id: string;
}

interface CampaignConfig {
  channels?: string[];
  posts_per_day?: number;
  focus?: string;
  genre?: string;
  content_notes?: string;
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
  config: CampaignConfig;
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
  const [channels, setChannels] = useState<Channel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formStep, setFormStep] = useState<1 | 2>(1);
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
  const [configData, setConfigData] = useState<CampaignConfig>({
    channels: [],
    posts_per_day: 2,
    focus: "engagement",
    genre: "",
    content_notes: "",
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
      const [camps, arts, rels, chans] = await Promise.all([
        apiGet<Campaign[]>(`/api/v1/campaigns/${statusParam}`),
        apiGet<Artist[]>("/api/v1/artists/"),
        apiGet<Release[]>("/api/v1/releases/"),
        apiGet<Channel[]>("/api/v1/channels").catch(() => [] as Channel[]),
      ]);
      setCampaigns(camps);
      setArtists(arts);
      setReleases(rels);
      setChannels(chans);
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

      // Save campaign config (channels, posts_per_day, focus, genre, content_notes)
      const config: CampaignConfig = {};
      if (configData.channels && configData.channels.length > 0) config.channels = configData.channels;
      if (configData.posts_per_day) config.posts_per_day = configData.posts_per_day;
      if (configData.focus) config.focus = configData.focus;
      if (configData.genre) config.genre = configData.genre;
      if (configData.content_notes) config.content_notes = configData.content_notes;
      payload.config = config;

      let campaignId = editingId;

      if (editingId) {
        await apiPut(`/api/v1/campaigns/${editingId}`, payload);
      } else {
        const created = await apiPost<Campaign>("/api/v1/campaigns/", payload);
        campaignId = created.id;
      }

      setShowForm(false);
      setEditingId(null);
      setFormStep(1);
      setFormData({ artist_id: "", release_id: "", name: "", phase: "pre_release", start_date: "", end_date: "", mode: "ai_plan" });
      setConfigData({ channels: [], posts_per_day: 2, focus: "engagement", genre: "", content_notes: "" });

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
    setConfigData({
      channels: c.config?.channels || [],
      posts_per_day: c.config?.posts_per_day || 2,
      focus: c.config?.focus || "engagement",
      genre: c.config?.genre || "",
      content_notes: c.config?.content_notes || "",
    });
    setFormStep(1);
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
      }>("/api/v1/ai/generate-plan", { campaign_id: campaignId, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone });
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
            setConfigData({ channels: [], posts_per_day: 2, focus: "engagement", genre: "", content_notes: "" });
            setFormStep(1);
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
          {/* Step indicator */}
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-[var(--text-primary)]">
              {editingId ? "Edit Campaign" : "New Campaign"}
            </h3>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setFormStep(1)}
                className={cn(
                  "flex items-center gap-1 rounded-full px-3 py-1 text-xs font-medium transition-colors",
                  formStep === 1 ? "bg-indigo-100 text-indigo-700" : "bg-[var(--bg-primary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                )}
              >
                <span className="flex h-4 w-4 items-center justify-center rounded-full bg-current/20 text-[10px]">1</span>
                Basics
              </button>
              <span className="text-[var(--text-secondary)]">&rarr;</span>
              <button
                onClick={() => { if (formData.name.trim() && formData.artist_id) setFormStep(2); }}
                className={cn(
                  "flex items-center gap-1 rounded-full px-3 py-1 text-xs font-medium transition-colors",
                  formStep === 2 ? "bg-indigo-100 text-indigo-700" : "bg-[var(--bg-primary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                )}
              >
                <span className="flex h-4 w-4 items-center justify-center rounded-full bg-current/20 text-[10px]">2</span>
                Strategy
              </button>
            </div>
          </div>

          {artists.length === 0 ? (
            <p className="text-sm text-[var(--text-secondary)]">
              Create an artist first before adding a campaign.
            </p>
          ) : formStep === 1 ? (
            <>
              {/* STEP 1: Basics */}
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
                        {r.title} ({r.release_type}{r.release_date ? ` \u00b7 ${r.release_date}` : ""})
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
                  onClick={() => { setShowForm(false); setEditingId(null); setFormStep(1); }}
                  className="rounded-lg px-4 py-2 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                >
                  Cancel
                </button>
                <button
                  onClick={() => setFormStep(2)}
                  disabled={!formData.name.trim() || !formData.artist_id}
                  className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
                >
                  Next: Strategy &rarr;
                </button>
              </div>
            </>
          ) : (
            <>
              {/* STEP 2: Strategy & Configuration */}

              {/* Channel Selection */}
              <div>
                <label className="text-xs font-medium text-[var(--text-secondary)]">Channels</label>
                <p className="text-[10px] text-[var(--text-secondary)] mb-2">Select which platforms to include in this campaign</p>
                <div className="flex flex-wrap gap-2">
                  {(() => {
                    // Get unique platforms from connected channels (for the selected artist or all)
                    const artistChannels = channels.filter(ch => ch.is_active && (!formData.artist_id || ch.artist_id === formData.artist_id));
                    const platforms = [...new Set(artistChannels.map(ch => ch.platform))];
                    // Also include common platforms even if not connected
                    const allPlatforms = [...new Set([...platforms, "instagram", "tiktok", "youtube", "facebook"])];
                    const platformLabels: Record<string, string> = {
                      instagram: "Instagram", tiktok: "TikTok", youtube: "YouTube",
                      facebook: "Facebook", twitter: "X / Twitter",
                    };
                    const platformEmoji: Record<string, string> = {
                      instagram: "\uD83D\uDCF8", tiktok: "\uD83C\uDFB5", youtube: "\u25B6\uFE0F",
                      facebook: "\uD83D\uDCD8", twitter: "\uD835\uDD4F",
                    };
                    return allPlatforms.map((p) => {
                      const selected = (configData.channels || []).includes(p);
                      const connected = platforms.includes(p);
                      return (
                        <button
                          key={p}
                          type="button"
                          onClick={() => {
                            const current = configData.channels || [];
                            setConfigData({
                              ...configData,
                              channels: selected ? current.filter(c => c !== p) : [...current, p],
                            });
                          }}
                          className={cn(
                            "flex items-center gap-1.5 rounded-lg border px-3 py-2 text-sm transition-all",
                            selected
                              ? "border-indigo-500 bg-indigo-50 text-indigo-700 ring-1 ring-indigo-500"
                              : "border-[var(--border-color)] bg-[var(--bg-primary)] text-[var(--text-secondary)] hover:border-[var(--text-secondary)]",
                            !connected && "opacity-60"
                          )}
                        >
                          <span>{platformEmoji[p] || ""}</span>
                          <span>{platformLabels[p] || p}</span>
                          {!connected && <span className="text-[9px] text-amber-500 ml-1">(not connected)</span>}
                        </button>
                      );
                    });
                  })()}
                </div>
              </div>

              {/* Posts per day + Focus + Genre */}
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                <div>
                  <label className="text-xs text-[var(--text-secondary)]">Posts per Channel per Day</label>
                  <select
                    value={configData.posts_per_day || 2}
                    onChange={(e) => setConfigData({ ...configData, posts_per_day: parseInt(e.target.value) })}
                    className="mt-1 w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--brand-gold)] focus:outline-none"
                  >
                    <option value={1}>1 post/day (minimal)</option>
                    <option value={2}>2 posts/day (recommended)</option>
                    <option value={3}>3 posts/day (aggressive)</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-[var(--text-secondary)]">Campaign Focus</label>
                  <select
                    value={configData.focus || "engagement"}
                    onChange={(e) => setConfigData({ ...configData, focus: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--brand-gold)] focus:outline-none"
                  >
                    <option value="engagement">Engagement (likes, comments, shares)</option>
                    <option value="awareness">Awareness (reach, impressions)</option>
                    <option value="conversions">Conversions (streams, pre-saves, link clicks)</option>
                    <option value="community">Community (fans, followers, interaction)</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-[var(--text-secondary)]">Genre</label>
                  <select
                    value={configData.genre || ""}
                    onChange={(e) => setConfigData({ ...configData, genre: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--brand-gold)] focus:outline-none"
                  >
                    <option value="">Auto-detect</option>
                    <option value="country">Country</option>
                    <option value="hip-hop">Hip-Hop / Rap</option>
                    <option value="pop">Pop</option>
                    <option value="rock">Rock</option>
                    <option value="indie">Indie</option>
                    <option value="r&b">R&B / Soul</option>
                    <option value="electronic">Electronic / EDM</option>
                    <option value="latin">Latin</option>
                    <option value="folk">Folk / Americana</option>
                    <option value="metal">Metal</option>
                    <option value="jazz">Jazz</option>
                    <option value="classical">Classical</option>
                  </select>
                </div>
              </div>

              {/* Content Notes */}
              <div>
                <label className="text-xs text-[var(--text-secondary)]">Content Notes</label>
                <p className="text-[10px] text-[var(--text-secondary)] mb-1">Any special instructions for the AI planner (optional)</p>
                <textarea
                  value={configData.content_notes || ""}
                  onChange={(e) => setConfigData({ ...configData, content_notes: e.target.value })}
                  rows={2}
                  className="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--brand-gold)] focus:outline-none resize-none"
                  placeholder="e.g. &quot;AI-generated music, no personal photos available&quot; or &quot;Focus on track #3 as the lead single&quot;"
                />
              </div>

              {/* Summary */}
              <div className="rounded-lg bg-indigo-50 border border-indigo-200 px-4 py-3">
                <div className="text-xs font-medium text-indigo-700 mb-1">Campaign Summary</div>
                <div className="text-xs text-indigo-600 space-y-0.5">
                  <div><span className="font-medium">{formData.name}</span> &middot; {formData.mode === "ai_plan" ? "AI Plan" : formData.mode === "autopilot" ? "Autopilot" : "Manual"}</div>
                  <div>{(configData.channels || []).length > 0 ? (configData.channels || []).join(", ") : "All connected channels"} &middot; {configData.posts_per_day || 2} post(s)/day</div>
                  {formData.start_date && <div>{formData.start_date}{formData.end_date ? ` \u2013 ${formData.end_date}` : ""}</div>}
                </div>
              </div>

              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setFormStep(1)}
                  className="rounded-lg px-4 py-2 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                >
                  &larr; Back
                </button>
                <button
                  onClick={() => { setShowForm(false); setEditingId(null); setFormStep(1); }}
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
