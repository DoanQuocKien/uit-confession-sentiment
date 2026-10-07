"""UIT Confession: browse the posts and their classifications, and fetch + classify new posts with one click.

Run:  streamlit run app.py      (or double-click start.bat)
Tab "Posts" reads results.csv; tab "Update" runs update.py (check / run) and shows its output live.
"""
import html, os, subprocess, sys, unicodedata
from pathlib import Path

import pandas as pd
import streamlit as st

from update import CDP, OLLAMA, truncated_count, up

ROOT = Path(__file__).parent
RESULTS = ROOT / "results.csv"
LABELS = ["positive", "neutral", "negative"]
COLOR = {"positive": "green", "neutral": "gray", "negative": "red", "error": "orange"}

st.set_page_config(page_title="UIT Confession", page_icon="💬", layout="wide")


def fold(s):
    """Lowercase without accents, so 'thang may' finds 'thang máy' (and đ matches d)."""
    s = unicodedata.normalize("NFD", str(s))
    return "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d").replace("Đ", "D").lower()


@st.cache_data(show_spinner=False)
def load(mtime):
    """results.csv as a table; `mtime` is the cache key, so the data reloads when the file changes."""
    df = pd.read_csv(RESULTS, encoding="utf-8-sig", dtype={"confession_id": str}, keep_default_na=False)
    df["id"] = pd.to_numeric(df["confession_id"], errors="coerce")
    df["shared"] = df.groupby("confession_id")["confession_id"].transform("size")
    df["_f"] = (df["text"] + " " + df["llm_reason"]).map(fold)
    return df


def card(r, query):
    with st.container(border=True):
        head = f"**#{r.confession_id}** &nbsp; :{COLOR.get(r.llm_label, 'gray')}-background[**{r.llm_label}**] &nbsp; "
        head += f":gray[PhoBERT: {r.phobert_label}{'' if r.agree else ' ≠'}]"
        if r.shared > 1:
            head += f" &nbsp; :violet[id shared by {r.shared}]"
        if r.truncated:
            head += " &nbsp; :orange[truncated]"
        st.markdown(head, unsafe_allow_html=True)
        text = r.text
        body = lambda t: "<div style='word-break:break-word'>" + html.escape(t).replace("\n", "<br>") + "</div>"
        if len(text) > 500:
            st.markdown(body(text[:500].rstrip()) + "…", unsafe_allow_html=True)
            with st.expander("Show the whole post"):
                st.markdown(body(text), unsafe_allow_html=True)
        else:
            st.markdown(body(text), unsafe_allow_html=True)
        st.caption(f"💬 {r.llm_reason}")


def posts_tab():
    if not RESULTS.exists():
        st.warning("results.csv not found. Run `python classify.py` first.")
        return
    df = load(RESULTS.stat().st_mtime)
    n = len(df)
    counts = df["llm_label"].value_counts()
    cols = st.columns(4)
    cols[0].metric("Posts", f"{n:,}", help=f"{df['confession_id'].nunique():,} distinct ids")
    for c, label in zip(cols[1:], LABELS):
        c.metric(label.capitalize(), f"{counts.get(label, 0):,}")
        c.caption(f"{100 * counts.get(label, 0) / n:.0f}% of posts")

    c1, c2, c3, c4 = st.columns([3, 2, 1.3, 1.3])
    query = c1.text_input("Search", placeholder="text or #id; accents optional", label_visibility="collapsed")
    chosen = c2.multiselect("Label", LABELS, default=LABELS, label_visibility="collapsed")
    only_dis = c3.checkbox("PhoBERT disagrees")
    only_shared = c4.checkbox("Shared ids")
    c5, c6 = st.columns([1, 1])
    newest_first = c5.selectbox("Order", ["ID ascending", "ID descending"], label_visibility="collapsed") == "ID descending"
    per = c6.selectbox("Per page", [10, 25, 50], index=1, label_visibility="collapsed")

    view = df[df["llm_label"].isin(chosen)]
    if only_dis:
        view = view[~view["agree"]]
    if only_shared:
        view = view[view["shared"] > 1]
    q = query.strip()
    if q:
        view = view[view["confession_id"] == q.lstrip("#")] if q.startswith("#") else view[view["_f"].str.contains(fold(q), regex=False)]
    view = view.sort_values("id", ascending=not newest_first, kind="stable")

    pages = max(1, -(-len(view) // per))
    sig = hash((q, tuple(chosen), only_dis, only_shared, newest_first, per))      # a new filter starts on page 1 again
    page = st.number_input(f"Page (of {pages})", 1, pages, 1, key=f"page-{sig}") if pages > 1 else 1
    st.caption(f"{len(view):,} post(s)" + (f", showing {(page - 1) * per + 1}-{min(page * per, len(view))}" if len(view) else ""))
    if view.empty:
        st.info("No posts match.")
    for r in view.iloc[(page - 1) * per: page * per].itertuples():
        card(r, q)


def run_update(command):
    """Run `update.py <command>` and show its output as it arrives; returns the exit code."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen([sys.executable, "-u", str(ROOT / "update.py"), command], cwd=ROOT, env=env, text=True,
                            encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    box, lines = st.empty(), []
    noisy = ("Warning", "warn", "symlink", "HF_TOKEN", "Loading weights", "developer mode", "[GIN]")
    for line in proc.stdout:
        if line.strip() and not any(s in line for s in noisy):
            lines.append(line.rstrip())
            box.code("\n".join(lines[-25:]), language=None)
    return proc.wait()


def update_tab():
    df = load(RESULTS.stat().st_mtime) if RESULTS.exists() else None
    a, b, c = st.columns(3)
    a.metric("Posts labeled", f"{len(df):,}" if df is not None else "0")
    b.metric("Chrome (Facebook login)", "running" if up(CDP + "/json/version") else "starts when needed")
    c.metric("Ollama (the model)", "running" if up(OLLAMA + "/api/version") else "starts when needed")
    cut = truncated_count() if Path("posts.jsonl").exists() else 0
    if cut:
        st.warning(f"{cut} stored post(s) are cut off at \"Xem thêm\"; **Fetch and classify** repairs them.")
    st.write("**Check** looks at the newest posts on the page and says how many are new. **Fetch and classify** adds them, "
             "labels them and updates the results (a few minutes; Chrome and Ollama start themselves if needed).")
    x, y = st.columns(2)
    check = x.button("🔎 Check for new posts", use_container_width=True)
    fetch = y.button("⬇️ Fetch and classify new posts", type="primary", use_container_width=True)
    if check or fetch:
        with st.spinner("Working... (this tab is busy until it finishes)"):
            code = run_update("check" if check else "run")
        if code == 0:
            st.success("Done." + (" Open the Posts tab to see the new posts." if fetch else ""))
        else:
            st.error(f"update.py stopped with exit code {code}; see the output above.")


st.title("UIT Confession: posts and classifications")
tab_posts, tab_update = st.tabs(["Posts", "Update"])
with tab_posts:
    posts_tab()
with tab_update:
    update_tab()
