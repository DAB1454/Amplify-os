# Examples

Practical examples of common Amplify-OS operations.

---

## 1. Calendar Import (CSV)

Amplify-OS accepts CSV calendar files for bulk-importing content schedules. The file `scripts/sample_calendar.csv` shows the expected format.

### CSV Schema

```
date,time,type,title,description,platform,caption,asset_ref,cta,track_ref,release_ref,campaign_ref
```

| Column | Required | Description |
|--------|----------|-------------|
| `date` | Yes | `YYYY-MM-DD` |
| `time` | No | `HH:MM AM/PM` (omit for milestones) |
| `type` | Yes | `post`, `story`, `reel`, `email`, `milestone`, `release`, `ad`, `reminder`, `deadline` |
| `title` | Yes | Short label |
| `description` | No | Longer detail |
| `platform` | No | `instagram`, `tiktok`, `youtube`, `email` (omit for cross-platform milestones) |
| `caption` | No | Post copy |
| `asset_ref` | No | Filename of the media asset |
| `cta` | No | Call-to-action text |
| `track_ref` | No | Track name (for single releases or previews) |
| `release_ref` | No | Release name |
| `campaign_ref` | No | Campaign name |

### API Usage

```bash
# Upload a calendar CSV to a campaign
curl -X POST http://localhost:8000/api/v1/campaigns/{campaign_id}/calendar/import \
  -H "Content-Type: multipart/form-data" \
  -F "file=@scripts/sample_calendar.csv"
```

### Worker Job

The `ingest_calendar` worker job processes the CSV and creates `CalendarItem` records:

```python
# apps/worker/app/jobs/ingest_calendar.py
async def ingest_calendar(payload: dict) -> dict:
    """Parse CSV rows → CalendarItem records."""
    # 1. Read CSV from S3 or local path
    # 2. Validate each row against expected schema
    # 3. Map type/platform columns to CalendarItem fields
    # 4. Deduplicate by (date, time, title, platform)
    # 5. Bulk insert into the calendar_items table
    # 6. Return count of created/skipped/errored rows
```

### Example CSV

```csv
date,time,type,title,platform,caption,cta,release_ref,campaign_ref
2026-03-01,10:00 AM,post,Album Announcement,instagram,"Big news coming 🎵 March 29th.",Pre-save link in bio,Midnight Frequencies,Launch
2026-03-08,,milestone,Pre-save Campaign Launch,,,,Midnight Frequencies,Launch
2026-03-14,12:00 PM,release,Single Release - Neon Pulse,,,,Midnight Frequencies,Launch
2026-03-29,,milestone,RELEASE DAY,,,,Midnight Frequencies,Launch
```

---

## 2. Release Setup

Setting up a new release involves creating the release record, adding tracks, connecting platform channels, and generating a campaign with calendar items.

### Step 1: Create the Artist (if new)

```bash
curl -X POST http://localhost:8000/api/v1/artists \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Luna Vega",
    "genre": "Indie Electronic",
    "bio": "Brooklyn-based indie electronic artist.",
    "social_links": {
      "instagram": "https://instagram.com/lunavegamusic",
      "tiktok": "https://tiktok.com/@lunavega"
    }
  }'
```

### Step 2: Create the Release

```bash
curl -X POST http://localhost:8000/api/v1/releases \
  -H "Content-Type: application/json" \
  -d '{
    "artist_id": "ARTIST_UUID",
    "title": "Midnight Frequencies",
    "release_type": "album",
    "release_date": "2026-04-15",
    "metadata": {
      "label": "Neon Drift Records",
      "genre_tags": ["indie electronic", "synthwave"],
      "distributor": "DistroKid"
    }
  }'
```

### Step 3: Add Tracks

```bash
curl -X POST http://localhost:8000/api/v1/releases/{release_id}/tracks \
  -H "Content-Type: application/json" \
  -d '{
    "tracks": [
      {"title": "Signal Lost", "track_number": 1, "duration_seconds": 245, "isrc": "USRC12600001"},
      {"title": "Midnight Frequencies", "track_number": 2, "duration_seconds": 312, "isrc": "USRC12600002", "is_single": true},
      {"title": "Neon Rain", "track_number": 3, "duration_seconds": 198, "isrc": "USRC12600003"}
    ]
  }'
```

### Step 4: Connect Channels

```bash
# Each platform requires OAuth — this starts the auth flow
curl -X POST http://localhost:8000/api/v1/channels \
  -H "Content-Type: application/json" \
  -d '{
    "artist_id": "ARTIST_UUID",
    "platform": "instagram",
    "platform_account_id": "lunavegamusic",
    "display_name": "Luna Vega"
  }'
```

### Step 5: Generate Campaign

