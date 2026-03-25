export interface Artist {
  id: string;
  name: string;
  genre?: string;
  imageUrl?: string;
  createdAt: string;
  updatedAt: string;
}

export interface ReleaseDestinations {
  hyperfollowUrl?: string;
  linktreeUrl?: string;
  bandcampUrl?: string;
  youtubeUrl?: string;
  tiktokUrl?: string;
  instagramUrl?: string;
}

export interface Release extends ReleaseDestinations {
  id: string;
  artistId: string;
  title: string;
  type: "single" | "ep" | "album";
  releaseDate: string;
  status: "draft" | "scheduled" | "released";
  createdAt: string;
  updatedAt: string;
}

export interface Campaign {
  id: string;
  name: string;
  artistId: string;
  releaseId?: string;
  status: "draft" | "active" | "paused" | "completed";
  mode: "manual" | "ai_plan" | "autopilot";
  startDate: string;
  endDate?: string;
  createdAt: string;
  updatedAt: string;
}

export interface Post {
  id: string;
  campaignId: string;
  platform: "instagram" | "tiktok" | "twitter" | "facebook" | "youtube";
  content: string;
  scheduledAt: string;
  status: "draft" | "scheduled" | "published" | "failed";
  approvalStatus?: "pending_review" | "approved" | "rejected" | null;
  dayNumber?: number | null;
  actionTypeLabel?: string | null;
  destinationUrl?: string;
  createdAt: string;
  updatedAt: string;
}

export interface PostDestinationWarning {
  postId: string;
  platform: string;
  status: string;
  scheduledAt: string | null;
}

export interface CampaignDestinationReport {
  campaignId: string;
  canonicalUrl: string | null;
  totalPosts: number;
  postsWithDestination: number;
  postsMissingDestination: PostDestinationWarning[];
}

export interface Approval {
  id: string;
  postId: string;
  requestedBy: string;
  assignedTo: string;
  status: "pending" | "approved" | "rejected";
  comment?: string;
  createdAt: string;
  updatedAt: string;
}

export interface CalendarItem {
  id: string;
  tenant_id: string;
  campaign_id: string | null;
  title: string;
  description: string;
  item_type: string;
  scheduled_date: string; // YYYY-MM-DD
  scheduled_time: string | null; // HH:MM:SS
  is_completed: boolean;
  created_at: string;
  updated_at: string;
}
