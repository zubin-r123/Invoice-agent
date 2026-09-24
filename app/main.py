from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse

load_dotenv()

app = FastAPI()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def index():
    return FileResponse("static/index.html")
