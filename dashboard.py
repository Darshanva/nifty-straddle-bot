import streamlit as st
import json
import os
import time
import subprocess
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd

# -------------------- Page Config --------------------
st.set_page_config(
    page_title="NIFTY Straddle Bot",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# -------------------- Custom CSS --------------------
st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        color: #00d4ff;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        color: #888;
        font-size: 1rem;
        margin-bottom: 1.5rem;
    }
    .metric-card {
        background: linear-gradient(135deg, #1e1e2f, #2a2a40);
        padding: 1.2rem;
        border-radius: 12px;
        border: 1px solid #333;
    }
    .stButton>button {
        width: 100%;
        border-radius: 8px;
        height: 3rem;
        font-weight: 600;
    }
    .status-running {
        color: #00ff88;
        font-weight: bold;
    }
    .status-idle {
        color: #ffaa00;
    }
    .status-stopped {
        color: #ff4444;
    }
</style>
""", unsafe_allow_html=True)

# -------------------- Paths & Constants --------------------
IST = ZoneInfo("Asia/Kolkata")
BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "logs"
STATE_DIR = BASE_DIR / "state"
KILL_FILE = BASE_DIR / "KILL_SWITCH"

# -------------------- Helper Functions --------------------
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

def get_latest_logs(n_lines=80):
    if not LOG_DIR.exists():
        return "No logs yet."
    files = sorted(LOG_DIR.glob("straddle_*.log"), reverse=True)
    if not files:
        return "No logs yet."
    try:
        content = files[0].read_text(encoding="utf-8")
        lines = content.strip().split("\n")
        return "\n".join(lines[-n_lines:])
    except:
        return "Could not read logs."

def is_kill_active():
    return KILL_FILE.exists()

def get_bot_status():
    # Simple status based on latest state file
    if not STATE_DIR.exists():
        return "IDLE", "No state found"
    states = sorted(STATE_DIR.glob("state_*.json"), reverse=True)
    if not states:
        return "IDLE", "No state found"
    try:
        with open(states[0]) as f:
            data = json.load(f)
        status = data.get("status", "UNKNOWN")
        return status, data
    except:
        return "IDLE", {}

# -------------------- Sidebar --------------------
with st.sidebar:
    st.markdown("## ⚙️ Controls")
    
    st.markdown("### Mode")
    mode = st.radio("Trading Mode", ["PAPER", "LIVE"], index=0, horizontal=True)
    
    st.markdown("### Position Size")
    lots = st.number_input("Lots", min_value=1, max_value=20, value=1, step=1)
    
    st.markdown("---")
    
    st.markdown("### Kill Switch")
    col_k1, col_k2 = st.columns(2)
    with col_k1:
        if st.button("🔴 ACTIVATE", use_container_width=True):
            KILL_FILE.touch()
            st.success("Kill Switch ON")
            st.rerun()
    with col_k2:
        if st.button("🟢 DEACTIVATE", use_container_width=True):
            if KILL_FILE.exists():
                KILL_FILE.unlink()
            st.success("Kill Switch OFF")
            st.rerun()
    
    kill_status = "🔴 ACTIVE" if is_kill_active() else "🟢 OFF"
    st.info(f"Kill Switch: **{kill_status}**")
    
    st.markdown("---")
    st.markdown("### Quick Actions")
    if st.button("🔄 Refresh Dashboard", use_container_width=True):
        st.rerun()
    
    st.markdown("---")
    st.caption("NIFTY 09:20 Short Straddle Bot")
    st.caption("Local Paper Trading Dashboard")

# -------------------- Main Header --------------------
st.markdown('<div class="main-header">📈 NIFTY Straddle Command Center</div>', unsafe_allow_html=True)
st.markdown(f'<div class="sub-header">Real-time monitoring • {datetime.now(IST).strftime("%A, %d %B %Y %H:%M IST")}</div>', unsafe_allow_html=True)

# -------------------- Top Metrics --------------------
status, state_data = get_bot_status()
summary = get_latest_summary()

m1, m2, m3, m4, m5 = st.columns(5)

with m1:
    st.metric("Bot Status", status)

with m2:
    st.metric("Mode", mode)

with m3:
    st.metric("Kill Switch", "ACTIVE" if is_kill_active() else "OFF")

with m4:
    pnl = summary.get("gross_pnl", 0) if summary else 0
    st.metric("Last Gross P&L", f"₹ {pnl}")

with m5:
    net = summary.get("net_pnl_estimate", 0) if summary else 0
    st.metric("Last Net P&L", f"₹ {net}")

st.markdown("---")

# -------------------- Main Content --------------------
left, right = st.columns([1.4, 1])

with left:
    st.subheader("📊 Latest Trade Summary")
    
    if summary:
        c1, c2, c3 = st.columns(3)
        c1.metric("Date", summary.get("date", "-"))
        c2.metric("Strike", summary.get("strike", "-"))
        c3.metric("Exit Reason", summary.get("exit_reason", "-"))
        
        st.markdown("#### Entry Legs")
        if "entry" in summary:
            st.dataframe(pd.DataFrame(summary["entry"]), use_container_width=True, hide_index=True)
        
        st.markdown("#### Exit Legs")
        if "exit" in summary:
            st.dataframe(pd.DataFrame(summary["exit"]), use_container_width=True, hide_index=True)
        
        with st.expander("Full Summary JSON"):
            st.json(summary)
    else:
        st.info("No trade has been executed yet. Run the bot on a trading day.")

with right:
    st.subheader("📜 Live Logs")
    
    log_content = get_latest_logs(100)
    st.text_area(
        "Recent Logs (last 100 lines)",
        value=log_content,
        height=420,
        disabled=True,
        label_visibility="collapsed"
    )
    
    if st.button("🔄 Refresh Logs"):
        st.rerun()

st.markdown("---")

# -------------------- Instructions --------------------
with st.expander("📖 How to use this dashboard"):
    st.markdown("""
    ### Daily Workflow
    1. Morning → Run `python login.py` in terminal and set Access Token
    2. Around 09:05–09:15 → Run `python main.py` in terminal
    3. Open this dashboard to monitor
    4. Use Kill Switch if you want to stop the bot immediately

    ### Notes
    - This dashboard is for **monitoring**
    - Actual trading is still done by `main.py`
    - Currently running in **PAPER** mode (safe)
    - Switch to LIVE only after thorough testing
    """)

st.caption("Built for local use • Paper trading recommended • v2.0")