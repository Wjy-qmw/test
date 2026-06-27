"""GitHub Treasure Hunter - a Streamlit dashboard for discovering valuable repos."""

from __future__ import annotations

import html
import math
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import streamlit as st

APP_TITLE = "GitHub Treasure Hunter"
DB_PATH = Path("github_treasure.db")
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"


st.set_page_config(
    page_title=APP_TITLE,
    page_icon="💎",
    layout="wide",
    initial_sidebar_state="expanded",
)


CSS = """
<style>
:root {
  --card-bg: rgba(23, 31, 51, 0.78);
  --card-border: rgba(125, 211, 252, 0.22);
  --text-muted: #a8b3cf;
  --accent: #7dd3fc;
  --accent-2: #c084fc;
  --gold: #fbbf24;
}
.stApp {
  background: radial-gradient(circle at top left, rgba(14, 165, 233, .18), transparent 34%),
              radial-gradient(circle at top right, rgba(168, 85, 247, .18), transparent 28%),
              linear-gradient(135deg, #07111f 0%, #0f172a 48%, #111827 100%);
  color: #e5eefc;
}
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, rgba(15, 23, 42, .98), rgba(17, 24, 39, .96));
  border-right: 1px solid rgba(125, 211, 252, .15);
}
.hero {
  padding: 2rem;
  border: 1px solid rgba(125, 211, 252, .25);
  border-radius: 28px;
  background: linear-gradient(135deg, rgba(14, 165, 233, .22), rgba(168, 85, 247, .16));
  box-shadow: 0 24px 80px rgba(2, 6, 23, .38);
  margin-bottom: 1.25rem;
}
.hero h1 {font-size: 3rem; margin: 0 0 .35rem 0; letter-spacing: -.04em;}
.hero p {font-size: 1.08rem; color: var(--text-muted); margin: 0;}
.repo-card {
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 22px;
  padding: 1.2rem;
  margin: .85rem 0;
  box-shadow: 0 14px 35px rgba(2, 6, 23, .3);
}
.repo-card h3 {margin: 0 0 .35rem 0; font-size: 1.35rem;}
.repo-card a {color: var(--accent); text-decoration: none;}
.repo-card p {color: #cbd5e1; margin: .55rem 0 .9rem 0;}
.metric-row {display: flex; flex-wrap: wrap; gap: .55rem; margin-top: .65rem;}
.pill {
  display: inline-flex;
  align-items: center;
  gap: .25rem;
  padding: .35rem .65rem;
  border-radius: 999px;
  background: rgba(148, 163, 184, .13);
  border: 1px solid rgba(148, 163, 184, .18);
  color: #e2e8f0;
  font-size: .88rem;
}
.score {background: linear-gradient(135deg, rgba(251,191,36,.22), rgba(192,132,252,.18)); border-color: rgba(251,191,36,.3);}
.footer-note {color: var(--text-muted); font-size: .9rem; text-align: center; padding: 2rem 0 1rem;}
.stButton>button {border-radius: 999px; font-weight: 700; border: 0; background: linear-gradient(90deg, #38bdf8, #a78bfa); color: #020617;}
</style>
"""


LANGUAGES = [
    "Any",
    "Python",
    "JavaScript",
    "TypeScript",
    "Java",
    "Go",
    "Rust",
    "C++",
    "C#",
    "PHP",
    "Ruby",
    "Swift",
    "Kotlin",
]

SORT_OPTIONS = {
    "Treasure score": "treasure_score",
    "Stars": "stargazers_count",
    "Forks": "forks_count",
}


