#!/usr/bin/env python3
"""
X (Twitter) OAuth 2.0 Authorization Code with PKCE — Local Dev Helper
- Uses a local HTTP server to complete the PKCE flow.
- Reads config from .env (X_CLIENT_ID, X_REDIRECT_URI, optional X_SCOPES).
- Requests scopes: tweet.read users.read like.read offline.access by default.
- Saves tokens to x_tokens.json (access_token, refresh_token, expires_in, scope, token_type).

Usage:
  1) Ensure your X App has OAuth 2.0 enabled and register the redirect URI (must match X_REDIRECT_URI).
  2) Put credentials in a .env next to this script:
        X_CLIENT_ID=your_client_id
        X_REDIRECT_URI=http://127.0.0.1:8765/callback
        X_SCOPES=tweet.read users.read like.read offline.access
  3) Run: python x_pkce_auth.py
  4) After success, tokens are saved to x_tokens.json
  5) To refresh later: python x_pkce_auth.py --refresh

Security notes:
- This is a local dev helper. Do not commit real tokens to source control.
- For production, store tokens in a secure vault and use HTTPS redirect URIs.

Tested on: Python 3.10+
"""

import os
import sys
import json
import time
import base64
import hashlib
import secrets
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

try:
    from dotenv import load_dotenv
    import requests
except ImportError as e:
    print("Missing dependency:", e)
    print("Run: pip install -r requirements.txt")
    sys.exit(1)

AUTH_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
REVOKE_URL = "https://api.x.com/2/oauth2/revoke"

DEFAULT_SCOPES = "tweet.read users.read like.read offline.access"

ROOT = Path(__file__).resolve().parent
TOKENS_PATH = ROOT / "x_tokens.json"

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")

def gen_code_verifier_challenge() -> tuple[str, str]:
    code_verifier = b64url(secrets.token_bytes(32))
    challenge = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = b64url(challenge)
    return code_verifier, code_challenge

def load_config():
    load_dotenv(dotenv_path=ROOT / ".env")
    client_id = os.getenv("X_CLIENT_ID")
    redirect_uri = os.getenv("X_REDIRECT_URI", "http://localhost:8080/callback")
    scopes = os.getenv("X_SCOPES", DEFAULT_SCOPES)
    if not client_id:
        print("X_CLIENT_ID is required in .env")
        sys.exit(1)
    return client_id, redirect_uri, scopes

class OAuthHandler(BaseHTTPRequestHandler):
    server_version = "XPKCEServer/1.0"

    def do_GET(self):
        # Parse query
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        # Serve only the configured callback path
        if parsed.path != self.server.callback_path:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")
            return

        # CSRF protection: state check
        state = query.get("state", [None])[0]
        code = query.get("code", [None])[0]

        if state != self.server.expected_state or not code:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Invalid state or missing code")
            self.server.error = "invalid_state_or_code"
            return

        # Success
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"You can close this tab and return to the terminal.")
        self.server.auth_code = code

    def log_message(self, format, *args):
        # Silence default logging
        return

def start_local_server(redirect_uri: str, expected_state: str):
    url = urllib.parse.urlparse(redirect_uri)
    host = url.hostname or "localhost"
    port = url.port or 8080
    callback_path = url.path or "/callback"

    server = OAuthHTTPServer((host, port), OAuthHandler)
    server.expected_state = expected_state
    server.callback_path = callback_path
    server.auth_code = None
    server.error = None

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server

class OAuthHTTPServer(HTTPServer):
    """Just to attach some state to the server instance"""
    pass

def build_authorize_url(client_id: str, redirect_uri: str, scopes: str, code_challenge: str, state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

def exchange_code_for_tokens(code: str, client_id: str, redirect_uri: str, code_verifier: str) -> dict:
    data = {
        "code": code,
        "grant_type": "authorization_code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    # If this app is configured as a confidential client, X expects Basic auth.
    client_secret = os.getenv("X_CLIENT_SECRET")
    if client_secret:
        import base64
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers["Authorization"] = f"Basic {basic}"
    resp = requests.post(TOKEN_URL, data=data, headers=headers, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Token exchange failed ({resp.status_code}): {resp.text}")
    return resp.json()

def refresh_tokens(client_id: str, refresh_token: str) -> dict:
    data = {
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
        "client_id": client_id,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    client_secret = os.getenv("X_CLIENT_SECRET")
    if client_secret:
        import base64
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers["Authorization"] = f"Basic {basic}"
    resp = requests.post(TOKEN_URL, data=data, headers=headers, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Refresh failed ({resp.status_code}): {resp.text}")
    return resp.json()

def save_tokens(tokens: dict):
    TOKENS_PATH.write_text(json.dumps(tokens, indent=2))
    print(f"Saved tokens -> {TOKENS_PATH}")

def mask(s: str, keep: int = 4) -> str:
    if not s:
        return ""
    return s[:keep] + "…" + s[-keep:]

def main():
    client_id, redirect_uri, scopes = load_config()

    if "--refresh" in sys.argv:
        if not TOKENS_PATH.exists():
            print("No existing tokens to refresh. Run initial auth first.")
            sys.exit(1)
        data = json.loads(TOKENS_PATH.read_text())
        rt = data.get("refresh_token")
        if not rt:
            print("No refresh_token found. Ensure 'offline.access' was included in scopes.")
            sys.exit(1)
        print("Refreshing access token…")
        new_tokens = refresh_tokens(client_id, rt)
        # Preserve original refresh token if none returned (provider-dependent)
        if "refresh_token" not in new_tokens:
            new_tokens["refresh_token"] = rt
        save_tokens(new_tokens)
        print("New access_token:", mask(new_tokens.get("access_token", "")))
        print("Scope:", new_tokens.get("scope"))
        print("Expires in (s):", new_tokens.get("expires_in"))
        return

    state = b64url(secrets.token_bytes(16))
    code_verifier, code_challenge = gen_code_verifier_challenge()

    server = start_local_server(redirect_uri, state)
    auth_url = build_authorize_url(client_id, redirect_uri, scopes, code_challenge, state)
    print("Opening browser for consent…")
    print(auth_url)
    webbrowser.open(auth_url)

    # Wait for callback
    print("Waiting for authorization… (Ctrl+C to abort)")
    for _ in range(600):  # 10 minutes
        if getattr(server, "auth_code", None) or getattr(server, "error", None):
            break
        time.sleep(1)

    if server.error or not server.auth_code:
        print("Authorization failed or timed out.")
        sys.exit(1)

    code = server.auth_code
    server.shutdown()

    print("Exchanging code for tokens…")
    tokens = exchange_code_for_tokens(code, client_id, redirect_uri, code_verifier)
    save_tokens(tokens)

    print("Access token:", mask(tokens.get("access_token", "")))
    if "refresh_token" in tokens:
        print("Refresh token:", mask(tokens.get("refresh_token", "")))
    else:
        print("No refresh_token returned. Did you include 'offline.access' in scopes?")
    print("Scope:", tokens.get("scope"))
    print("Expires in (s):", tokens.get("expires_in"))
    print("\nNext step: use the access token for user-context X API calls, e.g., GET /2/users/:id/liked_tweets")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
