from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from pathlib import Path
import os
import json
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo
from kiteconnect import KiteConnect

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
LOG_DIR = BASE_DIR / "logs"
STATE_DIR = BASE_DIR / "state"
KILL_FILE = BASE_DIR / "KILL_SWITCH"

app = FastAPI(title="NIFTY Straddle Bot")
app.add_middleware(SessionMiddleware, secret_key="nifty-straddle-secret-key-change-this-in-production-98765")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
IST = ZoneInfo("Asia/Kolkata")


def get_kite(request: Request):
    api_key = request.session.get("api_key")
    access_token = request.session.get("access_token")
    if not api_key or not access_token:
        return None
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


def get_latest_summary():
    if not LOG_DIR.exists():
        return None
    files = sorted(LOG_DIR.glob("summary_*.json"), reverse=True)
    if not files:
        return None
    try:
        with open(files[0], encoding="utf-8") as f:
            return json.load(f)
    except:
        return None


def get_latest_logs(n=80):
    if not LOG_DIR.exists():
        return "No logs yet."
    files = sorted(LOG_DIR.glob("straddle_*.log"), reverse=True)
    if not files:
        return "No logs yet."
    try:
        content = files[0].read_text(encoding="utf-8")
        lines = content.strip().splitlines()
        return "\n".join(lines[-n:])
    except:
        return "Could not read logs."


def get_bot_status():
    if KILL_FILE.exists():
        return "KILLED", "Kill switch is active"
    if not STATE_DIR.exists():
        return "IDLE", "No state found"
    states = sorted(STATE_DIR.glob("state_*.json"), reverse=True)
    if not states:
        return "IDLE", "No state found"
    try:
        with open(states[0]) as f:
            data = json.load(f)
        return data.get("status", "IDLE"), data
    except:
        return "IDLE", {}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if request.session.get("access_token"):
        return RedirectResponse("/dashboard")
    return templates.TemplateResponse(request=request, name="login.html")


@app.post("/login")
async def login(request: Request, api_key: str = Form(...), api_secret: str = Form(...)):
    request.session["api_key"] = api_key.strip()
    request.session["api_secret"] = api_secret.strip()
    kite = KiteConnect(api_key=api_key.strip())
    return RedirectResponse(kite.login_url(), status_code=303)


@app.get("/callback")
async def callback(request: Request, request_token: str = None, status: str = None):
    if status != "success" or not request_token:
        return HTMLResponse("<h2>Login failed. <a href='/'>Try again</a></h2>")
    api_key = request.session.get("api_key")
    api_secret = request.session.get("api_secret")
    if not api_key or not api_secret:
        return HTMLResponse("<h2>Session expired. <a href='/'>Login again</a></h2>")
    kite = KiteConnect(api_key=api_key)
    try:
        data = kite.generate_session(request_token, api_secret=api_secret)
        request.session["access_token"] = data["access_token"]
        request.session["user_id"] = data.get("user_id")
        request.session["user_name"] = data.get("user_name")
        return RedirectResponse("/dashboard", status_code=303)
    except Exception as e:
        return HTMLResponse(f"<h2>Error: {e}</h2><a href='/'>Try again</a>")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    kite = get_kite(request)
    if not kite:
        return RedirectResponse("/")
    try:
        profile = kite.profile()
        margins = kite.margins()
        positions = kite.positions().get("net", [])
    except Exception as e:
        return HTMLResponse(f"<h2>Token expired or error: {e}</h2><a href='/logout'>Login again</a>")

    equity = margins.get("equity", {})
    status, state_data = get_bot_status()
    summary = get_latest_summary()
    logs = get_latest_logs()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "profile": profile,
            "equity": equity,
            "positions": positions,
            "bot_status": status,
            "summary": summary,
            "logs": logs,
            "kill_active": KILL_FILE.exists(),
            "now": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        }
    )


@app.post("/kill/activate")
async def activate_kill(request: Request):
    KILL_FILE.touch()
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/kill/deactivate")
async def deactivate_kill(request: Request):
    if KILL_FILE.exists():
        KILL_FILE.unlink()
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)