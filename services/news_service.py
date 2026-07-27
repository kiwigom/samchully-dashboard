# -*- coding: utf-8 -*-
"""
4.3.1 1번(뉴스 정보) 데이터 연동 + 실시간 크롤링 (Supabase 저장)

1) 크롤링: 1.1~1.4 로직(구글 뉴스 RSS 검색)을 그대로 가져와, collect_*_news() /
   collect_all_news()로 제공한다. 결과는 로컬 CSV가 아니라 Supabase "news" 테이블에 저장된다
   (link 컬럼 기준 upsert라 같은 기사를 여러 번 수집해도 중복되지 않는다).
2) 읽기: Supabase "news" 테이블에서 읽어와 페이지에 뿌려줄 형태(과거 CSV와 동일한 한글
   컬럼명의 DataFrame)로 정리한다.
"""
import time
import urllib.parse
from datetime import datetime

import feedparser
import pandas as pd

from services import db

_CATEGORY_TARGET_COL = {
    "고객사": "고객사",
    "동종사": "동종사",
    "관심뉴스": "관심키워드",
    "에너지": "에너지키워드",
}


# ------------------------------------------------------------------
# 크롤링 공통 로직 (1.1.3~1.1.5, 1.2~1.4와 동일)
# ------------------------------------------------------------------
def search_google_news(query: str, lang: str = "ko", country: str = "KR") -> list:
    """구글 뉴스 RSS 검색 결과(entry 리스트)를 반환한다."""
    base_url = "https://news.google.com/rss/search"
    params = {"q": query, "hl": lang, "gl": country, "ceid": f"{country}:{lang}"}
    url = f"{base_url}?{urllib.parse.urlencode(params)}"
    feed = feedparser.parse(url)
    return feed.entries


def _parse_entries(entries: list, category: str, target: str, keyword: str) -> list:
    """RSS entry들을 Supabase news 테이블 스키마에 맞는 dict 리스트로 변환한다."""
    rows = []
    for e in entries:
        title = getattr(e, "title", "")
        link = getattr(e, "link", "")

        source = ""
        if hasattr(e, "source") and hasattr(e.source, "title"):
            source = e.source.title
        elif " - " in title:
            title, source = title.rsplit(" - ", 1)

        published_at = None
        if getattr(e, "published_parsed", None):
            published_at = datetime(*e.published_parsed[:6]).isoformat()

        rows.append(
            {
                "category": category,
                "target": target,
                "keyword": keyword,
                "title": title.strip(),
                "source": source.strip(),
                "published_at": published_at,
                "link": link,
            }
        )
    return rows


def _dedup_keep_latest(rows: list, top_n: int) -> list:
    """링크 기준 중복 제거 후 published_at 최신순 top_n개만 남긴다."""
    seen = {}
    for row in rows:
        seen[row["link"]] = row  # 같은 링크면 뒤 값으로 덮어씀(사실상 동일 내용)
    ordered = sorted(seen.values(), key=lambda r: r["published_at"] or "", reverse=True)
    return ordered[:top_n]


# ------------------------------------------------------------------
# 1.1~1.4 수집 (카테고리별)
# ------------------------------------------------------------------
def _collect_category(category: str, targets_keywords: dict, top_n: int, sleep_sec: float = 1.0) -> dict:
    """targets_keywords: {대상명: [검색어, ...]} -> Supabase에 upsert하고 대상별 건수를 반환."""
    result = {}
    for target, keywords in targets_keywords.items():
        all_rows = []
        for keyword in keywords:
            entries = search_google_news(keyword)
            all_rows.extend(_parse_entries(entries, category, target, keyword))
            time.sleep(sleep_sec)

        rows = _dedup_keep_latest(all_rows, top_n)
        if rows:
            db.upsert_rows("news", rows, on_conflict="link")
        result[target] = len(rows)
    return result


def collect_client_news(clients: list, keywords_map: dict, top_n: int = 10) -> dict:
    targets_keywords = {c: keywords_map.get(c, [c]) for c in clients}
    return _collect_category("고객사", targets_keywords, top_n)


