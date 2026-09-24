import asyncio
import json
from contextlib import asynccontextmanager
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from app import db
from app.pipeline import runner

load_dotenv()

_background_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(lifespan=lifespan)

app.mount("/samples", StaticFiles(directory="test_invoices"), name="samples")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.get("/dashboard")
async def dashboard():
    return FileResponse("static/dashboard.html")


@app.post("/runs")
async def create_run(file: UploadFile):
    pdf_bytes = await file.read()
    run_id = uuid4().hex
    task = asyncio.create_task(run_in_threadpool(runner.execute, run_id, pdf_bytes, file.filename))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"run_id": run_id}


@app.get("/runs/{run_id}/stream")
async def stream_run(run_id: str):
    async def event_generator():
        loop = asyncio.get_running_loop()
        queue = runner.subscribe(run_id, loop)
        try:
            for event in runner.get_history(run_id):
                yield {"data": json.dumps(event)}
                if event["stage"] == "saved" or event["status"] == "failed":
                    return
            while True:
                event = await queue.get()
                yield {"data": json.dumps(event)}
                if event["stage"] == "saved" or event["status"] == "failed":
                    return
        finally:
            runner.unsubscribe(run_id, queue)

    return EventSourceResponse(event_generator())


@app.get("/api/runs")
async def list_runs():
    return db.list_runs()


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@app.post("/api/reset")
async def reset_demo():
    """Clears all saved runs so the sample invoices can be re-run cleanly. Never touches
    data/*.csv (vendors, purchase orders, ledger)."""
    db.reset_runs()
    runner.clear_history()
    return {"status": "ok"}
