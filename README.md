# TickTick

A self-hosted work time tracking application built with Python and FastAPI.

## Features

- **Timer Controls**: Start, Pause, Continue, Stop, and Reset buttons
- **Persistent Tracking**: Time continues to be tracked even when the browser is closed
- **Daily Tracking**: Combine separate sessions into one daily total while keeping every session independently editable
- **Smart Calculations**:
  - Daily target finish time (8h 12m daily requirement for 41h/week)
  - Latest leave time (max 10 hours/day)
  - Automatic lunch deduction after 6 hours, reduced by recorded pauses and gaps
  - Automatic stop at the credited daily maximum, including earlier sessions
- **Statistics Page**: View weekly and monthly work summaries
- **Docker Ready**: Easy deployment with Docker Compose

## Quick Start

### Using Docker (Recommended)

```bash
# Clone the repository
git clone https://github.com/dadav/ticktick.git
cd ticktick

# Start the application
docker compose up -d

# Open in browser
open http://localhost:8000
```

### Manual Installation

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
# Clone the repository
git clone https://github.com/dadav/ticktick.git
cd ticktick

# Install dependencies
uv sync

# Run the application
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

## Configuration

Configuration is done via environment variables. Copy `.env.example` to `.env` and modify as needed:

```bash
cp .env.example .env
```

### Available Options

| Variable                   | Default              | Description                               |
| -------------------------- | -------------------- | ----------------------------------------- |
| `TICKTICK_DB_PATH`         | `./data/ticktick.db` | Path to SQLite database file              |
| `TICKTICK_WEEKLY_HOURS`    | `41`                 | Required work hours per week              |
| `TICKTICK_MAX_DAILY_HOURS` | `10`                 | Maximum credited work hours per day       |
| `TICKTICK_LUNCH_THRESHOLD` | `6`                  | Hours after which lunch break is deducted |
| `TICKTICK_LUNCH_DURATION`  | `30`                 | Lunch break duration in minutes           |
| `TICKTICK_HOST`            | `0.0.0.0`            | Server bind address                       |
| `TICKTICK_PORT`            | `8000`               | Server port                               |
| `TZ`                       | `Europe/Berlin`      | Container timezone (Docker only)          |

### Examples

**Standard 40-hour week:**

```bash
TICKTICK_WEEKLY_HOURS=40
```

**No lunch break deduction:**

```bash
TICKTICK_LUNCH_THRESHOLD=24
```

**Different port:**

```bash
TICKTICK_PORT=3000
```

## Usage

### Timer Page

1. **Start**: Begin a new work session
2. **Pause**: Temporarily stop the timer (e.g., for a break)
3. **Continue**: Resume after a pause
4. **Stop**: End and save the session
5. **Reset**: Discard the current session without saving

The page displays:

- Credited daily work time, retained after Stop
- Actual recorded work and the current session duration separately
- First start time and the workday date
- Number and total duration of pauses, including gaps between sessions
- Any remaining automatic lunch deduction
- Earliest time you can leave (reaching daily minimum)
- Latest time you should leave (max hours limit)
- Remaining work to reach the daily target and maximum

### Multiple sessions and lunch credit

Each Start creates a separate entry. Daily calculations combine entries with the same starting date and count overlapping work only once. Gaps between entries count as pauses when the next entry starts. Time after the final Stop does not add break credit.

After more than six hours of actual work, TickTick deducts only the missing part of the configured lunch allowance. All recorded pauses and gaps add up toward that allowance:

- Four hours of work, a two-hour gap, then four more hours count as eight credited hours.
- Twenty minutes of recorded breaks leave ten minutes of automatic deduction with the default settings.
- An uninterrupted 06:00 to 16:30 session counts as ten credited hours and reaches the daily maximum.

During an explicit Pause, credited time can increase as the real break replaces the automatic deduction. Actual recorded work stays frozen. Remaining durations and finish estimates include any lunch deduction still needed; paused estimates assume immediate continuation. Finish estimates are hidden while stopped.

The timer stops automatically at the credited daily maximum. If polling was delayed, it saves the time the maximum was first reached. Further starts are blocked, including when the prospective gap would restore enough lunch credit to reach the maximum immediately. Manual corrections above the maximum are preserved and flagged. Reducing a day's total can allow tracking again.

Overnight sessions belong entirely to their starting date. A new session started after midnight belongs to the new day. Discard only removes the current session from daily calculations.

### Statistics Page

View your work history including:

- This week's total hours and progress toward weekly goal
- This month's statistics
- The ten most recent completed workdays, with every session listed beneath its daily total and overtime
- Independent session editing and deletion, with totals refreshed immediately
- Average start/end times based on each day’s first start and last end

Existing history is recalculated from recorded timestamps and pauses using the same rules. Historical totals can change where old calculations deducted lunch twice or capped individual session durations. Stored timestamps are preserved and no database migration is required. Weekly and monthly summaries continue to include completed sessions only.

## API Endpoints

| Method   | Endpoint                  | Description                            |
| -------- | ------------------------- | -------------------------------------- |
| `GET`    | `/api/status`             | Get current timer status               |
| `POST`   | `/api/start`              | Start a new session                    |
| `POST`   | `/api/pause`              | Pause the current session              |
| `POST`   | `/api/continue`           | Resume from pause                      |
| `POST`   | `/api/stop`               | Stop and save session                  |
| `POST`   | `/api/reset`              | Discard current session                |
| `GET`    | `/api/statistics/summary` | Get weekly/monthly stats               |
| `POST`   | `/api/sessions`           | Manually add a completed past session  |
| `GET`    | `/api/sessions/{id}`      | Get session details with pause periods |
| `PUT`    | `/api/sessions/{id}`      | Update start/end time of a session     |
| `DELETE` | `/api/sessions/{id}`      | Delete a non-active session            |

`GET /api/status` includes a `day` summary even while idle if there are completed sessions today, plus `can_start` and `start_blocked_reason`. `session` describes only the active entry; `calculations` uses daily totals and has null finish estimates while idle. With an active overnight entry, `day.date` is its starting date.

`GET /api/statistics/summary` adds `recent_days`, each containing a `day` summary and its `sessions`. The legacy `recent_sessions` list remains available. Session duration fields describe actual work before the automatic lunch deduction; overtime fields describe the whole containing day.

## Data Persistence

All data is stored in a SQLite database. When using Docker, the database is persisted in the `./data` directory via a volume mount.

To backup your data:

```bash
cp ./data/ticktick.db ./ticktick-backup.db
```

## Development

```bash
# Install dependencies including dev tools
uv sync

# Run with auto-reload
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Backend regression checks:

```bash
just test
```

Browser checks use a temporary database and a controlled clock, with Chromium installed on the host:

```bash
uv run --with playwright python tests/browser_smoke.py
```

## License

MIT
