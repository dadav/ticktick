from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.routers import api, pages

app = FastAPI(title="TickTick", description="Work time tracking app")

# Mount static files
static_path = Path(__file__).parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=static_path), name="static")

# Include routers
app.include_router(api.router)
app.include_router(pages.router)


@app.on_event("startup")
def startup_event():
    init_db()


if __name__ == "__main__":
    import uvicorn
    from app.config import HOST, PORT

    uvicorn.run("main:app", host=HOST, port=PORT, reload=True)
