# ✅ MVP Execution Checklist — X Likes Analyzer (Free Tier, JSONL v1)

**Owner:** Sahil • **Target:** 3-day MVP • **Scope lock:** JSONL only; selective enrichment budgeted; no dashboard

> Use this as a living checklist. Items are grouped by milestone. Check off only when the acceptance test passes.


---

## M0 — Ingestion Pipeline (`scrape_likes.py`)

### Repo & Environment
- [x] **Repo skeleton** created: `src/`, `data/`, `raw/`, `notebooks/`, `scripts/`, `docs/`
- [x] `.gitignore` ignores `x_tokens.json`, `data/`, `raw/`, `*.env`, `*.ipynb_checkpoints`
- [x] Python **3.10+** confirmed (`python --version`)
- [x] Virtualenv/conda env set up; `requirements.txt` installed
- [x] **Timezone** default `America/Los_Angeles` defined (config/env)

### Auth & Config (pre-reqs)
- [x] `x_pkce_auth.py` executed; `x_tokens.json` exists and contains `access_token` (and `refresh_token` if `offline.access`)
- [x] `.env` prepared with optional `X_CLIENT_ID`, `X_CLIENT_SECRET`, `TZ`
- [x] **Sanity curl**: `/2/users/me` returns your user id (stores to `state.json`)

### CLI Interface (spec parity)
- [x] Argparse supports: `--full`, `--since`, `--max-pages N`, `--out-dir PATH`, `--sample N`, `--enrich N`, `--enrich-mode {threads,media,both}`, `--enrich-since DAYS`, `--tz`

