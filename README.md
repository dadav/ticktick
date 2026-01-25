# TickTick

A self-hosted work time tracking application built with Python and FastAPI.

## Features

- **Timer Controls**: Start, Pause, Continue, Stop, and Reset buttons
- **Persistent Tracking**: Time continues to be tracked even when the browser is closed
- **Smart Calculations**:
  - Earliest leave time (based on 8h 12m daily requirement for 41h/week)
  - Latest leave time (max 10 hours/day)
  - Automatic lunch break deduction (30 min after 6 hours)
- **Statistics Page**: View weekly and monthly work summaries
- **Docker Ready**: Easy deployment with Docker Compose

## Quick Start

### Using Docker (Recommended)

```bash
# Clone the repository
git clone https://github.com/yourusername/ticktick.git
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
git clone https://github.com/yourusername/ticktick.git
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

| Variable | Default | Description |
|----------|---------|-------------|
| `TICKTICK_DB_PATH` | `./data/ticktick.db` | Path to SQLite database file |
| `TICKTICK_WEEKLY_HOURS` | `41` | Required work hours per week |
| `TICKTICK_MAX_DAILY_HOURS` | `10` | Maximum allowed work hours per day |
| `TICKTICK_LUNCH_THRESHOLD` | `6` | Hours after which lunch break is deducted |
| `TICKTICK_LUNCH_DURATION` | `30` | Lunch break duration in minutes |
| `TICKTICK_HOST` | `0.0.0.0` | Server bind address |
| `TICKTICK_PORT` | `8000` | Server port |

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
- Current work time
- Start time
- Number of pauses and total pause duration
- Lunch break status
- Earliest time you can leave (reaching daily minimum)
- Latest time you should leave (max hours limit)
- Remaining time to reach daily requirement

### Statistics Page

View your work history including:
- This week's total hours and progress toward weekly goal
- This month's statistics
- List of recent completed sessions

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/status` | Get current timer status |
| `POST` | `/api/start` | Start a new session |
| `POST` | `/api/pause` | Pause the current session |
| `POST` | `/api/continue` | Resume from pause |
| `POST` | `/api/stop` | Stop and save session |
| `POST` | `/api/reset` | Discard current session |
| `GET` | `/api/statistics/summary` | Get weekly/monthly stats |

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

## License

MIT
