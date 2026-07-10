# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TickTick is a self-hosted work time tracking application built with Python and FastAPI. It tracks daily work hours with smart calculations for lunch breaks and leave times.

## Commands

```bash
# Install dependencies
uv sync

# Run development server with hot reload
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Run production server
uv run uvicorn main:app --host 0.0.0.0 --port 8000

# Docker deployment
docker compose up -d
docker compose down
```

Note: No test suite, linting, or type checking is currently configured.

Backend regression checks are available via:

```bash
.venv/bin/python -m unittest -v tests/test_backend_fixes.py
```

Current regression coverage includes:
- active start race handling in `start_timer`
- statistics excluding future completed sessions
- statistics deducting lunch once per day across multiple sessions
- session details 404 behavior
- live net/overtime values for active session details
- delete response status consistency while timer remains active
- delete raises 404 (missing) / 409 (active session)
- auto-stop when net work reaches MAX_DAILY_HOURS
- auto-stop sets `end_time` to the moment the cap was reached (incl. pauses)
- net work seconds capped at MAX_DAILY_SECONDS
- session update (end_time change + net_seconds recalculation)
- session update raises 409 for active and non-completed sessions
- session update validates start < end (422)
- session update validates against pause boundaries (422)
- session update returns 404 for missing sessions
- manual session creation + validation (start < end, past only, format)

## Architecture

### Tech Stack
- **Backend:** FastAPI + Uvicorn (ASGI)
- **Database:** SQLite with SQLAlchemy ORM
- **Templates:** Jinja2
- **Frontend:** Vanilla JavaScript + HTML + CSS (no build step)
- **Package Manager:** uv

### Code Organization

```
app/
├── routers/
│   ├── api.py          # REST API endpoints (timer controls, statistics)
│   └── pages.py        # HTML page rendering
├── services/
│   ├── calculations.py # Time math (net work, lunch thresholds, leave times)
│   ├── statistics.py   # Weekly/monthly aggregations
│   └── timer.py        # Timer state machine (idle → running → paused)
├── config.py           # Environment variable configuration
├── database.py         # SQLAlchemy setup
├── models.py           # ORM models: WorkSession, PausePeriod, TimerState
└── schemas.py          # Pydantic response schemas
```

### Data Flow

1. Frontend (`static/js/timer.js`) polls `/api/status` every 1000ms
2. API layer (`app/routers/api.py`) handles timer controls and statistics
3. Services layer performs business logic and calculations
4. SQLite database persists sessions, pauses, and timer state

### Key Design Decisions
- **Singleton timer state:** One `TimerState` record tracks current session/pause, persists across restarts
- **Concurrent start protection:** `start_timer` uses a compare-and-set update on `TimerState.current_session_id` and discards losing session rows if two start requests race
- **Pause audit trail:** Every break is stored as a `PausePeriod` linked to the session
- **Automatic lunch deduction:** 30 min deducted after 6 hours of *net* work time
- **Per-day statistics:** `summarize_sessions` groups sessions by date, so lunch and the daily target are applied once per day, not once per session
- **Auto-stop backdating:** `_auto_stop_session` uses `calculate_capped_end_time` to set `end_time` to the moment the daily cap was actually reached, not the poll time
- **HTTP semantics for session endpoints:** `POST/PUT/DELETE /api/sessions*` raise `HTTPException` (404 missing, 409 conflict, 422 validation). Timer controls keep the 200 + `success=false` pattern
- **Naive local timestamps:** `datetime.now()` everywhere, so Docker must set `TZ`

## Configuration

All settings via environment variables (prefix `TICKTICK_`):
- `TICKTICK_DB_PATH`: SQLite path (default: `./data/ticktick.db`)
- `TICKTICK_WEEKLY_HOURS`: Weekly target (default: 41)
- `TICKTICK_MAX_DAILY_HOURS`: Daily cap (default: 10)
- `TICKTICK_LUNCH_THRESHOLD`: Hours before lunch deduction (default: 6)
- `TICKTICK_LUNCH_DURATION`: Lunch minutes (default: 30)

Docker also honors `TZ` (default `Europe/Berlin`); without it the container computes all times in UTC.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/status` | Current timer state + calculations |
| POST | `/api/start` | Start new session |
| POST | `/api/pause` | Pause current session |
| POST | `/api/continue` | Resume from pause |
| POST | `/api/stop` | Stop and save session |
| POST | `/api/reset` | Discard current session |
| GET | `/api/statistics/summary` | Weekly/monthly stats |
| POST | `/api/sessions` | Manually add a completed past session (201; 422 on invalid input) |
| GET | `/api/sessions/{id}` | Get session details with pause periods (404 if missing) |
| PUT | `/api/sessions/{id}` | Update start/end time of a completed session (404/409/422) |
| DELETE | `/api/sessions/{id}` | Delete a non-active session by ID (404 missing, 409 if active) |

## CI/CD

GitHub Actions workflow (`.github/workflows/publish.yml`) builds and pushes Docker images to `ghcr.io/dadav/ticktick` on tag push or release.