### Token Handling
- [x] Loads tokens from `x_tokens.json`
- [x] On **401**, attempts refresh; persists updated tokens
- [ Logs mask tokens (head/tail only)

### Fetch Core: Liked Tweets (Free tier)
- [ ] Endpoint: `/2/users/:id/liked_tweets`
- [ ] Params: `max_results=100`, `expansions=author_id`
- [ ] `tweet.fields=created_at,public_metrics,entities,lang,possibly_sensitive,referenced_tweets,author_id,conversation_id`
- [ ] `user.fields=username,name,description,public_metrics,verified,created_at`
- [ ] Handles pagination via `meta.next_token`
- [ ] **Raw dumps** for each page saved to `raw/page_YYYYMMDD_HHMMSS_<n>.json`

### State & Cursors
- [ ] `data/state.json` initialized with `user_id`, `newest_like_id`, `oldest_like_id`, `last_run_at`, `pages_fetched`
- [ ] **Backfill mode (`--full`)** updates `oldest_like_id` until no `next_token`
- [ ] **Incremental mode (`--since`)** uses `since_id=newest_like_id`; updates `newest_like_id`

### JSONL Normalization Writers
- [ ] **tweets.jsonl** append-only; 1 line per tweet
- [ ] **users.jsonl** dedup by `id` (in-memory set or on-disk index)
- [ ] **Idempotency**: dedupe `tweet.id` (LRU/set against last N ids or small Bloom)
- [ ] `source_page` written to each tweet for traceability

### Derived Features (cheap)
- [ ] `text_len`, `hashtag_count`, `mention_count`, `url_count`
- [ ] `contains_question` (presence of `?`)
- [ ] `upper_ratio` (A–Z chars / total alpha)
- [ ] `is_reply_or_rt` from `referenced_tweets`
- [ ] Localized `hour_local`, `weekday` (respect `--tz`)

### Rate Limits & Backoff
- [ ] Parse `x-rate-limit-remaining` and `x-rate-limit-reset`
- [ ] On **429** or `remaining==0`, sleep to reset + jitter
- [ ] `--max-pages` hard cap respected per run
- [ ] Progress logs include ETA and pages fetched

### Sampling Support
- [ ] `--sample N` writes `data/sample_tweets.jsonl` (random or most-recent strategy, documented)

### M0 Acceptance Tests
- [ ] **Smoke run:** `--max-pages 1` produces `raw/page_*.json` and appends to `tweets.jsonl`
- [ ] **Backfill run:** `--full --max-pages 3` increases `pages_fetched`; `oldest_like_id` updated
- [ ] **Since run:** `--since --max-pages 1` fetches only newer likes; `newest_like_id` advances
- [ ] **Schema check:** all required fields present on sample of 100 tweets
- [ ] **Idempotency test:** rerun same page; no duplicate `id`s in `tweets.jsonl`


---

## M1 — Analysis (`notebooks/analyze.ipynb`) + `report.md`

### Data Loading & Hygiene
- [ ] Load `tweets.jsonl` and `users.jsonl` efficiently
- [ ] Validate schema & counts; assert uniqueness of `tweet.id`
- [ ] Quick EDA: distributions of `text_len`, `hour_local`, `weekday`

### Frequencies & N-grams
- [ ] Top **authors** by like frequency (table)
- [ ] Top **hashtags** (table)
- [ ] Top **unigrams/bigrams/trigrams** with stopword removal; show exemplars

### Embeddings & Clustering (sampled)
- [ ] Config switch: **OpenAI** vs **SentenceTransformers** (local)
- [ ] Sample `N ≤ 2000` tweets (`data/sample_tweets.jsonl` if present; else most recent)
- [ ] Compute embeddings; cluster (KMeans or HDBSCAN)
- [ ] Cluster labeling via top n-grams + **LLM summary of centroid exemplars**
- [ ] Output **cluster cards**: name, rationale, top exemplars, size

### Temporal & Spikes
- [ ] Hour-of-day and Day-of-week heatmaps
- [ ] Spike detection (e.g., 3σ over baseline) — list top spike dates with dominant authors/themes

### Contradiction Heuristics
- [ ] Rules that flag tension (e.g., discipline cluster ∧ late-night likes)
- [ ] Output a short **“tensions”** section with evidence

### Insights Report
- [ ] Generate `out/report.md` including:
  - 8–12 themes + **value drivers** (competence, autonomy, impact, self-respect, etc.)
  - Top 10 authors and why you gravitate to them
  - Temporal patterns and spike-day annotations
  - 3–5 contradictions/tensions with suggested behavioral experiments
- [ ] Export top cluster exemplars (IDs) to a CSV/JSON for quick manual review

### M1 Acceptance Tests
- [ ] Notebook runs top-to-bottom on `data/sample_tweets.jsonl` in < 10 minutes locally
- [ ] `out/report.md` renders without broken sections or empty tables
- [ ] At least **one** contradiction flagged with concrete evidence
- [ ] You can **veto/rename** at least 3 cluster labels and re-render report quickly


---

## M2 — Selective Enrichment (Budgeted Context)

### Candidate Selection
- [ ] Heuristics implemented:
  - Thread head patterns (`🧵`, `(1/`, `1/10`, etc.) OR author known for threads
  - Motivational image proxies (URL present + short text + high `upper_ratio`)
- [ ] CLI: `--enrich N`, `--enrich-mode {threads,media,both}`, `--enrich-since DAYS`

### Minimal Fetch
- [ ] For each candidate, fetch **root** by `conversation_id` (+ optionally 1–2 previous posts)
- [ ] Append short `context` text snippet to tweet record
- [ ] Cache enrichment results to avoid re-fetching

### Budget & Limits
- [ ] Hard cap per run via `--enrich N`
- [ ] Respect rate headers; merge with core backoff logic

### M2 Acceptance Tests
- [ ] `--enrich 10` attaches context to 10 flagged tweets without errors
- [ ] Re-running enrichment does not duplicate context
- [ ] Enriched snippets materially clarify at least **one** cluster label in `report.md`


---

## Cross-Cutting — Quality, Ops, Docs

### Quality & Validation
- [ ] **Schema validator** (lightweight) for `tweets.jsonl` (required keys + types)
- [ ] **Uniqueness** check: no duplicate `tweet.id` in dataset
- [ ] **Timezone** correctness: spot-check 10 items against PDT/PST
- [ ] **Logging**: concise progress logs with pages, rate-limit remaining, resets, ETA

### Ops
- [ ] Recommended runbook in `docs/` with example commands:
  - Backfill: `python scrape_likes.py --full --max-pages 4`
  - Delta: `python scrape_likes.py --since --max-pages 1`
  - Sample: `python scrape_likes.py --sample 2000`
  - Enrich: `python scrape_likes.py --enrich 10 --enrich-mode threads --enrich-since 14`
- [ ] Optional cron/systemd note for daily `--since` runs (document only; not required in MVP)

### Security & Privacy
- [ ] Token masking verified in logs
- [ ] `x_tokens.json` and `data/` not tracked by git
- [ ] Optional redaction toggle for URLs/handles during external embeddings

### Documentation
- [ ] `README.md` with quickstart, prerequisites, common errors, and rate-limit expectations
- [ ] `PRD_X_Likes_Analyzer_MVP.md` checked in under `docs/`
- [ ] `CHANGELOG.md` initialized


---

## Out-of-Scope (Do **not** do in v1)
- [ ] Full conversation crawling or rich media downloads
- [ ] SQLite/DuckDB builds (unless JSONL pain is proven)
- [ ] Streamlit/dashboard UI
- [ ] Per-tweet LLM theming (cluster-level only in MVP)


---

## Final Exit Criteria (Overall DoD)
- [ ] Backfill launched and completed at least **N pages** without errors (respecting Free-tier caps)
- [ ] `data/tweets.jsonl` contains ≥ **2,000** normalized records with derived features
- [ ] `out/report.md` exists and tells a coherent, defensible story (themes, values, authors, temporal, tensions)
- [ ] Selective enrichment improves clarity on at least one ambiguous cluster
- [ ] You can rerun **since-sync + analysis** end-to-end in < **20 minutes** on your machine
