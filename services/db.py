# -*- coding: utf-8 -*-
"""
Supabase 연결 모듈

로컬 CSV 대신 Supabase(Postgres) 테이블을 데이터 저장소로 사용하기 위한 공통 클라이언트.
필요 환경변수: SUPABASE_URL, SUPABASE_KEY (secret key 권장 - 쓰기 권한 필요)

구현 노트: 공식 `supabase` 파이썬 패키지(내부적으로 httpx의 HTTP/2 모드 사용) 대신
`requests`(HTTP/1.1)로 PostgREST API를 직접 호출한다. Vercel 같은 서버리스 환경에서
httpx의 HTTP/2 연결이 "RemoteProtocolError: StreamReset ... remote_reset:True"로
끊기는 알려진 호환성 문제가 있어, 이를 근본적으로 피하기 위함이다.

테이블 스키마(Supabase SQL Editor에서 미리 생성 필요):

    create table if not exists news (
        id bigint generated always as identity primary key,
        category text not null,        -- 고객사 / 동종사 / 관심뉴스 / 에너지
        target text not null,          -- 회사명 또는 키워드
        keyword text,
        title text not null,
        source text,
        published_at timestamptz,
        link text unique,
        collected_at timestamptz default now()
    );

    create table if not exists oil_price (
        date date not null,
        oil_type text not null,
        price double precision,
        primary key (date, oil_type)
    );

    create table if not exists fx_rate (
        date date primary key,
        rate double precision
    );

    create table if not exists ng_futures_price (
        date date primary key,
        price double precision,
        item text
    );

    create table if not exists city_gas_price (
        month date primary key,
        effective_date date,
        season text,
        industrial_price double precision
    );

    create table if not exists lng_positions (
        id bigint generated always as identity primary key,
        mmsi text not null,
        ship_name text,
        lat double precision,
        lon double precision,
        sog double precision,
        cog double precision,
        timestamp_utc timestamptz,
        collected_at timestamptz default now()
    );
"""
import os
import re

import requests


def _clean_env(value: str) -> str:
    """환경변수 값에서 모든 공백/개행 문자를 제거한다.

    Vercel 환경변수 입력 폼에 값을 붙여넣는 과정에서 문자 중간에 개행(\n)이
    섞여 들어가는 경우가 있었다(예: 'sb_secret_90Mv0\nKjHETx...'). 앞뒤 공백만
    제거하는 strip()으로는 이런 "중간에 낀" 개행을 잡지 못해 requests가
    InvalidHeader를 던졌다. URL/API 키에는 원래 공백 문자가 올 수 없으므로,
    문자열 어디에 있든 공백류 문자를 전부 제거해도 안전하다.
    """
    return re.sub(r"\s+", "", value or "")


def _base_url_and_headers(prefer: str = None):
    url = _clean_env(os.environ.get("SUPABASE_URL"))
    key = _clean_env(os.environ.get("SUPABASE_KEY"))
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_KEY 환경변수가 설정되어 있지 않습니다. "
            ".env 파일(로컬) 또는 Vercel/GitHub Actions 환경변수 설정을 확인하세요."
        )

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return url.rstrip("/") + "/rest/v1", headers


def upsert_rows(table: str, rows: list, on_conflict: str = None) -> int:
    """rows(list of dict)를 table에 upsert한다. 반환값: 처리한 행 수."""
    if not rows:
        return 0

    base_url, headers = _base_url_and_headers(prefer="resolution=merge-duplicates")
    params = {"on_conflict": on_conflict} if on_conflict else None

    resp = requests.post(f"{base_url}/{table}", headers=headers, params=params, json=rows, timeout=30)
    resp.raise_for_status()
    return len(rows)


def insert_rows(table: str, rows: list) -> int:
    """rows(list of dict)를 table에 그냥 추가(append)한다. (lng_positions처럼 시계열 누적용)"""
    if not rows:
        return 0

    base_url, headers = _base_url_and_headers()
    resp = requests.post(f"{base_url}/{table}", headers=headers, json=rows, timeout=30)
    resp.raise_for_status()
    return len(rows)


def fetch_all(table: str, order_by: str = None, desc: bool = False, limit: int = None) -> list:
    """table의 전체(또는 limit개) row를 list[dict]로 반환한다."""
    base_url, headers = _base_url_and_headers()

    params = {"select": "*"}
    if order_by:
        params["order"] = f"{order_by}.{'desc' if desc else 'asc'}"
    if limit:
        params["limit"] = limit

    resp = requests.get(f"{base_url}/{table}", headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json() or []


def fetch_where(table: str, column: str, value, order_by: str = None, desc: bool = False) -> list:
    base_url, headers = _base_url_and_headers()

    params = {"select": "*", column: f"eq.{value}"}
    if order_by:
        params["order"] = f"{order_by}.{'desc' if desc else 'asc'}"

    resp = requests.get(f"{base_url}/{table}", headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json() or []
