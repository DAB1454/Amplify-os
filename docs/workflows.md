# Campaign Workflows

Amplify-OS organizes music promotion into structured workflows aligned with the release lifecycle.

## Pre-Release Phase (4 weeks before release)

**Goal:** Build anticipation and grow audience before the release drops.

| Week | Actions | Automation Triggers |
|------|---------|---------------------|
| -4   | Announce upcoming release, teaser content | Campaign created with release date |
| -3   | Behind-the-scenes content, countdown posts | Scheduled by campaign timeline |
| -2   | Pre-save links, snippet previews | Auto-generated from release metadata |
| -1   | Final countdown, listening party announcements | Daily posts triggered by proximity to release |

**Automated actions:**
- AI generates teaser copy variants for each platform
- Media renderer creates countdown graphics
- Adapters schedule posts at optimal engagement times
- Pre-save link aggregation and tracking

## Release Day

**Goal:** Maximize first-day streams and engagement.

**Actions:**
- Publish "out now" posts across all connected platforms
- Share streaming links (Spotify, Apple Music, YouTube, etc.)
- Engage with fan comments and shares
- Push notifications to subscribers

**Automation Triggers:**
- Release date match triggers the release-day workflow
- Posts are pre-generated and queued during pre-release
- Real-time engagement metrics begin collection

## Post-Release Phase (30 days after release)

**Goal:** Sustain momentum and build long-term engagement.

| Week | Actions | Automation Triggers |
|------|---------|---------------------|
| +1   | Share milestones (stream counts, chart positions) | Metrics thresholds trigger celebration posts |
| +2   | Fan content reposts, lyric graphics | AI selects standout lyrics, generates visuals |
| +3   | Playlist placement updates, review highlights | Adapter polls playlist and review sources |
| +4   | Retrospective content, transition to evergreen | Campaign end date triggers phase change |

**Automated actions:**
- Daily metrics collection from all platforms
- Milestone detection and auto-celebration posts
- Engagement-based content recommendations

## Evergreen Phase

**Goal:** Keep catalog visible with minimal effort.

**Actions:**
- Periodic "throwback" posts for catalog releases
- Anniversary reminders
- Playlist pitch follow-ups
- Cross-promotion with new releases

**Automation Triggers:**
- Anniversary dates trigger throwback content
- New release campaigns reference catalog for cross-promotion
- Low-effort, high-frequency scheduling (1-2 posts/week)
