# Bootstrap Product

## Description
Scaffolds a new product/release in the Amplify OS system, creating all necessary domain objects and a default campaign workflow.

## Arguments
- `artist_name` (required): Name of the artist
- `release_title` (required): Title of the release
- `release_date` (required): Target release date (YYYY-MM-DD)
- `release_type` (optional): single | ep | album | deluxe (default: single)
- `track_list` (optional): Comma-separated track names

## Steps

1. **Gather release information**
   - Prompt for artist name, release title, release date, and track list if not provided as arguments
   - Validate the release date is in the future
   - Look up existing artist profile in the system or note that one needs to be created

2. **Create Release domain object**
   - Generate a Release entity in `packages/core/domain/release.py` format
   - Assign a unique release ID
   - Set release type, date, and metadata
   - Create Track domain objects for each track in the track list

3. **Generate default campaign workflow**
   - Based on release type, select appropriate workflow template from `packages/core/workflows/`
   - For singles: 7-day pre-release, release day, 14-day post-release, evergreen
   - For EPs: 14-day pre-release, release day, 21-day post-release, evergreen
   - For albums: 28-day pre-release with single rollout, release day, 30-day post-release, evergreen
   - Populate workflow with release-specific details

4. **Create calendar items**
   - Generate key date entries: announcement, pre-save launch, release day, music video (if applicable), playlist push deadlines
   - Space content teasers across the pre-release window

5. **Output summary**
   - Display all created entities with their IDs
   - Show the campaign timeline as a visual summary
   - List any manual steps required (e.g., uploading artwork, finalizing masters)
   - Suggest next steps: run the copywriter agent, connect distribution
