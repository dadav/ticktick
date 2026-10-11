import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import config
from app.database import init_db
from app.routers import api, pages
from app.services.notifications import run_reminder_loop

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    reminder_task = None
    if config.GOTIFY_CONFIGURED:
        reminder_task = asyncio.create_task(run_reminder_loop())
    else:
        logger.info("Gotify not configured; start reminders disabled")
    yield
    if reminder_task is not None:
        reminder_task.cancel()
        with suppress(asyncio.CancelledError):
            await reminder_task


app = FastAPI(title="TickTick", description="Work time tracking app", lifespan=lifespan)

# Mount static files
static_path = Path(__file__).parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=static_path), name="static")

# Include routers
app.include_router(api.router)
app.include_router(pages.router)


if __name__ == "__main__":
    import uvicorn
    from app.config import HOST, PORT

    uvicorn.run("main:app", host=HOST, port=PORT, reload=True)
