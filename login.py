"""
login.py — run once every morning
"""
from __future__ import annotations

import os
import sys
from urllib.parse import parse_qs, urlparse

from kiteconnect import KiteConnect


def _extract_token(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("http"):
        values = parse_qs(urlparse(raw).query).get("request_token")
        if not values:
            raise ValueError("No request_token in the URL")
        return values[0]
    return raw


def main() -> None:
    api_key = os.environ.get("KITE_API_KEY", "").strip()
    api_secret = os.environ.get("KITE_API_SECRET", "").strip()
    if not api_key or not api_secret:
        sys.exit("Set KITE_API_KEY and KITE_API_SECRET environment variables first.")
    kite = KiteConnect(api_key=api_key)
    print("\n1) Open and log in:\n   " + kite.login_url())
    request_token = _extract_token(input("\n2) Paste redirect URL or request_token: "))
    session = kite.generate_session(request_token, api_secret=api_secret)
    token = session["access_token"]
    print("\n3) Logged in as", session.get("user_id"))
    print("\nLinux/macOS:\n   export KITE_ACCESS_TOKEN=" + token)
    print("Windows PowerShell:\n   $env:KITE_ACCESS_TOKEN=\"" + token + "\"\n")
    print("Keep this token secret. It is valid for today only.")


if __name__ == "__main__":
    main()