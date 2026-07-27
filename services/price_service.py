# -*- coding: utf-8 -*-
"""
4.3.2 2번(가격 유추/입력기) 데이터 연동 + 실시간 수집 (Supabase 저장)

1) 수집: 2.1(유가: EIA+오피넷, 실패 시 yfinance) / 2.2(환율: ECOS, 실패 시 Frankfurter) /
   2.3(천연가스 선물: yfinance) / 2.4(도시가스 산업용요금: KOGAS 요금표 파싱) 로직을
   그대로 가져와 collect_*() 함수로 제공한다. 결과는 로컬 CSV가 아니라 Supabase 테이블에 저장된다.
2) 분석: 2.5(상관관계·회귀예측)와 2.6(입력기) 로직 - analyze() / estimate_price().
3) 읽기: Supabase 테이블에서 불러오는 load_all_price_data().
"""
import base64
import csv
import glob
import io
import os
import re
from datetime import datetime
from io import BytesIO

import matplotlib

matplotlib.use("Agg")  # 서버(웹)에는 화면이 없으므로 파일/버퍼 출력 전용 백엔드 사용
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import xml.etree.ElementTree as ET
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score

from services import db

for _font in ("Malgun Gothic", "AppleGothic", "NanumGothic"):
    if _font in [f.name for f in matplotlib.font_manager.fontManager.ttflist]:
        plt.rcParams["font.family"] = _font
        break
plt.rcParams["axes.unicode_minus"] = False


# ====================================================================
# 2.1 유가 수집 (EIA + 오피넷, 실패 시 yfinance)
# ====================================================================
def fetch_eia_spot_price(series_id: str, api_key: str, start: str = None, end: str = None) -> pd.DataFrame:
    url = "https://api.eia.gov/v2/petroleum/pri/spt/data/"
    params = {
        "api_key": api_key,
        "frequency": "daily",
        "data[0]": "value",
        "facets[series][]": series_id,
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "length": 5000,
    }
    if start:
        params["start"] = start
    if end:
        params["end"] = end

    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()["response"]["data"]

    df = pd.DataFrame(data)[["period", "value"]]
    df.columns = ["날짜", "가격"]
    df["날짜"] = pd.to_datetime(df["날짜"])
    df["가격"] = df["가격"].astype(float)
    return df


def fetch_wti_brent_eia(api_key: str) -> pd.DataFrame:
    wti = fetch_eia_spot_price("RWTC", api_key)
    wti["유종"] = "WTI"
    brent = fetch_eia_spot_price("RBRTE", api_key)
    brent["유종"] = "Brent"
    return pd.concat([wti, brent], ignore_index=True)


def fetch_dubai_opinet(api_key: str) -> pd.DataFrame:
    url = "http://www.opinet.co.kr/api/internationalPriceInfo.do"
    params = {"code": api_key, "out": "xml"}

    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    rows = []
    for oil in root.iter("OIL"):
        row = {child.tag.upper(): (child.text or "").strip() for child in oil}
        rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["날짜", "가격", "유종"])

    df = df.rename(columns={"DATE": "날짜", "DUBAI": "가격"})[["날짜", "가격"]]
    df["날짜"] = pd.to_datetime(df["날짜"], format="%Y%m%d", errors="coerce")
    df["가격"] = pd.to_numeric(df["가격"], errors="coerce")
    df["유종"] = "Dubai"
    return df.dropna(subset=["날짜"])


def fetch_oil_futures_yfinance(tickers: tuple = ("CL=F", "BZ=F"), period: str = "3mo") -> pd.DataFrame:
    """키가 없을 때 쓰는 대안: yfinance로 WTI/Brent 선물가격 조회."""
    import yfinance as yf

    name_map = {"CL=F": "WTI(선물)", "BZ=F": "Brent(선물)"}
    frames = []
    for ticker in tickers:
        hist = yf.Ticker(ticker).history(period=period)[["Close"]].reset_index()
        hist.columns = ["날짜", "가격"]
        hist["날짜"] = pd.to_datetime(hist["날짜"], utc=True).dt.tz_localize(None)
        hist["유종"] = name_map.get(ticker, ticker)
        frames.append(hist)
    return pd.concat(frames, ignore_index=True)


