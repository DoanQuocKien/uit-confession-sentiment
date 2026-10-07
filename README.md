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
| 1. first pass | `crawl.py`, `crawl_search.py` | The original crawlers: scroll the feed (about #3691 down to #2017, which is as far as the feed serves) and search the page one id at a time below that. They assumed ids are unique, which they are not (see step 2), so they keep only the first post seen per id. |
| 2. complete pass | `crawl_v2.py` | **Ids are not unique on this page** (about 350 ids are shared by two to five different posts), so this scrolls the feed and keeps every post, keyed by *id + start of text*, into `posts_v2.jsonl`. Plain `python crawl_v2.py` covers the newest posts down to #2017. To go below that, use the page's own date filter: `python crawl_v2.py --prepare 2023-09` opens the page and picks year/month in *Bộ lọc bài viết*, you click *Xong* yourself in that Chrome tab, then `python crawl_v2.py --use-open-tab --end-id 3` scrolls that tab down to #1 (about 20 minutes for #615 to #1). |
| 3. fill gaps | `recrawl_truncated.py`, `crawl_search_v2.py` | Re-fetch posts that stayed truncated at "Xem thêm", and search individual ids (slower, about 17 s per id). `crawl_date.py` searches by date window but a single query word only returns about 60% of the posts, so it is kept for reference and not recommended. |
| 4. merge | `merge_v2.py` | Folds `posts_v2.jsonl` into `posts.jsonl`: fuller text replaces truncated text, genuinely different posts become extra rows. Backs up first. `--dry-run` previews. |
| 5. classify | `classify.py` | Cleans the text, labels it, writes `results.csv` and `report.html`. |

`scripts/run_*.ps1` are silent supervisors that restart a crawl or the classifier if it crashes or stalls
(everything logs to `*_supervisor.log`). `missing.txt` lists the numbering gaps and ids not found.

### Keeping it up to date
```powershell
.venv\Scripts\python update.py check    # look at the top of the feed: how many posts are new? (changes nothing)
.venv\Scripts\python update.py run      # fetch them, add them to posts.jsonl, label them, update results.csv
```
`update.py` scrolls the feed from the newest post until it meets 15 posts it already has, so it is quick. It starts
Chrome (debug port, logged-in profile) and Ollama itself if they are not running; if they are not at their default
locations set `CHROME_EXE` / `OLLAMA_EXE`. `run` also labels any post that is in `posts.jsonl` but not yet in
`results.csv`, so an interrupted run is finished by the next one, and it repairs posts stored cut off at "Xem thêm"
(looked up by id in the page's search, or in the feed for the newest posts) before labeling them.

### Viewer and one-click update (Streamlit)
Double-click `start.bat` (or run `.venv\Scripts\streamlit run app.py`). It opens http://localhost:8501 with two tabs:

- **Posts**: every post with the LLM label and reason, PhoBERT's label, filters by label, "PhoBERT disagrees" and
  "shared ids", accent-insensitive search (`thang may` finds `thang máy`), sorting and paging. It re-reads
  `results.csv` when the file changes.
- **Update**: *Check for new posts* and *Fetch and classify new posts* run `update.py` and show its output live.

The server listens on this machine only (the data contains personal information). Machine-specific paths
(`OLLAMA_EXE`, `OLLAMA_MODELS`, `CHROME_EXE`, `CHROME_PROFILE`) go in `local_env.bat`, which `start.bat` loads and git ignores.

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
