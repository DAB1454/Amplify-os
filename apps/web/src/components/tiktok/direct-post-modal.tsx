"use client";

/**
 * TikTok Direct Post confirmation modal — implements TikTok's Content
 * Sharing Guidelines disclosure UX. Every element on this screen is
 * mandatory per the guidelines; reviewers reject submissions that omit
 * any of them. Do not "simplify" without re-reading the spec at
 * https://developers.tiktok.com/doc/content-sharing-guidelines.
 *
 * Required elements (each one matters for app review):
 *  1. Creator disclosure: "Your video will be posted to TikTok as @<username>"
 *  2. Privacy dropdown sourced from creator_info.privacy_level_options,
 *     defaulting to the most restrictive option present.
 *  3. Allow comment / duet / stitch checkboxes (hidden when the creator
 *     disabled them).
 *  4. "Disclose video content" toggle. When ON: "Your Brand" + "Branded
 *     Content" sub-toggles, with the Branded warning label.
 *  5. Branded Content + SELF_ONLY is invalid — Post button disabled.
 *  6. Read-only caption preview so the user can confirm what's about
 *     to be published.
 *  7. Post button stays disabled until the form is in a valid state.
 */

import { useEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "@/lib/api";
import { Spinner, ButtonSpinner } from "@/components/ui/spinner";

type PrivacyLevel =
  | "PUBLIC_TO_EVERYONE"
  | "MUTUAL_FOLLOW_FRIENDS"
  | "FOLLOWER_OF_CREATOR"
  | "SELF_ONLY";

interface CreatorInfo {
  creator_username: string;
  creator_nickname: string;
  creator_avatar_url: string;
  privacy_level_options: PrivacyLevel[];
  comment_disabled: boolean;
  duet_disabled: boolean;
  stitch_disabled: boolean;
  max_video_post_duration_sec: number;
}

// Ordered most-restrictive → least-restrictive. The default selection is
// the first option from this list that creator_info reports as available.
const RESTRICTIVENESS_ORDER: PrivacyLevel[] = [
  "SELF_ONLY",
  "MUTUAL_FOLLOW_FRIENDS",
  "FOLLOWER_OF_CREATOR",
  "PUBLIC_TO_EVERYONE",
];

const PRIVACY_LABEL: Record<PrivacyLevel, string> = {
  PUBLIC_TO_EVERYONE: "Public — anyone on TikTok",
  MUTUAL_FOLLOW_FRIENDS: "Friends — accounts you follow back",
  FOLLOWER_OF_CREATOR: "Followers",
  SELF_ONLY: "Only me (private)",
};

export interface TikTokDirectPostModalProps {
  postId: string;
  captionPreview: string;
  /**
   * "publish" — submit immediately to /publish.
   * "schedule" — persist disclosure params on the post and call /schedule
   *   with the supplied scheduledAt. Used by the autonomy/scheduling flow
   *   so the user picks disclosure once, at the moment of commitment,
   *   instead of being prompted again at the actual publish time.
   */
  mode?: "publish" | "schedule";
  /** Required when mode="schedule". ISO timestamp. */
  scheduledAt?: string;
  onClose: () => void;
  onPublished: (result: { post_id: string; status?: string }) => void;
}

export function TikTokDirectPostModal({
  postId,
  captionPreview,
  mode = "publish",
  scheduledAt,
  onClose,
  onPublished,
}: TikTokDirectPostModalProps) {
  const [info, setInfo] = useState<CreatorInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // User selections — initialized once creator_info arrives.
  const [privacyLevel, setPrivacyLevel] = useState<PrivacyLevel | "">("");
  const [allowComment, setAllowComment] = useState(true);
  const [allowDuet, setAllowDuet] = useState(true);
  const [allowStitch, setAllowStitch] = useState(true);
  const [discloseToggle, setDiscloseToggle] = useState(false);
  const [yourBrand, setYourBrand] = useState(false);
  const [brandedContent, setBrandedContent] = useState(false);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setLoadError(null);
      try {
        const data = await apiGet<CreatorInfo>(
          `/api/v1/posts/${postId}/tiktok/creator-info`
        );
        if (cancelled) return;
        setInfo(data);
        // Default to the most restrictive available privacy option.
        const defaultPrivacy = RESTRICTIVENESS_ORDER.find((p) =>
          data.privacy_level_options.includes(p)
        );
        if (defaultPrivacy) setPrivacyLevel(defaultPrivacy);
      } catch (err) {
        if (cancelled) return;
        setLoadError(
          err instanceof Error
            ? err.message
            : "Failed to load TikTok creator info"
        );
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [postId]);

  // ESC closes — guideline-compliant: user can always back out before posting.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !submitting) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose, submitting]);

  // Validity gate: every mandatory selection must be valid before the
  // Post button enables. Mirroring TikTok's own UX rules.
  const validity = useMemo(() => {
    if (!info) return { ok: false, reason: "Loading…" };
    if (!privacyLevel) return { ok: false, reason: "Pick a privacy level" };
    if (discloseToggle && !yourBrand && !brandedContent) {
      return {
        ok: false,
        reason:
          "If you disclose video content, pick Your Brand and/or Branded Content",
      };
    }
    if (brandedContent && privacyLevel === "SELF_ONLY") {
      return {
        ok: false,
        reason: "Branded Content can't be set to private",
      };
    }
    return { ok: true, reason: "" };
  }, [info, privacyLevel, discloseToggle, yourBrand, brandedContent]);

  const handlePost = async () => {
    if (!validity.ok || !info) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const tiktokParams = {
        privacy_level: privacyLevel,
        disable_comment: !allowComment,
        disable_duet: !allowDuet,
        disable_stitch: !allowStitch,
        brand_organic_toggle: yourBrand,
        brand_content_toggle: brandedContent,
      };
      let result: { post_id: string; status?: string };
      if (mode === "schedule") {
        // Schedule path: persist disclosure on the post and call
        // /schedule. The worker will read tiktok_post_info at publish
        // time. The user only goes through this modal once per post —
        // not again when the scheduled moment fires.
        if (!scheduledAt) {
          throw new Error("scheduledAt is required for schedule mode");
        }
        result = await apiPost<{ post_id: string; status?: string }>(
          `/api/v1/posts/${postId}/schedule`,
          {
            scheduled_at: scheduledAt,
            tiktok_post_info: tiktokParams,
          }
        );
      } else {
        result = await apiPost<{ post_id: string; status?: string }>(
          `/api/v1/posts/${postId}/publish`,
          { tiktok: tiktokParams }
        );
      }
      onPublished(result);
    } catch (err) {
      setSubmitError(
        err instanceof Error
          ? err.message
          : `Failed to ${mode === "schedule" ? "schedule" : "publish"} to TikTok`
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div
        className="w-full max-w-lg max-h-[90vh] overflow-y-auto rounded-xl bg-white shadow-2xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="tt-direct-post-title"
      >
        <div className="border-b border-gray-200 px-5 py-3 flex items-center justify-between">
          <h2 id="tt-direct-post-title" className="text-base font-semibold text-gray-900">
            {mode === "schedule" ? "Schedule TikTok post" : "Post to TikTok"}
          </h2>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="text-gray-400 hover:text-gray-600 disabled:opacity-50"
            aria-label="Close"
          >
            ×
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {loading && (
            <div className="flex items-center justify-center py-10">
              <Spinner size={28} />
            </div>
          )}

          {loadError && (
            <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              {loadError}
            </div>
          )}

          {info && !loading && (
            <>
              {/* MANDATORY: account disclosure */}
              <div className="flex items-center gap-3 rounded-lg border border-gray-200 bg-gray-50 p-3">
                {info.creator_avatar_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={info.creator_avatar_url}
                    alt={info.creator_username}
                    className="h-10 w-10 rounded-full object-cover"
                  />
                ) : (
                  <div className="h-10 w-10 rounded-full bg-gray-200" />
                )}
                <div className="min-w-0">
                  <p className="text-sm text-gray-700">
                    Your video will be posted to TikTok as{" "}
                    <span className="font-semibold">
                      @{info.creator_username}
                    </span>
                  </p>
                  {info.creator_nickname && (
                    <p className="text-xs text-gray-500 truncate">
                      {info.creator_nickname}
                    </p>
                  )}
                </div>
              </div>

              {/* Caption preview — read-only, mirrors what we're about to send */}
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">
                  Caption
                </label>
                <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-900 max-h-32 overflow-y-auto whitespace-pre-wrap">
                  {captionPreview || (
                    <span className="text-gray-400">No caption set.</span>
                  )}
                </div>
              </div>

              {/* MANDATORY: privacy dropdown sourced from creator_info */}
              <div>
                <label
                  htmlFor="tt-privacy"
                  className="block text-xs font-medium text-gray-600 mb-1"
                >
                  Who can view this video
                </label>
                <select
                  id="tt-privacy"
                  value={privacyLevel}
                  onChange={(e) =>
                    setPrivacyLevel(e.target.value as PrivacyLevel)
                  }
                  className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900"
                >
                  {RESTRICTIVENESS_ORDER.filter((p) =>
                    info.privacy_level_options.includes(p)
                  ).map((p) => (
                    <option key={p} value={p}>
                      {PRIVACY_LABEL[p]}
                    </option>
                  ))}
                </select>
              </div>

              {/* MANDATORY: Allow comment / duet / stitch — hide options
                  the creator has disabled at the account level. */}
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">
                  Allow users to
                </label>
                <div className="space-y-1.5">
                  {!info.comment_disabled && (
                    <label className="flex items-center gap-2 text-sm text-gray-700">
                      <input
                        type="checkbox"
                        checked={allowComment}
                        onChange={(e) => setAllowComment(e.target.checked)}
                      />
                      Comment
                    </label>
                  )}
                  {!info.duet_disabled && (
                    <label className="flex items-center gap-2 text-sm text-gray-700">
                      <input
                        type="checkbox"
                        checked={allowDuet}
                        onChange={(e) => setAllowDuet(e.target.checked)}
                      />
                      Duet
                    </label>
                  )}
                  {!info.stitch_disabled && (
                    <label className="flex items-center gap-2 text-sm text-gray-700">
                      <input
                        type="checkbox"
                        checked={allowStitch}
                        onChange={(e) => setAllowStitch(e.target.checked)}
                      />
                      Stitch
                    </label>
                  )}
                  {info.comment_disabled &&
                    info.duet_disabled &&
                    info.stitch_disabled && (
                      <p className="text-xs text-gray-500">
                        Interactions are disabled for this account in TikTok
                        settings.
                      </p>
                    )}
                </div>
              </div>

              {/* MANDATORY: Disclose video content toggle + sub-toggles.
                  Per Content Sharing Guidelines, when this is ON the user
                  MUST pick at least one of Your Brand or Branded Content,
                  and Branded Content cannot pair with SELF_ONLY. */}
              <div className="rounded-lg border border-gray-200 p-3">
                <label className="flex items-start gap-2 text-sm text-gray-800">
                  <input
                    type="checkbox"
                    checked={discloseToggle}
                    onChange={(e) => {
                      const v = e.target.checked;
                      setDiscloseToggle(v);
                      if (!v) {
                        setYourBrand(false);
                        setBrandedContent(false);
                      }
                    }}
                    className="mt-0.5"
                  />
                  <span>
                    <span className="font-medium">Disclose video content</span>
                    <span className="block text-xs text-gray-500">
                      Turn on to disclose that your video promotes yourself, a
                      third party, or both.
                    </span>
                  </span>
                </label>

                {discloseToggle && (
                  <div className="mt-3 ml-6 space-y-2">
                    <label className="flex items-start gap-2 text-sm text-gray-800">
                      <input
                        type="checkbox"
                        checked={yourBrand}
                        onChange={(e) => setYourBrand(e.target.checked)}
                        className="mt-0.5"
                      />
                      <span>
                        <span className="font-medium">Your Brand</span>
                        <span className="block text-xs text-gray-500">
                          You are promoting yourself or a business you own.
                          Your video will be labeled “Promotional content”.
                        </span>
                      </span>
                    </label>
                    <label className="flex items-start gap-2 text-sm text-gray-800">
                      <input
                        type="checkbox"
                        checked={brandedContent}
                        onChange={(e) => setBrandedContent(e.target.checked)}
                        className="mt-0.5"
                      />
                      <span>
                        <span className="font-medium">Branded Content</span>
                        <span className="block text-xs text-gray-500">
                          You are promoting another brand or a third party.
                          Your video will be labeled “Paid partnership”.
                        </span>
                      </span>
                    </label>

                    {brandedContent && (
                      <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                        By posting, you agree to TikTok&apos;s Branded Content
                        Policy. Branded Content can&apos;t be set to private.
                      </div>
                    )}
                  </div>
                )}
              </div>

              {!validity.ok && (
                <p className="text-xs text-amber-700">{validity.reason}</p>
              )}

              {submitError && (
                <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                  {submitError}
                </div>
              )}
            </>
          )}
        </div>

        <div className="border-t border-gray-200 px-5 py-3 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="rounded-lg px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handlePost}
            disabled={!validity.ok || submitting || !info}
            className="rounded-lg bg-black px-4 py-1.5 text-sm font-medium text-white hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? (
              <ButtonSpinner
                label={mode === "schedule" ? "Scheduling…" : "Posting…"}
              />
            ) : mode === "schedule" ? (
              "Schedule"
            ) : (
              "Post to TikTok"
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
