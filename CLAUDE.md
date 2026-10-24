# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TickTick is a self-hosted work time tracking application built with Python and FastAPI. It tracks daily work hours with smart calculations for lunch breaks and leave times.

## Commands

```bash
# All common commands are available via just (run `just` to list them)

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

Backend regression checks run with `just test` or `.venv/bin/python -m unittest discover -s tests -v`. Discovery includes timer/API regressions and deterministic daily calculation tests.

Browser checks run with `uv run --with playwright python tests/browser_smoke.py` and require system Chromium. They use an isolated temporary database and a controlled clock; never point them at production data.

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
│   ├── calculations.py # Shared daily interval math, lunch credit, cap crossings
│   ├── workdays.py     # Load full days with eagerly loaded pause periods
│   ├── statistics.py   # Weekly/monthly aggregations
│   ├── notifications.py # Start reminder + milestone notifications, workday check, loop
│   ├── gotify.py       # Gotify HTTP client with retry classification
│   └── timer.py        # Timer state machine (idle → running → paused)
├── config.py           # Environment variable configuration
├── database.py         # SQLAlchemy setup
├── models.py           # ORM models: WorkSession, PausePeriod, TimerState, NotificationSettings, MilestoneSettings, MilestoneEvent
└── schemas.py          # Pydantic response schemas
```

### Data Flow

1. Frontend (`static/js/timer.js`) polls `/api/status` every 1000ms
2. API layer (`app/routers/api.py`) handles timer controls and statistics
3. Services layer performs business logic and calculations
4. SQLite database persists sessions, pauses, and timer state

### Key Design Decisions
- **Singleton timer state:** One `TimerState` record tracks current session/pause, persists across restarts
- **Timer transactions:** Timer entry points, status polling, and history mutations acquire SQLite `BEGIN IMMEDIATE` before reading state, refresh cached ORM state, and commit or roll back once. Helpers only flush. Do not nest these entry points or pass a session with pending writes. SQLite's legacy SELECT behavior otherwise lets polling mix old timer state with newly committed history.
- **Concurrent start protection:** `start_timer` uses a compare-and-set update on `TimerState.current_session_id` and discards losing session rows if two start requests race
- **Pause audit trail:** Explicit pauses are stored as `PausePeriod` records linked to the session. Gaps between sessions are inferred and never saved as pause records.
- **Daily source of truth:** Derive totals from session timestamps and pauses, never from cached `net_seconds`. New writes cache uncapped actual session work. Merge work intervals before calculating daily work so overlaps count once.
- **Lunch credit:** After actual work exceeds the threshold, credited work equals actual work minus `max(0, lunch allowance - breaks)`. Explicit pauses and gaps contribute together, including short breaks. A gap between sessions contributes only if actual work before it is at or below the threshold (`late_gaps` excludes the rest; they stay in displayed pause totals). A gap after Stop counts only when another session starts; explicit open pauses restore credit live. Derive gaps from raw session spans, not `merge_intervals`, which drops a zero-length active session.
- **Daily limits:** Both the daily target and maximum apply to credited work. Remaining durations include upcoming lunch deductions. Do not cap individual durations or manually corrected history.
- **Cap enforcement:** Status, Pause, Continue, and Stop check the daily cap. `calculate_capped_end_time` walks work/break intervals to find the first crossing, including during a pause. Credit can drop at the lunch threshold, so binary search is invalid. Clip pauses to a backdated end. A qualifying gap never raises credit at Start, so Start only checks `cap_reached`.
- **History corrections during tracking:** Enforce any existing cap before changing the active workday's history. If the correction introduces a cap crossing, stop at the correction time and preserve all active work and pauses. Save the correction and stop atomically so a concurrent poll cannot backdate against partially updated history. Create and update reject future end times.
- **Workday boundaries:** Group by session start date in the existing local timezone. Overnight sessions stay on their starting day; no midnight split. Ignore reset sessions.
- **History and statistics:** Aggregate completed sessions only. `recent_days` includes all sessions for the ten most recent completed dates, with daily overtime shown once. Legacy session overtime fields mean the containing day's overtime. Average start/end times use the first start and last end per day.
- **Frontend history rendering:** One `renderRecentDays` function handles initial JSON and refreshes after all history mutations; do not duplicate the grouped markup in Jinja.
- **HTTP semantics for session endpoints:** `POST/PUT/DELETE /api/sessions*` raise `HTTPException` (404 missing, 409 conflict, 422 validation). Timer controls keep the 200 + `success=false` pattern
- **UI language:** `ActionResponse.message` is German. HTTP error details remain stable English API strings; `readErrorDetail` in `statistics.js` translates these and request-validation errors into German before displaying them.
- **Start reminder:** Lifespan runs a 60s loop only when Gotify URL and token are set. Due on workdays (configured weekdays minus `holidays` calendar) in `[REMINDER_TIME, REMINDER_CUTOFF)` while the timer is idle and no non-reset session exists today. At-most-once: claim `last_start_reminder_date` in `timer_transaction`, send outside it, release the claim only for `GotifyError.retryable` (refused/DNS/5xx/429). Timeouts and crashes after the claim never resend.
- **Milestone notifications:** Optional checkboxes (`MilestoneSettings`, default off) for `min_work` (actual work >= `MIN_WORK_HOURS`; credited drops right after the lunch threshold, so a periodic check would miss it), `daily_target` (`target_reached`) and `daily_max` (`cap_reached`). The same loop checks candidate workdays today, yesterday (overnight sessions stopped after midnight) and the active session's date, enforcing the cap first via `timer.enforce_daily_cap`. One `MilestoneEvent` row per (workday, key) makes delivery at-most-once: `claimed` before sending, deleted only on retryable errors; `disabled`/`paused`/`stale` rows record suppression. A milestone is `stale` once its workday ended more than 30 min ago, which bounds replay and retries. `update_settings` first records milestones reached under the old suppression as `baseline`, so enabling or unpausing never sends a passed milestone. `paused_until` mutes milestones too. The max message stays generic because idle history entries can reach the cap.
- **Notification settings:** `NotificationSettings` singleton (id=1). Expired `paused_until` is normalized to null on every read/write, and PUT accepts past dates as null so stale pages never get a 422.
- **Tests never reach Gotify:** Unit tests patch `gotify.send_message`; `tests/browser_smoke.py` blanks the Gotify env vars before importing the app.
- **Health probe:** Docker healthchecks use `/api/health`, a plain `SELECT 1`. Never point them at `/api/status`: it takes the `BEGIN IMMEDIATE` lock and can auto-stop at the cap.
- **Naive local timestamps:** `datetime.now()` everywhere, so Docker must set `TZ`

