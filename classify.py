"""Classify posts.jsonl toward UIT with an LLM and PhoBERT -> results.csv + report.html.

Usage: python classify.py [--engine ollama|claude] [--sample N]
  --sample writes results_sample.csv / report_sample.html (separate from the full run).
ollama: needs `ollama serve` running with OLLAMA_MODEL pulled; labels are cached in
  llm_cache{_sample}.jsonl so a stopped run resumes.
claude: needs ANTHROPIC_API_KEY (env var or .env); the batch id is kept in batch_id{_sample}.txt.
"""
import argparse, csv, hashlib, json, os, random, re, time, urllib.request
from pathlib import Path

from crawl import clean_text, is_truncated  # crawl imports playwright only inside main()

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:7b-instruct"

MODEL = "claude-haiku-4-5"  # Haiku rejects "effort", so it is not sent
SYSTEM = (
    "You label anonymous Vietnamese confession posts written by students of UIT "
    "(University of Information Technology, VNU-HCM). Slang, abbreviations and sarcasm are common. "
    "Label the overall feeling the post expresses about UIT and its community: the university, its "
    "lecturers and staff, its rules and facilities, its events, and its students and student life. "
    "Problems and people at UIT count, not only the institution itself.\n"
    "- positive: praise, gratitude, pride, admiration, happiness or kindness connected to UIT; also "
    "friendly social posts such as looking to join a team or make friends, and asking for the "
    "contact or info of someone the author finds attractive, admires or wants to get to know at UIT "
    "(asking who a specific person is, or for their info, counts).\n"
    "- negative: complaints, criticism, anger, sadness, stress, unfairness, conflict, or any problem "
    "or bad behavior in UIT life, including complaints about students. A personal problem that "
    "mentions studying or life at UIT is negative.\n"
    "- neutral: plain questions, requests for information or advice, asking which lecturer or course "
    "to choose, lost-and-found, and announcements, with no feeling. A purely personal problem "
    "(family, love, apology) with no connection to UIT is neutral.\n"
    "When the post has a dominant feeling, use it. "
    "Write the reason in English only, at most 15 words."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": ["positive", "negative", "neutral"]},
        "reason": {"type": "string"},
    },
    "required": ["label", "reason"],
    "additionalProperties": False,
}
PHO = {"POS": "positive", "NEG": "negative", "NEU": "neutral"}


def load_env():
    """Read KEY=VALUE lines from .env (gitignored) without overriding real env vars."""
    f = Path(".env")
    for line in f.read_text(encoding="utf-8").splitlines() if f.exists() else []:
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def load_posts():
    """Posts with page furniture removed. Ids can repeat (different posts share a number):
    `occ` is the 0-based occurrence of the id, so each post gets its own cache entry."""
    rows = [json.loads(l) for l in Path("posts.jsonl").read_text(encoding="utf-8").splitlines() if l]
    seen, out = {}, []
    for r in rows:
        text = clean_text(r["text"])
        if not text:
            continue  # nothing left but page furniture
        occ = seen[r["confession_id"]] = seen.get(r["confession_id"], -1) + 1
        out.append({**r, "text": text, "occ": occ, "truncated": is_truncated(r["text"])})
    return out


def text_hash(text):
    return hashlib.sha1(re.sub(r"\s+", " ", text).strip().encode()).hexdigest()[:8]


