from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from pathlib import Path
from datetime import datetime, date
from zoneinfo import ZoneInfo
from kiteconnect import KiteConnect
from app.bot_manager import bot_manager
import math

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
KILL_FILE = BASE_DIR / "KILL_SWITCH"

app = FastAPI(title="NIFTY Straddle Bot")
app.add_middleware(SessionMiddleware, secret_key="nifty-straddle-secret-key-98765")
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


def get_atm_straddle(kite):
    """Fetch live Nifty spot and select ATM CE + PE"""
    spot = kite.ltp(["NSE:NIFTY 50"])["NSE:NIFTY 50"]["last_price"]
    step = 50
    atm = int(math.floor(spot / step + 0.5) * step)

    instruments = kite.instruments("NFO")
    today = date.today()

    # Filter Nifty options
    options = [
        i for i in instruments
        if i["name"] == "NIFTY"
        and i["segment"] == "NFO-OPT"
        and i["instrument_type"] in ("CE", "PE")
        and i["expiry"] >= today
    ]

    # Nearest expiry
    expiries = sorted(set(i["expiry"] for i in options))
    if not expiries:
        raise Exception("No expiry found")
    expiry = expiries[0]

    ce = next((i for i in options if i["strike"] == atm and i["expiry"] == expiry and i["instrument_type"] == "CE"), None)
    pe = next((i for i in options if i["strike"] == atm and i["expiry"] == expiry and i["instrument_type"] == "PE"), None)

    if not ce or not pe:
        raise Exception(f"ATM {atm} CE/PE not found")

    # Live LTP
    quotes = kite.ltp([f"NFO:{ce['tradingsymbol']}", f"NFO:{pe['tradingsymbol']}"])
    ce_ltp = quotes[f"NFO:{ce['tradingsymbol']}"]["last_price"]
    pe_ltp = quotes[f"NFO:{pe['tradingsymbol']}"]["last_price"]

    return {
        "spot": spot,
        "atm": atm,
        "expiry": str(expiry),
        "ce": ce,
        "pe": pe,
        "ce_ltp": ce_ltp,
        "pe_ltp": pe_ltp,
    }


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
        return HTMLResponse(f"<h2>Token expired: {e}</h2><a href='/logout'>Login again</a>")

    equity = margins.get("equity", {})
    bot = bot_manager.get_status()

    # Live price update if bot is running
    live_ce = None
    live_pe = None
    event = None
    if bot_manager.running and bot_manager.ce_symbol and bot_manager.pe_symbol:
        try:
            quotes = kite.ltp([
                f"NFO:{bot_manager.ce_symbol}",
                f"NFO:{bot_manager.pe_symbol}"
            ])
            live_ce = quotes[f"NFO:{bot_manager.ce_symbol}"]["last_price"]
            live_pe = quotes[f"NFO:{bot_manager.pe_symbol}"]["last_price"]
            event = bot_manager.on_prices(live_ce, live_pe)
            bot = bot_manager.get_status()  # refresh after tick
        except:
            pass

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "profile": profile,
            "equity": equity,
            "positions": positions,
            "bot": bot,
            "live_ce": live_ce,
            "live_pe": live_pe,
            "event": event,
            "kill_active": KILL_FILE.exists(),
            "now": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        }
    )


@app.post("/bot/start")
async def start_bot(request: Request):
    kite = get_kite(request)
    if not kite:
        return RedirectResponse("/")

    try:
        data = get_atm_straddle(kite)
        bot_manager.start_paper(
            ce_symbol=data["ce"]["tradingsymbol"],
            pe_symbol=data["pe"]["tradingsymbol"],
            ce_token=data["ce"]["instrument_token"],
            pe_token=data["pe"]["instrument_token"],
            ce_price=data["ce_ltp"],
            pe_price=data["pe_ltp"]
        )
    except Exception as e:
        return HTMLResponse(f"<h2>Could not start: {e}</h2><a href='/dashboard'>Back</a>")

    return RedirectResponse("/dashboard", status_code=303)


@app.post("/bot/stop")
async def stop_bot(request: Request):
    bot_manager.stop()
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/kill/activate")
async def activate_kill(request: Request):
    KILL_FILE.touch()
    bot_manager.stop()
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