```bash
curl -X POST http://localhost:8000/api/v1/campaigns \
  -H "Content-Type: application/json" \
  -d '{
    "artist_id": "ARTIST_UUID",
    "release_id": "RELEASE_UUID",
    "name": "Midnight Frequencies Launch",
    "phase": "pre_release",
    "start_date": "2026-03-15",
    "end_date": "2026-05-15",
    "config": {
      "platforms": ["instagram", "tiktok", "youtube"],
      "posting_cadence": "daily"
    }
  }'
```

### Using the Seed Script

For local development, the seed script does all of the above in one step:

```bash
# Demo artist (Luna Vega)
make seed

# Real artist (Drew Baird)
make seed-drew
```

---

## 3. Campaign Execution

A campaign moves through four workflow phases automatically. Here's how the system executes each phase.

### Phase 1: Pre-Release (4 weeks before release)

The `PreReleaseWorkflow` generates a 4-week countdown calendar:

```python
from packages.core.workflows.pre_release import PreReleaseWorkflow
from datetime import date

calendar = PreReleaseWorkflow.generate_calendar(
    release_date=date(2026, 4, 15),
    channels=["instagram", "tiktok", "youtube"]
)

# Returns 4 weekly entries:
# Week 1: "Teaser & Announce" — teaser visuals, announce date, pre-save links
# Week 2: "Behind the Scenes" — studio content, creative process, pre-save push
# Week 3: "Single/Preview & Ramp" — lead single, ramp posting, cross-promotion
# Week 4: "Final Countdown" — daily countdowns, final pre-save push, email blast
```

### Phase 2: Release Day

The `ReleaseDayWorkflow` generates an hour-by-hour checklist:

```python
from packages.core.workflows.release_day import ReleaseDayWorkflow
from datetime import date

checklist = ReleaseDayWorkflow.generate_calendar(
    release_date=date(2026, 4, 15),
    channels=["instagram", "tiktok", "youtube"]
)

# 10 timed actions from 06:00 to 22:00:
# 06:00 — Verify all platforms are live (CRITICAL)
# 08:00 — Post "Out Now" across all channels (CRITICAL)
# 09:00 — Stories with streaming links
# 10:00 — Email blast to subscribers (CRITICAL)
# 12:00 — Midday engagement push
# 14:00 — Behind-the-scenes content
# 16:00 — Fan reaction roundup
# 18:00 — Evening timezone push
# 20:00 — Thank-you post
# 22:00 — Day 1 recap and tomorrow preview
```

### Phase 3: Post-Release 30-Day

The `PostRelease30Workflow` sustains momentum for 30 days:

```python
from packages.core.workflows.post_release_30 import PostRelease30Workflow
from datetime import date

calendar = PostRelease30Workflow.generate_calendar(
    release_date=date(2026, 4, 15),
    channels=["instagram", "tiktok", "youtube"]
)

# Week 1: "Momentum" — streaming milestones, fan reactions, playlist pitching
# Week 2: "UGC & Challenges" — TikTok challenges, hashtag campaigns, giveaways
# Week 3: "Remixes & Acoustic" — alternate versions, creator collabs, press
# Week 4: "Recap & Analytics" — 30-day stats, thank fans, tease next project
```

### Phase 4: Evergreen

The `EvergreenWorkflow` generates recurring content on a configurable cycle:

```python
from packages.core.workflows.evergreen import EvergreenWorkflow

schedule = EvergreenWorkflow.generate_schedule(frequency_days=3)

# 7 recurring activities, cycling every 21 days:
# Day 0:  Throwback Post
# Day 3:  Lyric Card
# Day 6:  Fan Highlight
# Day 9:  Playlist Pitching
# Day 12: Milestone Celebration
# Day 15: Behind the Scenes
# Day 18: Engagement Post
# (then repeats)
```

### End-to-End Flow

```
1. Artist creates a release → API stores in DB
2. Campaign is created → workflows auto-generate calendar
3. AI agent writes post copy for each calendar item
4. Policy engine evaluates each post:
   - ALLOWED → queued for publishing
   - REQUIRE_APPROVAL → sent to approval queue
5. Scheduler triggers publish jobs at scheduled times
6. Platform adapters post to Instagram/TikTok/YouTube
7. Worker polls platform APIs for engagement metrics
8. Experiment engine compares A/B variants, promotes winners
9. After 30 days → campaign transitions to evergreen phase
```

### Monitoring a Running Campaign

```bash
# View campaign status
curl http://localhost:8000/api/v1/campaigns/{campaign_id}

# View upcoming calendar items
curl http://localhost:8000/api/v1/campaigns/{campaign_id}/calendar?status=upcoming

# View pending approvals
curl http://localhost:8000/api/v1/approvals?status=pending

# View post performance
curl http://localhost:8000/api/v1/analytics/posts?campaign_id={campaign_id}

# View experiment results
curl http://localhost:8000/api/v1/experiments/{experiment_id}
```