def collect_peer_news(peers: list, keywords_map: dict, top_n: int = 10) -> dict:
    targets_keywords = {p: keywords_map.get(p, [p]) for p in peers}
    return _collect_category("동종사", targets_keywords, top_n)


def collect_interest_news(keywords: list, top_n: int = 10) -> dict:
    targets_keywords = {k: [k] for k in keywords}
    return _collect_category("관심뉴스", targets_keywords, top_n)


def collect_energy_news(keywords: list, top_n: int = 10) -> dict:
    targets_keywords = {k: [k] for k in keywords}
    return _collect_category("에너지", targets_keywords, top_n)


def collect_all_news(
    clients: list,
    client_keywords: dict,
    peers: list,
    peer_keywords: dict,
    interest_keywords: list,
    energy_keywords: list,
    top_n: int = 10,
    **_ignored,
) -> dict:
    """4개 뉴스 카테고리를 순서대로 전부 수집해서 Supabase에 저장하고, 건수 요약을 반환한다."""
    return {
        "고객사": collect_client_news(clients, client_keywords, top_n),
        "동종사": collect_peer_news(peers, peer_keywords, top_n),
        "관심뉴스": collect_interest_news(interest_keywords, top_n),
        "에너지": collect_energy_news(energy_keywords, top_n),
    }


# ------------------------------------------------------------------
# 4.3.1 Supabase에서 읽어서 페이지 표시용으로 정리
# ------------------------------------------------------------------
def _rows_to_df(rows: list, target_col: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.rename(
        columns={
            "target": target_col,
            "keyword": "검색어",
            "title": "제목",
            "source": "언론사",
            "published_at": "날짜",
            "link": "링크",
        }
    )
    keep_cols = [c for c in [target_col, "검색어", "제목", "언론사", "날짜", "링크"] if c in df.columns]
    return df[keep_cols]


def load_client_news(**_ignored) -> pd.DataFrame:
    return _rows_to_df(db.fetch_where("news", "category", "고객사", order_by="published_at", desc=True), "고객사")


def load_peer_news(**_ignored) -> pd.DataFrame:
    return _rows_to_df(db.fetch_where("news", "category", "동종사", order_by="published_at", desc=True), "동종사")


def load_interest_news(**_ignored) -> pd.DataFrame:
    return _rows_to_df(db.fetch_where("news", "category", "관심뉴스", order_by="published_at", desc=True), "관심키워드")


def load_energy_news(**_ignored) -> pd.DataFrame:
    return _rows_to_df(db.fetch_where("news", "category", "에너지", order_by="published_at", desc=True), "에너지키워드")


def _to_records(df: pd.DataFrame, group_col: str, top_n: int) -> dict:
    """그룹(고객사/동종사/키워드)별로 최신순 top_n건을 dict 형태로 정리한다."""
    if df.empty or group_col not in df.columns:
        return {}

    result = {}
    for name, group in df.groupby(group_col):
        if "날짜" in group.columns:
            group = group.sort_values("날짜", ascending=False)
        result[name] = group.head(top_n).to_dict("records")
    return result


def get_all_news(data_dir: str = None, top_n: int = 10) -> dict:
    """뉴스 페이지에서 바로 쓸 수 있는 {구분: {대상: [기사, ...]}} 딕셔너리를 반환한다.
    (data_dir는 과거 CSV 버전과의 호출부 호환을 위해 남겨둔 인자로, 지금은 쓰이지 않는다.)
    """
    return {
        "고객사": _to_records(load_client_news(), "고객사", top_n),
        "동종사": _to_records(load_peer_news(), "동종사", top_n),
        "관심뉴스": _to_records(load_interest_news(), "관심키워드", top_n),
        "에너지": _to_records(load_energy_news(), "에너지키워드", top_n),
    }


def get_latest_headlines(data_dir: str = None, n: int = 6) -> list:
    """대시보드 요약용: 모든 구분을 합쳐 최신순 n건을 반환한다."""
    rows = db.fetch_all("news", order_by="published_at", desc=True, limit=n)
    if not rows:
        return []

    df = pd.DataFrame(rows).rename(
        columns={"category": "구분", "title": "제목", "source": "언론사", "published_at": "날짜", "link": "링크"}
    )
    return df.head(n).to_dict("records")
