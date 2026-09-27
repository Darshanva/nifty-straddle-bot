from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from pathlib import Path
import os
from kiteconnect import KiteConnect
from datetime import datetime
from zoneinfo import ZoneInfo

# -------------------- Config --------------------
BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

app = FastAPI(title="NIFTY Straddle Bot")
app.add_middleware(SessionMiddleware, secret_key="change-this-to-a-long-random-secret-key-12345")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

IST = ZoneInfo("Asia/Kolkata")

# -------------------- Helpers --------------------
def get_kite(request: Request) -> KiteConnect | None:
    api_key = request.session.get("api_key")
    access_token = request.session.get("access_token")
    if not api_key or not access_token:
        return None
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


# -------------------- Routes --------------------
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
    login_url = kite.login_url()
    return RedirectResponse(login_url, status_code=303)


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
        positions = kite.positions()
    except Exception as e:
        return HTMLResponse(f"<h2>Token expired or error: {e}</h2><a href='/logout'>Login again</a>")

    equity = margins.get("equity", {})
    net = positions.get("net", [])

    return templates.TemplateResponse(
    request=request,
    name="dashboard.html",
    context={
        "profile": profile,
        "equity": equity,
        "positions": net,
        "now": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    }
)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")


# -------------------- Run --------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)