def collect_oil_price(eia_api_key: str, opinet_api_key: str) -> int:
    try:
        df = fetch_wti_brent_eia(eia_api_key)
        dubai = fetch_dubai_opinet(opinet_api_key)
        oil_df = pd.concat([df, dubai], ignore_index=True).sort_values(["유종", "날짜"])
    except Exception:
        oil_df = fetch_oil_futures_yfinance()

    rows = [
        {"date": d.strftime("%Y-%m-%d"), "oil_type": t, "price": float(p)}
        for d, t, p in zip(oil_df["날짜"], oil_df["유종"], oil_df["가격"])
        if pd.notna(p)
    ]
    return db.upsert_rows("oil_price", rows, on_conflict="date,oil_type")


# ====================================================================
# 2.2 환율 수집 (ECOS, 실패 시 Frankfurter)
# ====================================================================
def fetch_fx_ecos(api_key: str, start_date: str, end_date: str) -> pd.DataFrame:
    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/1000/"
        f"731Y001/D/{start_date}/{end_date}/0000001"
    )
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    rows = data.get("StatisticSearch", {}).get("row", [])
    df = pd.DataFrame(rows)[["TIME", "DATA_VALUE"]]
    df.columns = ["날짜", "환율"]
    df["날짜"] = pd.to_datetime(df["날짜"], format="%Y%m%d")
    df["환율"] = df["환율"].astype(float)
    return df.sort_values("날짜").reset_index(drop=True)


def fetch_fx_frankfurter(start_date: str, end_date: str, base: str = "USD", target: str = "KRW") -> pd.DataFrame:
    url = f"https://api.frankfurter.app/{start_date}..{end_date}"
    params = {"from": base, "to": target}

    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    rates = resp.json().get("rates", {})

    rows = [{"날짜": date, "환율": value[target]} for date, value in rates.items()]
    df = pd.DataFrame(rows).sort_values("날짜").reset_index(drop=True)
    df["날짜"] = pd.to_datetime(df["날짜"])
    return df


def collect_fx_rate(ecos_api_key: str, start_date: str = "2023-01-01") -> int:
    end_date = datetime.now().strftime("%Y-%m-%d")

    fx_df = None
    if ecos_api_key and ecos_api_key != "YOUR_ECOS_API_KEY":
        try:
            fx_df = fetch_fx_ecos(ecos_api_key, start_date.replace("-", ""), end_date.replace("-", ""))
        except Exception:
            fx_df = None

    if fx_df is None:
        fx_df = fetch_fx_frankfurter(start_date, end_date)

    rows = [
        {"date": d.strftime("%Y-%m-%d"), "rate": float(r)}
        for d, r in zip(fx_df["날짜"], fx_df["환율"])
        if pd.notna(r)
    ]
    return db.upsert_rows("fx_rate", rows, on_conflict="date")


# ====================================================================
# 2.3 천연가스 선물가격 수집 (yfinance)
# ====================================================================
def fetch_ng_futures_yfinance(ticker: str = "NG=F", period: str = "6mo") -> pd.DataFrame:
    import yfinance as yf

    hist = yf.Ticker(ticker).history(period=period)[["Close"]].reset_index()
    hist.columns = ["날짜", "가격"]
    hist["날짜"] = pd.to_datetime(hist["날짜"], utc=True).dt.tz_localize(None)
    hist["품목"] = "Henry Hub 천연가스(선물)"
    return hist


def collect_ng_price() -> int:
    ng_df = fetch_ng_futures_yfinance()
    rows = [
        {"date": d.strftime("%Y-%m-%d"), "price": float(p), "item": item}
        for d, p, item in zip(ng_df["날짜"], ng_df["가격"], ng_df["품목"])
        if pd.notna(p)
    ]
    return db.upsert_rows("ng_futures_price", rows, on_conflict="date")


# ====================================================================
# 2.4 도시가스 산업용요금 수집 (KOGAS 요금표 파싱)
# ====================================================================
SEASON_MONTH_MAP = {
    12: "동절기", 1: "동절기", 2: "동절기", 3: "동절기",
    6: "하절기", 7: "하절기", 8: "하절기", 9: "하절기",
    4: "기타월", 5: "기타월", 10: "기타월", 11: "기타월",
}


def _load_rows(file_path: str) -> list:
    raw = open(file_path, "rb").read()
    try:
        text = raw.decode("cp949")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="ignore")
    return list(csv.reader(io.StringIO(text)))


