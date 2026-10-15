"""UIT Confession: browse every confession and how it feels about the school.

Run:  streamlit run app.py      (or double-click start.bat)
Reads results.csv; if it is missing, shows a few invented sample posts instead.
"""
import html
import unicodedata
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).parent
RESULTS = ROOT / "results.csv"
DEMO = ROOT / "demo" / "sample_results.csv"      # invented posts, shown when the real results.csv is absent
LABELS = ["positive", "neutral", "negative"]
NAMES = {"positive": "Positive", "neutral": "Neutral", "negative": "Negative"}
COLOR = {"positive": "green", "neutral": "gray", "negative": "red"}

st.set_page_config(page_title="UIT Confession", page_icon="💬", layout="centered")
st.markdown("<style>.block-container{max-width:820px;padding-top:2.5rem}</style>", unsafe_allow_html=True)


def fold(s):
    """Lowercase without accents, so 'thang may' finds 'thang máy' (and đ matches d)."""
    s = unicodedata.normalize("NFD", str(s))
    return "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d").replace("Đ", "D").lower()


@st.cache_data(show_spinner=False)
def load(path, mtime):
    """The results table; `mtime` is part of the cache key, so the data reloads when the file changes."""
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"confession_id": str}, keep_default_na=False)
    df["id"] = pd.to_numeric(df["confession_id"], errors="coerce")
    df["shared"] = df.groupby("confession_id")["confession_id"].transform("size")
    df["_f"] = (df["text"] + " " + df["llm_reason"]).map(fold)
    return df


def card(r):
    with st.container(border=True):
        st.markdown(f"**#{r.confession_id}** &nbsp; :{COLOR.get(r.llm_label, 'gray')}-background[**{NAMES.get(r.llm_label, r.llm_label)}**]")
        st.markdown("<div style='word-break:break-word'>" + html.escape(r.text).replace("\n", "<br>") + "</div>", unsafe_allow_html=True)
        st.caption(f"Why: {r.llm_reason}")


def main():
    st.title("UIT Confession")
    st.write("Every confession from the page, and whether it speaks well, badly or neutrally of the school.")
    if not RESULTS.exists():
        st.info("These are **invented sample posts** so you can try the viewer. The real posts are not included, "
                "because they contain real people's personal details.")
    src = RESULTS if RESULTS.exists() else DEMO
    df = load(src, src.stat().st_mtime)
    n = len(df)
    counts = df["llm_label"].value_counts()

    cols = st.columns(4)
    cols[0].metric("Confessions", f"{n:,}")
    for c, label in zip(cols[1:], LABELS):
        c.metric(NAMES[label], f"{counts.get(label, 0):,}")
        c.caption(f"{100 * counts.get(label, 0) / n:.0f}% of all")

    query = st.text_input("Search", placeholder="Search the posts, or type a number like #123 (accents are optional)",
                          label_visibility="collapsed")
    c1, c2 = st.columns([3, 2])
    chosen = c1.multiselect("Show", LABELS, default=LABELS, format_func=NAMES.get)
    order = c2.selectbox("Order", ["Oldest first", "Newest first"])
    with st.expander("More filters"):
        only_dis = st.checkbox("Only posts where a second reader (PhoBERT) disagreed")
        only_shared = st.checkbox("Only numbers used by more than one post")

    view = df[df["llm_label"].isin(chosen)]
    if only_dis:
        view = view[~view["agree"]]
    if only_shared:
        view = view[view["shared"] > 1]
    q = query.strip()
    if q:
        view = view[view["confession_id"] == q.lstrip("#")] if q.startswith("#") else view[view["_f"].str.contains(fold(q), regex=False)]
    view = view.sort_values("id", ascending=order == "Oldest first", kind="stable")

    per = 20
    pages = max(1, -(-len(view) // per))
    sig = hash((q, tuple(chosen), only_dis, only_shared, order))      # a new search starts on page 1 again
    if st.session_state.get("sig") != sig:
        st.session_state.sig, st.session_state.page = sig, 1
    page = min(st.session_state.page, pages)

    def pager(where):
        a, b, c = where.columns([1, 2, 1])
        if a.button("← Previous", disabled=page <= 1, key=f"prev-{where is st}", use_container_width=True):
            st.session_state.page, st.session_state.to_top = page - 1, True
            st.rerun()
        b.markdown(f"<div style='text-align:center;padding-top:.4rem'>Page {page} of {pages} &nbsp;·&nbsp; {len(view):,} confessions</div>",
                   unsafe_allow_html=True)
        if c.button("Next →", disabled=page >= pages, key=f"next-{where is st}", use_container_width=True):
            st.session_state.page, st.session_state.to_top = page + 1, True
            st.rerun()

    if view.empty:
        st.info("Nothing matches. Try fewer words or another filter.")
        return
    if st.session_state.pop("to_top", False):      # came from Previous/Next: start the new page at the top
        components.html("<script>const m=window.parent.document.querySelector('[data-testid=stMain]');"
                        "if(m)m.scrollTo(0,0);</script>", height=0)
    pager(st)
    for r in view.iloc[(page - 1) * per: page * per].itertuples():
        card(r)
    top = st.container()
    pager(top)
    st.caption("CS232 - Introduction to Multimedia Computing")


main()