def ollama_labels(posts, cache_file):
    """Label each post with a local model; append each result to cache_file as it completes."""
    # cache key includes a hash of prompt+model, so changing either recomputes instead of reusing
    tag = hashlib.sha1((SYSTEM + OLLAMA_MODEL).encode()).hexdigest()[:8]
    cache = {}
    if cache_file.exists():
        for l in cache_file.read_text(encoding="utf-8").splitlines():
            r = json.loads(l)
            if r["out"]["label"] != "error":  # errors are retried on the next run
                cache[r["id"]] = (r.get("h"), r["out"])
    # reason first, then label: a short rationale before the verdict helps small models
    schema = {**SCHEMA, "properties": {"reason": SCHEMA["properties"]["reason"], "label": SCHEMA["properties"]["label"]}}
    t0 = time.time()
    for i, p in enumerate(posts):
        key = cache_key(p, tag)
        h = text_hash(p["text"])
        if key in cache and cache[key][0] == h:  # same post text as when it was labeled
            continue
        body = json.dumps({"model": OLLAMA_MODEL, "stream": False, "format": schema,
                           # num_ctx 2048 + 1500-char posts: lets more layers fit in 6 GB VRAM
                           "options": {"temperature": 0, "num_ctx": 2048},
                           "messages": [{"role": "system", "content": SYSTEM},
                                        {"role": "user", "content": p["text"][:1500] + "\n\n(Write the reason in English.)"}]}).encode()
        try:
            req = urllib.request.Request(OLLAMA_URL, body, {"Content-Type": "application/json"})
            out = json.loads(json.loads(urllib.request.urlopen(req, timeout=300).read())["message"]["content"])
            assert out["label"] in ("positive", "negative", "neutral")
        except Exception as e:  # bad JSON / timeout: record as error, keep going
            out = {"label": "error", "reason": str(e)[:100]}
        cache[key] = (h, out)
        with cache_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"id": key, "h": h, "out": out}, ensure_ascii=False) + "\n")
        if (i + 1) % 25 == 0:
            print(f"{i + 1}/{len(posts)} labeled, {(time.time() - t0) / (i + 1):.1f}s/post", flush=True)
    try:  # free the GPU for PhoBERT: keep_alive 0 unloads the model right away
        urllib.request.urlopen(urllib.request.Request(
            OLLAMA_URL.replace("/chat", "/generate"),
            json.dumps({"model": OLLAMA_MODEL, "keep_alive": 0}).encode(),
            {"Content-Type": "application/json"}), timeout=60).read()
    except Exception:
        pass
    return {i: cache[cache_key(p, tag)][1] for i, p in enumerate(posts)}


def cache_key(p, tag):
    """One entry per post: a repeated id gets '#occurrence' (the first keeps the plain old key)."""
    return f'{p["confession_id"]}{"#%d" % p["occ"] if p["occ"] else ""}:{tag}'


def claude_labels(posts, batch_file):
    import anthropic  # only needed for --engine claude
    client = anthropic.Anthropic()
    if batch_file.exists():
        bid = batch_file.read_text().strip()
    else:
        batch = client.messages.batches.create(requests=[
            {"custom_id": str(i), "params": {
                "model": MODEL, "max_tokens": 2000, "system": SYSTEM,
                "messages": [{"role": "user", "content": p["text"][:6000]}],
                "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
            }} for i, p in enumerate(posts)])
        bid = batch.id
        batch_file.write_text(bid)
    while (b := client.messages.batches.retrieve(bid)).processing_status != "ended":
        print("batch", b.request_counts)
        time.sleep(60)
    out = {}
    for r in client.messages.batches.results(bid):
        if r.result.type == "succeeded":
            text = next((c.text for c in r.result.message.content if c.type == "text"), "")
            try:
                out[int(r.custom_id)] = json.loads(text)
                continue
            except json.JSONDecodeError:
                pass
        out[int(r.custom_id)] = {"label": "error", "reason": r.result.type}
    return out


def phobert_labels(posts):
    import torch
    from transformers import pipeline
    try:
        from pyvi.ViTokenizer import tokenize  # model expects word-segmented text
    except Exception:  # ponytail: no pyvi -> unsegmented text, lower accuracy
        tokenize = lambda s: s
    pipe = pipeline("text-classification", model="wonrax/phobert-base-vietnamese-sentiment",
                    device=0 if torch.cuda.is_available() else -1)
    texts = [tokenize(" ".join(p["text"].split())) for p in posts]
    res = pipe(texts, batch_size=32, truncation=True, max_length=256)
    return [PHO.get(r["label"], r["label"]) for r in res]


def counts(rows, key):
    c = {}
    for r in rows:
        c[r[key]] = c.get(r[key], 0) + 1
    return c


