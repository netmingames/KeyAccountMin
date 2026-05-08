"""Sid / KeyAccountMin — FastAPI Skelett (Phase 0).

Nur das Foundation-Geruest: Healthcheck + Begruessungsseite. Echte
Item-/Translation-Endpoints folgen mit NT-547 ff.

Start (lokal/dev):
    python app.py

Im Betrieb laeuft der Service via Scheduled Task `Sid_KeyAccountMin`
auf devastator:5003. Center-Tile verlinkt direkt auf http://devastator:5003.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

VERSION = "0.1.0"
NAME = "Sid / KeyAccountMin"
ROOT = Path(__file__).resolve().parent

app = FastAPI(title=NAME, version=VERSION)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")


@app.get("/api/health")
def health() -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "name": NAME,
            "version": VERSION,
            "phase": "0 - Foundation",
            "timestamp": _dt.datetime.now().isoformat(timespec="seconds"),
        }
    )


@app.get("/api/version")
def version() -> dict:
    return {"name": NAME, "version": VERSION}


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "name": NAME, "version": VERSION},
    )


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=5003,
        log_level="info",
        reload=False,
    )
