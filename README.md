# UIT Confession: crawl and sentiment classification

Collects the posts of the **UIT Confession** Facebook page (University of Information Technology,
VNU-HCM) and labels each confession **positive / negative / neutral** about the school and its
community, using a local LLM (Qwen2.5-7B-Instruct via Ollama) with PhoBERT as a second opinion.

> **Data is not included.** The posts contain real people's names, contact details and personal
> stories, and scraping Facebook may violate its terms of service. This repo is code only; run the
> pipeline yourself, on your own account, and mind the privacy of the people in the data.

## Pipeline

| Step | Script | What it does |
|---|---|---|
| 1. crawl the feed | `crawl.py` | Scrolls the page through a logged-in Chrome (Playwright over the debug port), expands "Xem thêm", saves `posts.jsonl`. Reaches back to about #2017 (what the feed serves). |
| 2. crawl older ids | `crawl_search.py` | Facebook search filtered to the page, one id at a time, for ids below the feed's reach. Skips known numbering gaps. |
| 3. find shared ids | `crawl_v2.py`, `crawl_search_v2.py`, `recrawl_truncated.py` | **Ids are not unique on this page** (different posts reuse a number), so these keep every post keyed by *id + start of text* into `posts_v2.jsonl`. |
| 4. merge | `merge_v2.py` | Folds v2 results into `posts.jsonl`: fuller text replaces truncated text, genuinely different posts become extra rows. Backs up first. `--dry-run` previews. |
| 5. classify | `classify.py` | Cleans the text, labels it, writes `results.csv` and `report.html`. |

`scripts/run_*.ps1` are silent supervisors that restart a crawl or the classifier if it crashes or stalls
(everything logs to `*_supervisor.log`). `missing.txt` lists the numbering gaps and ids not found.

### Text cleaning (`crawl.clean_text`)
Removes page furniture found by counting repeated lines over all posts: the `#UITconfessions: bit.ly/...`
hashtag/link, the `Ẩn bớt` button text, `-----` separators, lone `.` lines and a trailing `… Xem thêm`.
Raw text stays in `posts.jsonl`; cleaning happens at load time.

### Labeling rule
Overall feeling about UIT *and its community* (the school, lecturers, staff, rules, events, students):
positive = praise, gratitude, pride, kindness, friendly social posts, asking about someone you admire;
negative = complaints, anger, stress, unfairness, any problem or bad behavior in UIT life (including about
students); neutral = plain questions, advice requests, lecturer/course choice, lost-and-found, announcements,
and purely personal matters unrelated to UIT. The exact prompt is `SYSTEM` in `classify.py`.
Labels are cached per post by a hash of prompt + model + text, so changing any of them relabels only what changed.

## Setup (Windows)

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
python test_crawl.py            # sanity check of the parsing helpers

# Chrome with a separate profile and the debug port (log in to Facebook there once, finish 2FA):
chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\chrome_fb

.venv\Scripts\python crawl.py --cdp http://localhost:9222
```

Classification needs [Ollama](https://ollama.com) with the model imported (a GGUF plus a ChatML `TEMPLATE`
in the `Modelfile` in this repo) or, alternatively, an Anthropic API key for `--engine claude` (copy `.env.example` to `.env`).

```powershell
.venv\Scripts\python classify.py --sample 30     # try 30 posts first
.venv\Scripts\python classify.py                 # everything
```

Scripts default to the paths in the variables at the top of `scripts/*.ps1`; override with
`CHROME_EXE`, `CHROME_PROFILE`, `OLLAMA_EXE`, `OLLAMA_MODELS`.

## Notes and limits
- **No ground truth.** Labels are the model's judgment under the rule above and are noisiest on borderline
  posts; PhoBERT (`wonrax/phobert-base-vietnamese-sentiment`) scores overall tone, not attitude toward the
  school, so it is only a second opinion (about 60% agreement).
- A 7B model on a 6 GB GPU labels about 4 seconds per post.
- The page's numbering has gaps (for example #1485-#1984 never existed); see `missing.txt`.
- Facebook's DOM selectors will break eventually; the crawlers are written against the markup of 2026-10.
