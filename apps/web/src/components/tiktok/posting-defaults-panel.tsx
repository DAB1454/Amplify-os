"use client";

/**
 * Channel-level TikTok posting defaults.
 *
 * Once Direct Post is approved, autopilot and scheduled posts need to
 * carry valid Content Sharing Guidelines disclosure choices when the
 * user isn't pressing a button per-post. This panel lets the user
 * configure those defaults at the channel level. The planner stamps
 * them onto each TikTok post at creation time so the row is
 * self-contained by the time the worker publishes it.
 *
 * Branded Content is INTENTIONALLY not a default — TikTok requires
 * per-post affirmation of paid-partnership disclosure, so posts that
 * the planner ever marks as Branded MUST land in the manual review
 * queue and never auto-publish.
 *
 * Copy on this panel mirrors the per-post modal so the user's
 * agreement here is informed in the same way as a per-post agreement.
 */

import { useEffect, useState } from "react";
import { apiGet, apiPut } from "@/lib/api";
import { Spinner, ButtonSpinner } from "@/components/ui/spinner";

type PrivacyLevel =
  | "PUBLIC_TO_EVERYONE"
  | "MUTUAL_FOLLOW_FRIENDS"
  | "FOLLOWER_OF_CREATOR"
  | "SELF_ONLY";

interface Defaults {
  privacy_level: PrivacyLevel;
  disable_comment: boolean;
  disable_duet: boolean;
  disable_stitch: boolean;
  brand_organic_toggle: boolean;
}

const PRIVACY_OPTIONS: { value: PrivacyLevel; label: string }[] = [
  { value: "SELF_ONLY", label: "Only me (private)" },
  { value: "MUTUAL_FOLLOW_FRIENDS", label: "Friends — accounts you follow back" },
  { value: "FOLLOWER_OF_CREATOR", label: "Followers" },
  { value: "PUBLIC_TO_EVERYONE", label: "Public — anyone on TikTok" },
];

export function TikTokPostingDefaultsPanel({
  channelId,
}: {
  channelId: string;
}) {
  const [defaults, setDefaults] = useState<Defaults | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!expanded) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      setLoadError(null);
      try {
        const data = await apiGet<Defaults>(
          `/api/v1/channels/${channelId}/tiktok-defaults`
        );
        if (!cancelled) setDefaults(data);
      } catch (err) {
        if (!cancelled)
          setLoadError(
            err instanceof Error ? err.message : "Failed to load defaults"
          );
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [expanded, channelId]);

  const onSave = async () => {
    if (!defaults) return;
    setSaving(true);
    setSaveError(null);
    try {
      const updated = await apiPut<Defaults>(
        `/api/v1/channels/${channelId}/tiktok-defaults`,
        defaults
      );
      setDefaults(updated);
      setSavedAt(Date.now());
    } catch (err) {
      setSaveError(
        err instanceof Error ? err.message : "Failed to save defaults"
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mt-2 w-full">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] underline-offset-2 hover:underline"
      >
        {expanded ? "Hide" : "Configure"} TikTok posting defaults
      </button>

      {expanded && (
        <div className="mt-2 rounded-lg border border-gray-200 bg-white p-4 space-y-3">
          <p className="text-xs text-gray-600">
            These defaults apply to scheduled and autopilot TikTok posts.
            They mirror the choices you&apos;d make on the per-post Direct
            Post screen. Branded Content disclosure is{" "}
            <span className="font-medium">not</span> available as a default
            — TikTok requires per-post confirmation for paid partnerships.
          </p>

          {loading && (
            <div className="flex items-center justify-center py-6">
              <Spinner size={20} />
            </div>
          )}

          {loadError && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
              {loadError}
            </div>
          )}

          {defaults && !loading && (
            <>
              <div>
                <label
                  htmlFor={`tt-def-privacy-${channelId}`}
                  className="block text-xs font-medium text-gray-700 mb-1"
                >
                  Default privacy
                </label>
                <select
                  id={`tt-def-privacy-${channelId}`}
                  value={defaults.privacy_level}
                  onChange={(e) =>
                    setDefaults({
                      ...defaults,
                      privacy_level: e.target.value as PrivacyLevel,
                    })
                  }
                  className="w-full rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900"
                >
                  {PRIVACY_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
                <p className="mt-1 text-[10px] text-gray-500">
                  Pick the most restrictive setting you&apos;re comfortable
                  with for unattended posts. You can override per-post on
                  the Posts screen.
                </p>
              </div>

              <div>
                <p className="block text-xs font-medium text-gray-700 mb-1">
                  Default allow
                </p>
                <div className="space-y-1.5">
                  <label className="flex items-center gap-2 text-sm text-gray-700">
                    <input
                      type="checkbox"
                      checked={!defaults.disable_comment}
                      onChange={(e) =>
                        setDefaults({
                          ...defaults,
                          disable_comment: !e.target.checked,
                        })
                      }
                    />
                    Comment
                  </label>
                  <label className="flex items-center gap-2 text-sm text-gray-700">
                    <input
                      type="checkbox"
                      checked={!defaults.disable_duet}
                      onChange={(e) =>
                        setDefaults({
                          ...defaults,
                          disable_duet: !e.target.checked,
                        })
                      }
                    />
                    Duet
                  </label>
                  <label className="flex items-center gap-2 text-sm text-gray-700">
                    <input
                      type="checkbox"
                      checked={!defaults.disable_stitch}
                      onChange={(e) =>
                        setDefaults({
                          ...defaults,
                          disable_stitch: !e.target.checked,
                        })
                      }
                    />
                    Stitch
                  </label>
                </div>
              </div>

              <div className="rounded-md border border-gray-200 bg-gray-50 p-3">
                <label className="flex items-start gap-2 text-sm text-gray-800">
                  <input
                    type="checkbox"
                    checked={defaults.brand_organic_toggle}
                    onChange={(e) =>
                      setDefaults({
                        ...defaults,
                        brand_organic_toggle: e.target.checked,
                      })
                    }
                    className="mt-0.5"
                  />
                  <span>
                    <span className="font-medium">
                      Default disclosure: Your Brand
                    </span>
                    <span className="block text-xs text-gray-500">
                      You are promoting yourself or a business you own.
                      Autopilot posts will be labeled “Promotional content”
                      on TikTok. Leave off if your posts are creative/
                      organic rather than marketing.
                    </span>
                  </span>
                </label>
              </div>

              {saveError && (
                <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
                  {saveError}
                </div>
              )}

              <div className="flex items-center justify-end gap-2">
                {savedAt && Date.now() - savedAt < 4000 && (
                  <span className="text-xs text-green-600">Saved</span>
                )}
                <button
                  type="button"
                  onClick={onSave}
                  disabled={saving}
                  className="rounded-lg bg-black px-3 py-1.5 text-xs font-medium text-white hover:bg-gray-800 disabled:opacity-50"
                >
                  {saving ? <ButtonSpinner label="Saving…" /> : "Save defaults"}
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