HTML = """<!doctype html><html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>UIT Confessions Sentiment</title>
<style>
:root{--bg:#fff;--fg:#1a1a1a;--mut:#666;--bd:#ddd;--positive:#2e7d32;--negative:#c62828;--neutral:#757575;--error:#b26a00}
@media(prefers-color-scheme:dark){:root{--bg:#161616;--fg:#eee;--mut:#999;--bd:#333;--positive:#66bb6a;--negative:#ef5350;--neutral:#9e9e9e;--error:#ffb74d}}
body{background:var(--bg);color:var(--fg);font:15px system-ui,sans-serif;margin:0;padding:16px;max-width:1100px;margin-inline:auto}
.bars{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}
.bar{display:flex;height:22px;border-radius:4px;overflow:hidden;margin:6px 0}.bar div{min-width:2px}
.legend span{margin-right:12px;color:var(--mut)}
.ctl{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}input,select{padding:6px;background:var(--bg);color:var(--fg);border:1px solid var(--bd);border-radius:4px}
table{width:100%;border-collapse:collapse}td,th{border-bottom:1px solid var(--bd);padding:6px;text-align:left;vertical-align:top}
.t{max-width:520px;white-space:pre-wrap;word-break:break-word}.r{color:var(--mut);font-size:13px}
.positive{color:var(--positive)}.negative{color:var(--negative)}.neutral{color:var(--neutral)}.error{color:var(--error)}
.tw{overflow-x:auto}
</style>
<h1>UIT Confessions: sentiment toward the school</h1>
<div class="bars" id="bars"></div>
<div class="ctl"><input id="q" placeholder="Search text or ID"><select id="f"><option value="">All LLM labels</option><option>positive</option><option>negative</option><option>neutral</option><option>error</option></select>
<label><input type="checkbox" id="d"> disagreements only</label><span id="n" class="r"></span></div>
<div class="tw"><table><thead><tr><th>ID<th>Text<th>LLM<th>PhoBERT</tr></thead><tbody id="tb"></tbody></table></div>
<script>
const R=__DATA__,C={positive:"var(--positive)",negative:"var(--negative)",neutral:"var(--neutral)",error:"var(--error)"};
const esc=s=>s.replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
for(const [k,t] of [["llm_label","LLM"],["phobert_label","PhoBERT"]]){
  const c={};R.forEach(r=>c[r[k]]=(c[r[k]]||0)+1);
  document.getElementById("bars").innerHTML+=`<div><b>${t}</b><div class="bar">${Object.entries(c).map(([l,v])=>`<div style="flex:${v};background:${C[l]||"#999"}" title="${l}: ${v}"></div>`).join("")}</div><div class="legend">${Object.entries(c).map(([l,v])=>`<span class="${l}">${l} ${v} (${(100*v/R.length).toFixed(0)}%)</span>`).join("")}</div></div>`}
function draw(){const q=document.getElementById("q").value.toLowerCase(),f=document.getElementById("f").value,d=document.getElementById("d").checked;
  const rows=R.filter(r=>(!f||r.llm_label==f)&&(!d||r.llm_label!=r.phobert_label)&&(!q||(r.text+r.confession_id).toLowerCase().includes(q)));
  document.getElementById("n").textContent=rows.length+" posts";
  document.getElementById("tb").innerHTML=rows.map(r=>`<tr><td>${r.post_url?`<a href="${esc(r.post_url)}" target="_blank" rel="noopener">#${esc(String(r.confession_id))}</a>`:"#"+esc(String(r.confession_id))}<td class="t">${esc(r.text.slice(0,600))}<div class="r">${esc(r.llm_reason)}</div><td class="${r.llm_label}">${r.llm_label}<td class="${r.phobert_label}">${r.phobert_label}`).join("")}
["q","f","d"].forEach(i=>document.getElementById(i).addEventListener("input",draw));draw();
</script></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["ollama", "claude"], default="ollama")
    ap.add_argument("--sample", type=int, default=0, help="classify a random sample of N posts")
    args = ap.parse_args()
    load_env()
    posts = load_posts()
    sfx = ""
    if args.sample:
        posts = random.Random(0).sample(posts, min(args.sample, len(posts)))  # fixed seed: repeatable
        sfx = "_sample"
    print(len(posts), "posts")
    if args.engine == "ollama":
        cl = ollama_labels(posts, Path(f"llm_cache{sfx}.jsonl"))
    else:
        cl = claude_labels(posts, Path(f"batch_id{sfx}.txt"))
    ph = phobert_labels(posts)
    rows = []
    for i, p in enumerate(posts):
        c = cl.get(i, {"label": "error", "reason": "missing"})
        rows.append({"confession_id": p["confession_id"], "post_url": p["post_url"], "text": p["text"],
                     "llm_label": c["label"], "llm_reason": c["reason"], "phobert_label": ph[i],
                     "agree": c["label"] == ph[i], "truncated": p["truncated"]})
    with open(f"results{sfx}.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    data = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    Path(f"report{sfx}.html").write_text(HTML.replace("__DATA__", data), encoding="utf-8")
    print("llm    ", counts(rows, "llm_label"))
    print("phobert", counts(rows, "phobert_label"))
    print(f"agreement {sum(r['agree'] for r in rows) / len(rows):.0%}")


if __name__ == "__main__":
    main()
