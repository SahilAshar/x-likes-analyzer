# PRD — X Likes Analyzer (MVP, Free Tier)

**Owner:** Sahil  
**Date:** 2025‑08‑03  
**Objective:** Extract introspective signals from Sahil’s liked posts on X: dominant themes, value drivers, author affinity, and temporal patterns — without overbuilding.

---

## 1. Goals / Non‑Goals

### Goals
- Ingest liked tweets (Free tier) and persist a normalized dataset.  
- Compute cheap derived features to aid downstream clustering and narrative.  
- Produce a first insights report: top themes, value drivers, author affinity, and time‑based behaviors.

### Non‑Goals (MVP)
- Full conversation graph ingestion.  
- Rich media analysis beyond presence flags.  
- Production dashboard or real‑time sync.

---

## 2. Constraints & Assumptions
| Constraint | Detail |
|------------|--------|
| **API tier** | Free. Low, shifting rate limits; code must honor `x‑rate‑limit‑*` headers. |
| **Scale (current)** | ~9 154 total likes; ~20 new/day. |
| **Privacy** | Tweet text can be sent to external APIs (public data). |
| **Environment** | Python 3.10+ on WSL; TZ = America/Los_Angeles |
| **Backfill** | Staged multi‑day backfill acceptable; no Data Archive path planned. |

---

## 3. Product Outcomes (Definition of Done)

1. **CLI ingestion** (`scrape_likes.py`) that backfills and incrementally syncs likes, writes JSONL outputs and state, stores raw API pages, obeys rate limits, and auto‑refreshes tokens.  
2. **Analysis notebook** (`analyze.ipynb`) that delivers:
   - Top authors, hashtags, n‑grams.  
   - Sampled embeddings ➜ clusters ➜ LLM summaries.  
   - Temporal patterns & spike days.  
   - A 1‑page markdown *Insights Report*.  
3. **Selective enrichment** (budgeted) for threads/media when it materially changes interpretation.

---

## 4. User Stories
- **Backfill & sync**: run `scrape_likes.py` to fetch likes and append data safely.  
- **Insights**: generate a narrative about actual drivers within 48 h of starting backfill.  
- **Selective context**: enrich only high‑impact tweets without depleting rate limits.

---

## 5. Data Model & Storage

**Storage**: **JSONL** primary, plus raw response dumps. Optional DuckDB deferred to later.

### Files
```
data/tweets.jsonl   # normalized tweets
data/users.jsonl    # deduped authors
data/state.json     # cursors + diagnostics
raw/page_*.json     # raw API pages
```

### Tweet Record (normalized)
```json
{
  "id": "...",
  "text": "...",
  "created_at": "...",
  "author_id": "...",
  "lang": "en",
  "public_metrics": {...},
  "entities": {...},
  "referenced_tweets": [...],
  "conversation_id": "...",
  "possibly_sensitive": false,
  "flags": {
    "contains_question": true,
    "is_reply_or_rt": true,
    "upper_ratio": 0.04,
    "has_media_hint": false
  },
  "temporal": {"hour_local": 22, "weekday": 4},
  "source_page": "raw/page_20250803_201500_07.json"
}
```

### User Record
```json
{"id":"...","username":"...","name":"...","verified":true,
 "created_at":"...","public_metrics":{...},
 "description":"..."}
```

---

## 6. Ingestion Design

- **Endpoint**: `GET /2/users/:id/liked_tweets` with `max_results=100`, `expansions=author_id`, and the specified `tweet.fields` / `user.fields`.  
- **Auth**: Bearer *user* token; auto‑refresh on 401.  
- **Pagination**: `meta.next_token`; initial backfill, then `since_id` delta sync.  
- **Rate limits**: parse headers, sleep until `x‑rate‑limit‑reset` + jitter on 429.  
- **Idempotency**: dedupe on `tweet.id`; small on‑disk set for recent IDs.  
- **Derived features**: `text_len`, counts, question mark, upper_ratio, reply flag, hour & weekday.

---

## 7. Enrichment Strategy (Threads & Media)

Selective only, under CLI flags, capped by `--enrich N`. Heuristics flag:
- Thread heads (`🧵`, `(1/… )`)  
- Motivational image posts (URL + short text + uppercase ratio)

For each candidate, fetch minimal context (root tweet / 1‑2 prev posts) and cache results.

---

## 8. Analysis Plan

1. **Frequencies**: authors, hashtags, n‑grams.  
2. **Embeddings & clustering**: sample N ≤ 2 000, cluster, label with n‑grams + LLM summary.  
3. **Temporal**: hour/day heatmap, spike days.  
4. **Contradictions**: rule‑based tension detection.  
5. **Insights Report**: 8–12 themes, top authors, value drivers, contradictions, action prompts.

---

## 9. CLI Spec — `scrape_likes.py`

```
usage: scrape_likes.py [--full] [--since] [--max-pages N]
                       [--out-dir PATH] [--sample N]
                       [--enrich N] [--enrich-mode threads|media|both]
                       [--enrich-since DAYS]
                       [--tz America/Los_Angeles]
```
Key flags explained inline.

---

## 10. Security & Privacy
- Mask tokens in logs.  
- No dataset uploads by default.  
- Optional handle/URL redaction when sending to external embeddings.

---

## 11. Metrics
- **TTFI** (Time to First Insight) ≤ 48 h.  
- **Explainability**: each cluster labeled with exemplars.  
- **Stability**: re‑runs on same data yield consistent top patterns.

---

## 12. Milestones
| Milestone | Scope | ETA |
|-----------|-------|-----|
| **M0** | Ingestion script, JSONL, state, rate‑limit handling | Day 0–1 |
| **M1** | Analysis notebook + `report.md` | Day 1–2 |
| **M2** | Selective enrichment | Day 2–3 |

---

## 13. Risks & Mitigations
| Risk | Mitigation |
|------|------------|
| **Rate‑limit churn** | Honor headers; expose `--max-pages`. |
| **Interpretation bias** | Show exemplars & n‑grams; let user veto labels. |
| **Scope creep** | Threads/media locked behind budget. |

---

## 14. Acceptance Criteria
- `scrape_likes.py --full --max-pages ...` runs without error; JSONL grows; `state.json` updates.  
- `analyze.ipynb` on sample data produces `report.md` with coherent insights.  
- Optional enrichment adds useful context without exceeding limits.
