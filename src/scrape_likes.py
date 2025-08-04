#!/usr/bin/env python3
"""
scrape_likes.py — Pulls liked tweets for a user (Free Tier) and writes normalized JSONL.

Usage examples:
    python scrape_likes.py --full --max-pages 3
    python scrape_likes.py --since --max-pages 1 --sample 2000
    python scrape_likes.py --enrich 10 --enrich-mode threads --enrich-since 14
"""

import argparse
import os
import sys
import json
import time
import uuid
import random
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional

import requests
from dotenv import load_dotenv
from zoneinfo import ZoneInfo
from tqdm import tqdm

# ------------------------------- CONFIG -------------------------------- #

API_BASE = "https://api.x.com/2"
LIKES_ENDPOINT = "/users/{user_id}/liked_tweets"
DEFAULT_TWEET_FIELDS = (
    "created_at,public_metrics,entities,lang,possibly_sensitive,"
    "referenced_tweets,author_id,conversation_id"
)
DEFAULT_USER_FIELDS = (
    "username,name,description,public_metrics,verified,created_at"
)
EXPANSIONS = "author_id"

# ------------------------------- HELPERS -------------------------------- #


def jitter(seconds: float = 1.0) -> float:
    return seconds * (1 + random.random() * 0.1)


def b64mask(token: str, keep: int = 4) -> str:
    if not token:
        return ""
    return token[:keep] + "…" + token[-keep:]


def now_iso() -> str:
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()


def ensure_dir(path: Path):
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)


# ------------------------------- STATE ---------------------------------- #


class State:
    def __init__(self, path: Path):
        self.path = path
        if path.exists():
            self._data = json.loads(path.read_text())
        else:
            self._data = {}

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value

    def save(self):
        self.path.write_text(json.dumps(self._data, indent=2))


# ------------------------------- TOKEN ---------------------------------- #


def load_tokens(tokens_path: Path) -> Dict[str, Any]:
    if not tokens_path.exists():
        print("x_tokens.json not found. Run x_pkce_auth.py first.", file=sys.stderr)
        sys.exit(1)
    tokens = json.loads(tokens_path.read_text())
    return tokens