def _extract_effective_date(filename: str):
    m = re.search(r"(\d{6})", filename)
    if not m:
        return None
    yy, mm, dd = m.group(1)[:2], m.group(1)[2:4], m.group(1)[4:6]
    try:
        return pd.Timestamp(year=2000 + int(yy), month=int(mm), day=int(dd))
    except ValueError:
        return None


def _find_header_idx(rows: list):
    for i, row in enumerate(rows):
        if row and row[0].replace(" ", "") == "구분":
            return i
    return None


def _extract_industrial_rates(rows: list, header_idx: int) -> dict:
    industrial_row_idx = None
    industrial_col_idx = None
    for r_idx in range(header_idx + 1, len(rows)):
        row = rows[r_idx]
        for c_idx, cell in enumerate(row):
            if cell.strip() == "산업용":
                industrial_row_idx, industrial_col_idx = r_idx, c_idx
                break
        if industrial_row_idx is not None:
            break
    if industrial_row_idx is None:
        return {}

    season_col = industrial_col_idx + 1
    results = {}
    r_idx = industrial_row_idx
    first = True

    while r_idx < len(rows):
        row = rows[r_idx]
        if not first and (len(row) <= industrial_col_idx or row[industrial_col_idx].strip() != ""):
            break
        if len(row) <= season_col:
            break

        season = row[season_col].strip()
        price_cells = [c.strip() for c in row[season_col + 1:] if c.strip() != ""]
        if not price_cells:
            break
        try:
            price = float(price_cells[-1])
        except ValueError:
            break

        if season == "":
            if first:
                return {"동절기": price, "하절기": price, "기타월": price}
            break
        else:
            results[season] = price

        first = False
        r_idx += 1
        if len(results) >= 3:
            break

    return results


def _parse_rate_file(file_path: str) -> dict:
    rows = _load_rows(file_path)
    header_idx = _find_header_idx(rows)
    if header_idx is None:
        return {}
    return _extract_industrial_rates(rows, header_idx)


def load_rate_tables(kogas_raw_dir: str) -> pd.DataFrame:
    files = sorted(glob.glob(f"{kogas_raw_dir}/*.csv"))
    records = []
    for f in files:
        eff_date = _extract_effective_date(os.path.basename(f))
        if eff_date is None:
            continue
        rates = _parse_rate_file(f)
        if not rates:
            continue
        records.append({"시행일자": eff_date, "파일명": os.path.basename(f), **rates})

    df = pd.DataFrame(records)
    return df.sort_values("시행일자").reset_index(drop=True)


def build_monthly_industrial_price(rate_table: pd.DataFrame, start=None, end=None) -> pd.DataFrame:
    rate_table = rate_table.sort_values("시행일자").reset_index(drop=True)
    start = start or rate_table["시행일자"].min()
    end = end or pd.Timestamp.today().normalize()

    rows = []
    for month in pd.date_range(start, end, freq="MS"):
        applicable = rate_table[rate_table["시행일자"] <= month]
        if applicable.empty:
            continue
        current = applicable.iloc[-1]
        season = SEASON_MONTH_MAP[month.month]
        rows.append(
            {
                "날짜": month,
                "적용시행일자": current["시행일자"],
                "계절": season,
                "산업용요금": current.get(season),
            }
        )
    return pd.DataFrame(rows)


def collect_city_gas_price(kogas_raw_dir: str) -> int:
    rate_table = load_rate_tables(kogas_raw_dir)
    if rate_table.empty:
        raise FileNotFoundError(
            f"'{kogas_raw_dir}' 폴더에서 KOGAS 용도별 도매요금표 CSV를 찾지 못했습니다."
        )
    monthly_df = build_monthly_industrial_price(rate_table)

    rows = [
        {
            "month": row["날짜"].strftime("%Y-%m-%d"),
            "effective_date": row["적용시행일자"].strftime("%Y-%m-%d"),
            "season": row["계절"],
            "industrial_price": float(row["산업용요금"]) if pd.notna(row["산업용요금"]) else None,
        }
        for _, row in monthly_df.iterrows()
    ]
    return db.upsert_rows("city_gas_price", rows, on_conflict="month")