def init_db() -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                language TEXT NOT NULL,
                min_stars INTEGER NOT NULL,
                sort_by TEXT NOT NULL,
                result_count INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def record_search(keyword: str, language: str, min_stars: int, sort_by: str, result_count: int) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            """
            INSERT INTO searches (keyword, language, min_stars, sort_by, result_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (keyword, language, min_stars, sort_by, result_count, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def load_search_history() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    with closing(sqlite3.connect(DB_PATH)) as conn:
        return pd.read_sql_query(
            "SELECT keyword, language, min_stars, sort_by, result_count, created_at FROM searches ORDER BY id DESC LIMIT 25",
            conn,
        )


def treasure_score(repo: dict[str, Any]) -> float:
    stars = repo.get("stargazers_count", 0) or 0
    forks = repo.get("forks_count", 0) or 0
    watchers = repo.get("watchers_count", 0) or 0
    issues = repo.get("open_issues_count", 0) or 0
    pushed_at = repo.get("pushed_at")
    recency_bonus = 0.0
    if pushed_at:
        try:
            pushed = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
            age_days = max((datetime.now(timezone.utc) - pushed).days, 0)
            recency_bonus = max(0, 120 - age_days) / 12
        except ValueError:
            recency_bonus = 0.0
    return round(math.log1p(stars) * 12 + math.log1p(forks) * 7 + math.log1p(watchers) * 3 + recency_bonus - math.log1p(issues), 2)


@st.cache_data(ttl=600, show_spinner=False)
def search_github(keyword: str, language: str, min_stars: int, per_page: int) -> list[dict[str, Any]]:
    query = f"{keyword.strip()} stars:>={min_stars}"
    if language != "Any":
        query += f" language:{language}"
    response = requests.get(
        GITHUB_SEARCH_URL,
        params={"q": query, "sort": "stars", "order": "desc", "per_page": per_page},
        headers=github_headers(),
        timeout=20,
    )
    response.raise_for_status()
    repos = response.json().get("items", [])
    for repo in repos:
        repo["treasure_score"] = treasure_score(repo)
    return repos


def github_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "github-treasure-hunter-streamlit"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def format_number(value: int | float | None) -> str:
    value = value or 0
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(int(value))


def repo_card(repo: dict[str, Any]) -> None:
    description = html.escape(repo.get("description") or "No description provided. The treasure may be hidden in the code.")
    language = html.escape(repo.get("language") or "Unknown")
    full_name = html.escape(repo.get("full_name") or "Unknown repository")
    repo_url = html.escape(repo.get("html_url") or "#", quote=True)
    updated = html.escape((repo.get("pushed_at") or "").replace("T", " ").replace("Z", " UTC"))
    st.markdown(
        f"""
        <div class="repo-card">
          <h3><a href="{repo_url}" target="_blank" rel="noopener noreferrer">💎 {full_name}</a></h3>
          <p>{description}</p>
          <div class="metric-row">
            <span class="pill score">🏆 Treasure score: {repo.get('treasure_score')}</span>
            <span class="pill">⭐ {format_number(repo.get('stargazers_count'))} stars</span>
            <span class="pill">🍴 {format_number(repo.get('forks_count'))} forks</span>
            <span class="pill">👀 {format_number(repo.get('watchers_count'))} watchers</span>
            <span class="pill">🧭 {language}</span>
            <span class="pill">🕒 {updated}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_charts(df: pd.DataFrame) -> None:
    st.subheader("📊 Treasure map analytics")
    col1, col2 = st.columns(2)
    with col1:
        language_counts = df["language"].fillna("Unknown").value_counts().rename_axis("language").reset_index(name="repositories")
        st.bar_chart(language_counts, x="language", y="repositories", color="#38bdf8")
    with col2:
        chart_df = df[["full_name", "stargazers_count", "forks_count", "treasure_score"]].set_index("full_name")
        st.scatter_chart(chart_df, x="stargazers_count", y="forks_count", size="treasure_score")


def main() -> None:
    init_db()
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(
        """
        <section class="hero">
          <h1>💎 GitHub Treasure Hunter</h1>
          <p>Discover high-signal open-source repositories with a sleek, dark-mode Streamlit dashboard.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("🧰 Hunt filters")
        keyword = st.text_input("Repository keyword", value="streamlit", placeholder="machine learning, cli, dashboard...")
        language = st.selectbox("Language", LANGUAGES)
        min_stars = st.slider("Minimum stars", min_value=0, max_value=50_000, value=500, step=100)
        sort_label = st.selectbox("Sort by", list(SORT_OPTIONS.keys()))
        per_page = st.slider("Results to scan", min_value=5, max_value=50, value=20, step=5)
        hunt = st.button("Start treasure hunt", use_container_width=True)
        st.divider()
        st.caption("Recent hunts are stored locally in SQLite.")

    if "repos" not in st.session_state:
        st.session_state.repos = []

    if hunt:
        if not keyword.strip():
            st.warning("Enter a keyword to start hunting.")
        else:
            progress = st.progress(0, text="Charting the GitHub seas...")
            try:
                with st.spinner("Loading repositories and calculating treasure scores..."):
                    progress.progress(30, text="Calling GitHub Search API...")
                    repos = search_github(keyword, language, min_stars, per_page)
                    progress.progress(70, text="Ranking hidden gems...")
                    sort_key = SORT_OPTIONS[sort_label]
                    repos = sorted(repos, key=lambda item: item.get(sort_key, 0) or 0, reverse=True)
                    st.session_state.repos = repos
                    record_search(keyword.strip(), language, min_stars, sort_label, len(repos))
                    progress.progress(100, text="Treasure found!")
                st.success(f"Found {len(st.session_state.repos)} repositories.")
            except requests.HTTPError as exc:
                st.error(f"GitHub API error: {exc.response.status_code} - {exc.response.text[:180]}")
            except requests.RequestException as exc:
                st.error(f"Network error while searching GitHub: {exc}")

    repos = st.session_state.repos
    if repos:
        df = pd.DataFrame(repos)
        top_score = df["treasure_score"].max()
        total_stars = int(df["stargazers_count"].sum())
        total_forks = int(df["forks_count"].sum())
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Repositories", len(df))
        col2.metric("Total stars", format_number(total_stars))
        col3.metric("Total forks", format_number(total_forks))
        col4.metric("Top score", f"{top_score:.2f}")
        render_charts(df)
        st.subheader("🗃️ Repository cards")
        for repo in repos:
            repo_card(repo)
    else:
        st.info("Use the sidebar to search GitHub repositories and reveal treasure cards.")

    history = load_search_history()
    if not history.empty:
        with st.expander("🕰️ Recent SQLite search history"):
            st.dataframe(history, use_container_width=True, hide_index=True)

    st.markdown('<p class="footer-note">Built with Python, Streamlit, SQLite, requests, and pandas.</p>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
