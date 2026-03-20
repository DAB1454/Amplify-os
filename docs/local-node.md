# Local Node: Running Amplify-OS on Your Machine

The **local node** lets a single artist run the full Amplify-OS stack 24/7 on their own machine — a laptop, desktop, or home server. It handles job execution, media rendering, browser automation, and scheduled tasks without depending on cloud infrastructure.

## Architecture

```
┌──────────────────────────────────────────────────┐
│  Local Node Process                              │
│                                                  │
│  ┌─────────────┐  ┌─────────────┐               │
│  │   Worker     │  │  Scheduler  │               │
│  │  (job pull)  │  │ (heartbeat, │               │
│  │             │  │  post scan,  │               │
│  │             │  │  metric sync)│               │
│  └──────┬──────┘  └──────┬──────┘               │
│         │                │                       │
│  ┌──────┴────────────────┴──────┐               │
│  │        Supervisor            │               │
│  │   (auto-restart, backoff)    │               │
│  └──────────────────────────────┘               │
│                                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │  FFmpeg  │ │Playwright│ │  Offline Queue   │ │
│  │  Runner  │ │  Runner  │ │  (SQLite)        │ │
│  └──────────┘ └──────────┘ └──────────────────┘ │
│                                                  │
│  ┌──────────┐ ┌──────────────────────────────┐  │
│  │  Secrets │ │  Heartbeat HTTP Server       │  │
│  │  (Fernet)│ │  :8100/health  :8100/ready   │  │
│  └──────────┘ └──────────────────────────────┘  │
└──────────────────────────────────────────────────┘
         │
         │  HTTP (when online)
         ▼
┌──────────────────┐
│  Amplify API     │
│  :8000           │
└──────────────────┘
```

**Key behaviors:**
- **Auto-restart**: If a process crashes, the supervisor restarts it with exponential backoff (1s → 2s → 4s ... 60s max). After 10 consecutive startup failures, the process enters FATAL state.
- **Offline resilience**: When the API is unreachable, jobs are written to a local SQLite queue. The scheduler flushes the queue once connectivity returns.
- **Encrypted secrets**: API keys and tokens are stored in a Fernet-encrypted file. The encryption key is auto-generated and stored at `~/.amplify-os/.secrets.key`.

## Quick Start

### Option A: Docker Compose (recommended)

```bash
# Start the full stack including local node
docker compose --profile local-node up -d

# Or use the Makefile shortcut
make local-node
```

This starts postgres, redis, the API, web dashboard, worker, AND the local node. The local node connects to the API at `http://api:8000` inside the Docker network.

### Option B: Bare metal

```bash
# 1. Install prerequisites
# Python 3.12+, ffmpeg, Node.js 18+

# macOS
brew install ffmpeg python@3.12

# Ubuntu/Debian
sudo apt install ffmpeg python3.12 python3.12-venv

# Windows
# Install ffmpeg from https://ffmpeg.org/download.html
# Install Python 3.12 from https://python.org

# 2. Clone and install
git clone <repo-url> amplify-os && cd amplify-os
make setup

# 3. Start supporting services
docker compose up -d postgres redis

# 4. Run the API
cd apps/api && uvicorn app.main:app --port 8000 &

# 5. Start the local node
amplify-node start --api-url http://localhost:8000
```

## Configuration

All settings use environment variables with the `AMPLIFY_NODE_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `AMPLIFY_NODE_API_URL` | `http://localhost:8000` | API server URL |
| `AMPLIFY_NODE_TENANT_ID` | `local` | Tenant identifier |
| `AMPLIFY_NODE_HEARTBEAT_PORT` | `8100` | Local health endpoint port |
| `AMPLIFY_NODE_HEARTBEAT_INTERVAL` | `30` | Heartbeat frequency (seconds) |
| `AMPLIFY_NODE_SCHEDULER_POLL_INTERVAL` | `60` | Job polling interval (seconds) |
| `AMPLIFY_NODE_DATA_DIR` | `~/.amplify-os` | Base data directory |
| `AMPLIFY_NODE_CACHE_DIR` | `~/.amplify-os/cache` | Rendered asset cache |
| `AMPLIFY_NODE_MEDIA_OUTPUT_DIR` | `~/.amplify-os/media` | Media output directory |
| `AMPLIFY_NODE_MAX_RESTART_ATTEMPTS` | `10` | Max consecutive crash restarts |
| `AMPLIFY_NODE_MAX_OFFLINE_QUEUE_SIZE` | `1000` | Max buffered offline jobs |
| `AMPLIFY_NODE_LOG_LEVEL` | `INFO` | Logging level |

