# -*- coding: utf-8 -*-
"""
Supabase 연결 모듈

로컬 CSV 대신 Supabase(Postgres) 테이블을 데이터 저장소로 사용하기 위한 공통 클라이언트.
필요 환경변수: SUPABASE_URL, SUPABASE_KEY (service role key 권장 - 쓰기 권한 필요)

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

_client = None


def get_client():
    """Supabase 클라이언트를 lazy하게 생성해서 재사용한다."""
    global _client
    if _client is not None:
        return _client

    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_KEY 환경변수가 설정되어 있지 않습니다. "
            ".env 파일(로컬) 또는 Vercel/GitHub Actions 환경변수 설정을 확인하세요."
        )

    _client = create_client(url, key)
    return _client


def upsert_rows(table: str, rows: list, on_conflict: str = None) -> int:
    """rows(list of dict)를 table에 upsert한다. 반환값: 처리한 행 수."""
    if not rows:
        return 0

    client = get_client()
    query = client.table(table).upsert(rows, on_conflict=on_conflict) if on_conflict else client.table(table).upsert(rows)
    query.execute()
    return len(rows)


def insert_rows(table: str, rows: list) -> int:
    """rows(list of dict)를 table에 그냥 추가(append)한다. (lng_positions처럼 시계열 누적용)"""
    if not rows:
        return 0
    client = get_client()
    client.table(table).insert(rows).execute()
    return len(rows)


def fetch_all(table: str, order_by: str = None, desc: bool = False, limit: int = None) -> list:
    """table의 전체(또는 limit개) row를 list[dict]로 반환한다."""
    client = get_client()
    query = client.table(table).select("*")
    if order_by:
        query = query.order(order_by, desc=desc)
    if limit:
        query = query.limit(limit)
    resp = query.execute()
    return resp.data or []


def fetch_where(table: str, column: str, value, order_by: str = None, desc: bool = False) -> list:
    client = get_client()
    query = client.table(table).select("*").eq(column, value)
    if order_by:
        query = query.order(order_by, desc=desc)
    resp = query.execute()
    return resp.data or []