# ====================================================================
# 2.1~2.4 전체 수집 오케스트레이션
# ====================================================================
def collect_all_price_data(
    kogas_raw_dir: str,
    eia_api_key: str,
    opinet_api_key: str,
    ecos_api_key: str,
    **_ignored,
) -> dict:
    """유가/환율/천연가스/도시가스요금을 순서대로 전부 수집한다. 일부가 실패해도 나머지는 계속 진행."""
    status = {}

    try:
        n = collect_oil_price(eia_api_key, opinet_api_key)
        status["유가"] = f"{n}건 수집"
    except Exception as e:
        status["유가"] = f"실패: {e}"

    try:
        n = collect_fx_rate(ecos_api_key)
        status["환율"] = f"{n}건 수집"
    except Exception as e:
        status["환율"] = f"실패: {e}"

    try:
        n = collect_ng_price()
        status["천연가스선물"] = f"{n}건 수집"
    except Exception as e:
        status["천연가스선물"] = f"실패: {e}"

    try:
        n = collect_city_gas_price(kogas_raw_dir)
        status["도시가스요금"] = f"{n}개월 수집"
    except Exception as e:
        status["도시가스요금"] = f"실패: {e}"

    return status


# ====================================================================
# 4.3.2 Supabase에서 읽기 + 2.5 상관관계/예측 + 2.6 입력기
# ====================================================================
def load_all_price_data(**_ignored) -> dict:
    oil_rows = db.fetch_all("oil_price")
    fx_rows = db.fetch_all("fx_rate")
    ng_rows = db.fetch_all("ng_futures_price")
    city_gas_rows = db.fetch_all("city_gas_price")

    oil_df = pd.DataFrame(oil_rows).rename(columns={"date": "날짜", "oil_type": "유종", "price": "가격"})
    fx_df = pd.DataFrame(fx_rows).rename(columns={"date": "날짜", "rate": "환율"})
    ng_df = pd.DataFrame(ng_rows).rename(columns={"date": "날짜", "price": "가격", "item": "품목"})
    city_gas_df = pd.DataFrame(city_gas_rows).rename(
        columns={"month": "날짜", "effective_date": "적용시행일자", "season": "계절", "industrial_price": "산업용요금"}
    )

    return {"oil": oil_df, "fx": fx_df, "ng": ng_df, "city_gas": city_gas_df}


def _normalize_date(series: pd.Series) -> pd.Series:
    """소스마다 시간대(tz) 포함 여부가 달라도 항상 '시간대 없는' naive datetime으로 통일한다."""
    return pd.to_datetime(series, utc=True, errors="coerce").dt.tz_localize(None)


def build_monthly_dataset(raw: dict) -> pd.DataFrame:
    frames = []

    oil = raw.get("oil", pd.DataFrame())
    if not oil.empty:
        oil = oil.copy()
        oil["날짜"] = _normalize_date(oil["날짜"])
        oil_wide = oil.pivot_table(index="날짜", columns="유종", values="가격", aggfunc="mean")
        frames.append(oil_wide.resample("MS").mean())

    fx = raw.get("fx", pd.DataFrame())
    if not fx.empty:
        fx = fx.copy()
        fx["날짜"] = _normalize_date(fx["날짜"])
        frames.append(fx.set_index("날짜")["환율"].resample("MS").mean().to_frame("환율"))

    ng = raw.get("ng", pd.DataFrame())
    if not ng.empty:
        ng = ng.copy()
        ng["날짜"] = _normalize_date(ng["날짜"])
        frames.append(ng.set_index("날짜")["가격"].resample("MS").mean().to_frame("천연가스선물"))

    city_gas = raw.get("city_gas", pd.DataFrame())
    if not city_gas.empty:
        city_gas = city_gas.copy()
        city_gas["날짜"] = _normalize_date(city_gas["날짜"])
        frames.append(
            city_gas.set_index("날짜")["산업용요금"].resample("MS").ffill().to_frame("산업용요금")
        )

    if not frames:
        return pd.DataFrame()

    merged = frames[0]
    for f in frames[1:]:
        merged = merged.join(f, how="outer")

    return merged.sort_index().ffill()


def correlation_analysis(monthly_df: pd.DataFrame, target_col: str = "산업용요금") -> pd.DataFrame:
    if target_col not in monthly_df.columns:
        return pd.DataFrame()
    corr = monthly_df.corr(numeric_only=True)[[target_col]]
    return corr.sort_values(target_col, ascending=False)