## CLI Reference

```bash
# Start the daemon
amplify-node start
amplify-node start --api-url http://my-server:8000 --tenant-id my-label

# Check connectivity and process status
amplify-node status

# Detect local capabilities (ffmpeg, playwright)
amplify-node capabilities

# Manage encrypted secrets
amplify-node secret INSTAGRAM_TOKEN sk-abc123   # set
amplify-node secret INSTAGRAM_TOKEN             # get
amplify-node secret INSTAGRAM_TOKEN --delete    # delete
amplify-node list-secrets                       # list all keys

# View offline queue
amplify-node queue-status
```

## Running 24/7

### Linux (systemd)

Create `/etc/systemd/system/amplify-node.service`:

```ini
[Unit]
Description=Amplify-OS Local Node
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/home/your-user/amplify-os
ExecStart=/home/your-user/amplify-os/.venv/bin/amplify-node start
Restart=always
RestartSec=5
Environment=AMPLIFY_NODE_API_URL=http://localhost:8000
Environment=AMPLIFY_NODE_TENANT_ID=local

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable amplify-node
sudo systemctl start amplify-node

# Check status
sudo systemctl status amplify-node
journalctl -u amplify-node -f
```

### macOS (launchd)

Create `~/Library/LaunchAgents/com.amplify.node.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.amplify.node</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/you/amplify-os/.venv/bin/amplify-node</string>
        <string>start</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>AMPLIFY_NODE_API_URL</key>
        <string>http://localhost:8000</string>
    </dict>
    <key>StandardOutPath</key>
    <string>/tmp/amplify-node.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/amplify-node.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.amplify.node.plist
launchctl start com.amplify.node
```

### Windows (Task Scheduler)

1. Open Task Scheduler → Create Task
2. **General**: Name = "Amplify Node", Run whether user is logged on or not
3. **Triggers**: At startup
4. **Actions**: Start a program
   - Program: `C:\Users\you\amplify-os\.venv\Scripts\amplify-node.exe`
   - Arguments: `start`
   - Start in: `C:\Users\you\amplify-os`
5. **Settings**: If task fails, restart every 1 minute (up to 10 times)

### Docker (recommended for always-on)

```bash
# The local-node service has restart: unless-stopped
make local-node

# To check status
docker compose --profile local-node ps
docker compose --profile local-node logs local-node -f

# Health check
curl http://localhost:8100/health
```

## Monitoring

The local node exposes two HTTP endpoints:

**GET /health** — Returns node status, uptime, process states, offline queue size:
```json
{
  "status": "ok",
  "node_id": "a1b2c3d4e5f6",
  "uptime_seconds": 86400,
  "processes": {
    "worker": {"state": "running", "restart_count": 1},
    "scheduler": {"state": "running", "restart_count": 0}
  },
  "offline_queue_size": 0,
  "system": {"os": "Linux", "arch": "x86_64", "hostname": "studio-pc"}
}
```

**GET /ready** — Returns 200 if all processes are running, 503 otherwise. Use this for load balancer health checks or monitoring scripts.

## Offline Behavior

When the API goes down:

1. The heartbeat client detects the outage and tracks consecutive failures
2. The worker buffers undeliverable jobs into the SQLite offline queue
3. The scheduler continues scanning for due posts and queues them locally
4. Once the API returns, the offline queue flushes in FIFO order
5. Successfully delivered jobs are removed; failures are retried with attempt tracking

The offline queue persists across node restarts (SQLite on disk). It caps at 1000 jobs by default — if full, the oldest job is evicted to make room.

## Data Layout

```
~/.amplify-os/
├── secrets.enc          # Fernet-encrypted secrets blob
├── .secrets.key         # Encryption key (chmod 600)
├── offline_queue.db     # SQLite offline job queue
├── cache/               # Content-addressed asset cache (SHA-256)
└── media/               # Rendered media output
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "Cannot decrypt secrets" | Wrong key file or passphrase. Delete `~/.amplify-os/secrets.enc` and re-enter secrets. |
| ffmpeg not detected | Install ffmpeg and ensure it's on PATH. Run `amplify-node capabilities`. |
| Node stuck in BACKOFF | Check `amplify-node status` — a process is crash-looping. Check logs for the root cause. |
| Offline queue growing | API is unreachable. Check `amplify-node status` and verify the API URL. |
| High restart count | Normal after brief network blips. Only concerning if a process reaches FATAL state. |