## Configuration

All settings via environment variables (prefix `TICKTICK_`):
- `TICKTICK_DB_PATH`: SQLite path (default: `./data/ticktick.db`)
- `TICKTICK_WEEKLY_HOURS`: Weekly target (default: 41)
- `TICKTICK_MAX_DAILY_HOURS`: Daily cap (default: 10)
- `TICKTICK_LUNCH_THRESHOLD`: Hours before lunch deduction (default: 6)
- `TICKTICK_LUNCH_DURATION`: Lunch minutes (default: 30)
- `TICKTICK_GOTIFY_URL`, `TICKTICK_GOTIFY_TOKEN`, `TICKTICK_GOTIFY_PRIORITY` (default: 5): start reminder push target
- `TICKTICK_REMINDER_TIME` / `TICKTICK_REMINDER_CUTOFF`: reminder window (default: 09:00 / 12:00)
- `TICKTICK_REMINDER_WEEKDAYS` (default: `mon,tue,wed,thu,fri`), `TICKTICK_HOLIDAY_COUNTRY` (default: `DE`), `TICKTICK_HOLIDAY_SUBDIV`

Docker also honors `TZ` (default `Europe/Berlin`); without it the container computes all times in UTC.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Liveness probe (`SELECT 1`, no timer transaction) |
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
| GET | `/api/notifications` | Start reminder + milestone settings, today's workday/holiday |
| PUT | `/api/notifications` | Set `enabled`, `paused_until` (past dates become null), optional `notify_*` flags (omitted = unchanged) |
| POST | `/api/notifications/test` | Send Gotify test message (409 unconfigured, 502 Gotify failure) |

## CI/CD

GitHub Actions workflow (`.github/workflows/publish.yml`) builds and pushes Docker images to `ghcr.io/dadav/ticktick` on tag push or release.

Cut a release with `just release 0.8.0`: it runs the tests, bumps the version in `pyproject.toml` and `uv.lock`, commits as `chore: version bump`, and creates the `v0.8.0` tag. The tag is not pushed automatically; `git push && git push origin v0.8.0` triggers the publish workflow.