def refresh_tokens(tokens_path: Path, client_id: str, client_secret: Optional[str]):
    data = json.loads(tokens_path.read_text())
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        print("No refresh_token present; re-run the PKCE flow to get one.")
        sys.exit(1)
    token_url = "https://api.x.com/2/oauth2/token"
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if client_secret:
        import base64

        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers["Authorization"] = f"Basic {basic}"
    resp = requests.post(token_url, data=payload, headers=headers, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Token refresh failed: {resp.text}")
    new_tokens = resp.json()
    if "refresh_token" not in new_tokens:
        new_tokens["refresh_token"] = refresh_token  # preserve
    tokens_path.write_text(json.dumps(new_tokens, indent=2))
    return new_tokens


# ------------------------------- DERIVED FEATURES ----------------------- #


def derive_flags(text: str) -> Dict[str, Any]:
    alpha = sum(1 for c in text if c.isalpha())
    upper = sum(1 for c in text if c.isupper())
    upper_ratio = upper / alpha if alpha else 0.0
    return {
        "contains_question": "?" in text,
        "upper_ratio": round(upper_ratio, 3),
    }


def local_temporal(utc_iso: str, tz: ZoneInfo) -> Dict[str, int]:
    dt_utc = datetime.fromisoformat(utc_iso.replace("Z", "+00:00")).astimezone(tz)
    return {"hour_local": dt_utc.hour, "weekday": dt_utc.weekday()}


# ------------------------------- CORE ----------------------------------- #


class LikesScraper:
    def __init__(
        self,
        access_token: str,
        output_dir: Path,
        state: State,
        tz: ZoneInfo,
        max_pages: int,
        mode_full: bool,
        since: bool,
    ):
        self.token = access_token
        self.out = output_dir
        self.state = state
        self.tz = tz
        self.max_pages = max_pages
        self.mode_full = mode_full
        self.since = since

        ensure_dir(self.out / "raw")
        ensure_dir(self.out / "data")

        # File handles
        self.tweets_fp = open(self.out / "data" / "tweets.jsonl", "a", encoding="utf-8")
        self.users_fp = open(self.out / "data" / "users.jsonl", "a", encoding="utf-8")

        self.seen_tweet_ids = set()
        self.seen_user_ids = set()

    # -------------- Networking helpers ----------------#

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def _get(self, url: str, params: Dict[str, Any]):
        resp = requests.get(url, headers=self._headers(), params=params, timeout=30)
        if resp.status_code == 401:
            raise RuntimeError("401 Unauthorized – access token expired.")
        if resp.status_code == 429:
            reset = int(resp.headers.get("x-rate-limit-reset", time.time() + 60))
            sleep_for = max(reset - int(time.time()), 0) + 5
            print(f"429 Rate limit. Sleeping {sleep_for}s …")
            time.sleep(sleep_for)
            return self._get(url, params)
        resp.raise_for_status()
        return resp

    # -------------- Storage helpers -------------------#

    def _write_jsonl(self, fp, obj):
        fp.write(json.dumps(obj, ensure_ascii=False) + "\n")
        fp.flush()

    # -------------- Main fetch loop -------------------#

    def run(self):
        user_id = self.state.get("user_id")
        if not user_id:
            # Attempt to fetch /users/me
            me = requests.get(
                f"{API_BASE}/users/me", headers=self._headers(), timeout=30
            ).json()
            user_id = me["data"]["id"]
            self.state.set("user_id", user_id)
            self.state.save()
        print(f"Scraping likes for user_id={user_id}")

        params = {
            "max_results": 100,
            "expansions": EXPANSIONS,
            "tweet.fields": DEFAULT_TWEET_FIELDS,
            "user.fields": DEFAULT_USER_FIELDS,
        }
        if self.since:
            since_id = self.state.get("newest_like_id")
            if since_id:
                params["since_id"] = since_id
        page = 0
        next_token = None

        pbar = tqdm(total=self.max_pages, desc="pages", unit="page")
        while True:
            if self.max_pages and page >= self.max_pages:
                break
            if next_token:
                params["pagination_token"] = next_token
            elif "pagination_token" in params:
                params.pop("pagination_token")

            url = f"{API_BASE}{LIKES_ENDPOINT.format(user_id=user_id)}"
            resp = self._get(url, params)
            data = resp.json()
            raw_path = (
                self.out
                / "raw"
                / f"page_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{page:04d}.json"
            )
            raw_path.write_text(resp.text)

            includes_users = {
                u["id"]: u for u in data.get("includes", {}).get("users", [])
            }

            for tweet in data.get("data", []):
                tid = tweet["id"]
                if tid in self.seen_tweet_ids:
                    continue
                # Derived flags
                flags = derive_flags(tweet["text"])
                temporal = local_temporal(tweet["created_at"], self.tz)
                tweet["flags"] = flags
                tweet["temporal"] = temporal
                tweet["source_page"] = raw_path.name
                self._write_jsonl(self.tweets_fp, tweet)
                self.seen_tweet_ids.add(tid)
                # User
                uid = tweet["author_id"]
                if uid in includes_users and uid not in self.seen_user_ids:
                    self._write_jsonl(self.users_fp, includes_users[uid])
                    self.seen_user_ids.add(uid)

            meta = data.get("meta", {})
            next_token = meta.get("next_token")

            # Update cursors
            if page == 0 and "data" in data and data["data"]:
                self.state.set("newest_like_id", data["data"][0]["id"])
            if not meta.get("next_token"):
                # Last page sets oldest
                last_items = data.get("data", [])
                if last_items:
                    self.state.set("oldest_like_id", last_items[-1]["id"])

            page += 1
            pbar.update(1)
            if not next_token:
                break

        pbar.close()
        self.state.set("last_run_at", now_iso())
        self.state.set("pages_fetched", self.state.get("pages_fetched", 0) + page)
        self.state.save()
        self.tweets_fp.close()
        self.users_fp.close()
        print(f"Finished. Pages fetched this run: {page}")

# ------------------------------- CLI ------------------------------------#


def main():
    parser = argparse.ArgumentParser(description="Scrape liked tweets into JSONL.")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--full", action="store_true", help="Backfill all pages.")
    g.add_argument("--since", action="store_true", help="Fetch newest likes since last run.")
    parser.add_argument("--max-pages", type=int, default=0, help="Max pages per run (0 = no limit).")
    parser.add_argument("--out-dir", type=Path, default=Path.cwd(), help="Output directory root.")
    parser.add_argument("--sample", type=int, default=0, help="Write a sample JSONL of N tweets.")
    parser.add_argument("--tz", type=str, default="America/Los_Angeles", help="Timezone for temporal features.")
    args = parser.parse_args()

    load_dotenv()
    tz = ZoneInfo(args.tz)

    tokens_path = Path("x_tokens.json")
    tokens = load_tokens(tokens_path)
    access_token = tokens["access_token"]

    # Validate token expiry (simple check)
    # (X's tokens are JWT; but Free tier returns expires_in; rely on refresh ahead of scrape)
    # We'll refresh only on 401 in runtime.

    state_path = args.out_dir / "data" / "state.json"
    ensure_dir(state_path.parent)
    state = State(state_path)

    scraper = LikesScraper(
        access_token=access_token,
        output_dir=args.out_dir,
        state=state,
        tz=tz,
        max_pages=args.max_pages,
        mode_full=args.full,
        since=args.since,
    )
    try:
        scraper.run()
    except RuntimeError as e:
        if "401" in str(e):
            # Token expired: attempt refresh once
            client_id = os.getenv("X_CLIENT_ID")
            client_secret = os.getenv("X_CLIENT_SECRET")
            print("Attempting token refresh …")
            tokens = refresh_tokens(tokens_path, client_id, client_secret)
            scraper.token = tokens["access_token"]
            scraper.run()
        else:
            raise


if __name__ == "__main__":
    main()