def build_regression_model(monthly_df: pd.DataFrame, feature_cols: list, target_col: str, lag_months: int = 1):
    model_df = monthly_df.copy()
    for col in feature_cols:
        model_df[col] = model_df[col].shift(lag_months)

    model_df = model_df.dropna(subset=feature_cols + [target_col])
    if model_df.empty:
        return None, model_df, None

    X = model_df[feature_cols].values
    y = model_df[target_col].values

    model = LinearRegression().fit(X, y)
    r2 = r2_score(y, model.predict(X))
    return model, model_df, r2


def forecast_next_months(model, model_df: pd.DataFrame, feature_cols: list, n_months: int = 2) -> pd.DataFrame:
    last_row = model_df[feature_cols].iloc[-1]
    last_date = model_df.index[-1]

    future_dates = pd.date_range(last_date + pd.offsets.MonthBegin(1), periods=n_months, freq="MS")
    future_X = pd.DataFrame([last_row.values] * n_months, columns=feature_cols, index=future_dates)

    preds = model.predict(future_X.values)
    return pd.DataFrame({"예측_산업용요금": preds}, index=future_dates)


def render_plot_base64(monthly_df: pd.DataFrame, forecast_df: pd.DataFrame, target_col: str = "산업용요금") -> str:
    """실적/예측 그래프를 그려서 <img> 태그에 바로 넣을 수 있는 base64 문자열로 반환한다."""
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(monthly_df.index, monthly_df[target_col], marker="o", label="실제")
    if forecast_df is not None and not forecast_df.empty:
        ax.plot(forecast_df.index, forecast_df["예측_산업용요금"], marker="x", linestyle="--", label="예측")
    ax.set_xlabel("날짜")
    ax.set_ylabel("원/MJ")
    ax.set_title("도시가스 산업용요금 실적 및 예측")
    ax.legend()
    fig.tight_layout()

    buf = BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


def estimate_price(model, feature_cols: list, inputs: dict) -> float:
    """2.6 입력기 로직: {컬럼명: 값} 형태 입력을 받아 산업용 도시가스요금을 추정한다."""
    X = np.array([[inputs[col] for col in feature_cols]])
    return float(model.predict(X)[0])


def analyze(data_dir: str = None, lag_months: int = 1, forecast_months: int = 2) -> dict:
    """가격분석 페이지에 필요한 결과를 한 번에 계산해서 반환한다.
    (data_dir는 과거 CSV 버전과의 호출부 호환을 위해 남겨둔 인자로, 지금은 쓰이지 않는다.)
    """
    raw = load_all_price_data()
    monthly_df = build_monthly_dataset(raw)

    if monthly_df.empty or "산업용요금" not in monthly_df.columns:
        return {
            "available": False,
            "message": (
                "가격 데이터(유가/환율/천연가스/도시가스)가 아직 충분하지 않습니다. "
                "가격분석 페이지의 '데이터 수집' 버튼을 먼저 눌러주세요."
            ),
        }

    oil_candidates = [c for c in monthly_df.columns if c not in ("환율", "천연가스선물", "산업용요금")]
    oil_col = "Dubai" if "Dubai" in oil_candidates else (oil_candidates[0] if oil_candidates else None)

    corr_df = correlation_analysis(monthly_df, target_col="산업용요금")

    feature_cols = [c for c in [oil_col, "환율"] if c and c in monthly_df.columns]
    model, model_df, r2 = None, pd.DataFrame(), None
    forecast_df = pd.DataFrame()
    plot_base64 = None

    if feature_cols:
        model, model_df, r2 = build_regression_model(
            monthly_df, feature_cols, target_col="산업용요금", lag_months=lag_months
        )
        if model is not None:
            forecast_df = forecast_next_months(model, model_df, feature_cols, n_months=forecast_months)
            plot_base64 = render_plot_base64(monthly_df, forecast_df)

    return {
        "available": True,
        "monthly_df": monthly_df,
        "corr_df": corr_df,
        "model": model,
        "model_df": model_df,
        "r2": r2,
        "feature_cols": feature_cols,
        "oil_col": oil_col,
        "forecast_df": forecast_df,
        "plot_base64": plot_base64,
    }
