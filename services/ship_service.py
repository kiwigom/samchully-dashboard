# -*- coding: utf-8 -*-
"""
4.3.3 3번(LNG선 위치 지도) 데이터 연동 + 실시간 AIS 수집 (Supabase 저장)

1) 수집: 3.3 로직(aisstream.io WebSocket)을 그대로 가져와 collect_positions()로 제공한다.
   결과는 로컬 CSV가 아니라 Supabase "lng_positions" 테이블에 append(insert)된다.
2) 지도: 3.4 로직(Folium)으로 Supabase에 쌓인 위치 데이터를 지도로 그린다.
   Vercel은 파일시스템이 읽기전용(임시 /tmp 제외)이라, 지도는 파일로 저장하지 않고
   메모리에서 바로 HTML 문자열로 렌더링해서 반환한다 (generate_map_html).
"""
import asyncio
import json
from datetime import datetime, timezone

import folium
import pandas as pd
import websockets

from services import db

AISSTREAM_WS_URL = "wss://stream.aisstream.io/v0/stream"


# ------------------------------------------------------------------
# 3.3 AIS 실시간 위치 수집 (aisstream.io)
# ------------------------------------------------------------------
async def _collect_ais_positions(api_key: str, mmsi_list: list, duration_sec: int, bounding_box: list = None) -> list:
    bounding_box = bounding_box or [[[-90, -180], [90, 180]]]  # 기본값: 전세계

    subscribe_message = {
        "APIKey": api_key,
        "BoundingBoxes": bounding_box,
        "FiltersShipMMSI": mmsi_list,
        "FilterMessageTypes": ["PositionReport"],
    }

    records = []
    async with websockets.connect(AISSTREAM_WS_URL) as ws:
        await ws.send(json.dumps(subscribe_message))

        loop = asyncio.get_event_loop()
        end_time = loop.time() + duration_sec

        while loop.time() < end_time:
            remaining = end_time - loop.time()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break

            message = json.loads(raw)
            if message.get("MessageType") != "PositionReport":
                continue

            report = message["Message"]["PositionReport"]
            meta = message.get("MetaData", {})

            records.append(
                {
                    "mmsi": str(report.get("UserID")),
                    "ship_name": (meta.get("ShipName") or "").strip(),
                    "lat": report.get("Latitude"),
                    "lon": report.get("Longitude"),
                    "sog": report.get("Sog"),
                    "cog": report.get("Cog"),
                    "timestamp_utc": meta.get("time_utc", datetime.now(timezone.utc).isoformat()),
                }
            )

    return records


def collect_positions(api_key: str, vessels: list, duration_sec: int = 60) -> int:
    """vessels(3.2의 VESSELS)에서 MMSI를 뽑아 aisstream에 접속, duration_sec 동안 수집 후 Supabase에 insert.
    반환값: 이번에 수집된 건수.
    """
    if not api_key or api_key == "YOUR_AISSTREAM_API_KEY":
        raise ValueError("AISSTREAM_API_KEY가 설정되지 않았습니다. https://aisstream.io/apikeys 에서 발급하세요.")

    mmsi_list = [v["mmsi"] for v in vessels if v.get("mmsi")][:50]
    if not mmsi_list:
        raise ValueError("추적할 선박(MMSI)이 config.py의 VESSELS에 등록되어 있지 않습니다.")

    records = asyncio.run(_collect_ais_positions(api_key, mmsi_list, duration_sec))
    db.insert_rows("lng_positions", records)
    return len(records)


# ------------------------------------------------------------------
# 3.4 지도 시각화 (Folium)
# ------------------------------------------------------------------
def load_positions(**_ignored) -> pd.DataFrame:
    """Supabase lng_positions 테이블을 과거 CSV 버전과 동일한 한글 컬럼명 DataFrame으로 반환한다."""
    rows = db.fetch_all("lng_positions", order_by="timestamp_utc")
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).rename(
        columns={
            "mmsi": "MMSI",
            "ship_name": "선박명",
            "lat": "위도",
            "lon": "경도",
            "sog": "속력(SOG)",
            "cog": "침로(COG)",
            "timestamp_utc": "시각_UTC",
        }
    )

    df["시각_UTC"] = pd.to_datetime(df["시각_UTC"], utc=True, format="mixed", errors="coerce")
    df = df.dropna(subset=["위도", "경도"])
    return df.sort_values(["MMSI", "시각_UTC"])


def build_ship_map(df: pd.DataFrame) -> folium.Map:
    if df.empty:
        m = folium.Map(location=[36.5, 127.8], zoom_start=6, tiles="OpenStreetMap")
        folium.Marker(
            location=[36.5, 127.8],
            popup="아직 수집된 LNG선 위치 데이터가 없습니다. '데이터 수집' 버튼을 눌러보세요.",
        ).add_to(m)
        return m

    center = [df["위도"].mean(), df["경도"].mean()]
    m = folium.Map(location=center, zoom_start=4, tiles="OpenStreetMap")

    colors = ["red", "blue", "green", "purple", "orange", "darkred", "cadetblue"]

    for i, (mmsi, group) in enumerate(df.groupby("MMSI")):
        color = colors[i % len(colors)]
        ship_name = group["선박명"].dropna().iloc[-1] if group["선박명"].notna().any() else str(mmsi)

        track_points = list(zip(group["위도"], group["경도"]))
        folium.PolyLine(track_points, color=color, weight=2, opacity=0.7, tooltip=ship_name).add_to(m)

        last = group.iloc[-1]
        popup_html = (
            f"<b>{ship_name}</b><br>"
            f"MMSI: {mmsi}<br>"
            f"속력: {last['속력(SOG)']} kn<br>"
            f"침로: {last['침로(COG)']}&deg;<br>"
            f"시각(UTC): {last['시각_UTC']}"
        )
        folium.Marker(
            location=[last["위도"], last["경도"]],
            popup=folium.Popup(popup_html, max_width=250),
            tooltip=ship_name,
            icon=folium.Icon(color=color, icon="ship", prefix="fa"),
        ).add_to(m)

    return m


def generate_map_html(**_ignored) -> str:
    """Supabase 위치 데이터로 지도를 만들어 HTML 문자열로 바로 반환한다 (파일로 저장하지 않음).
    Vercel 등 서버리스 환경에서도 동작하도록 메모리에서만 렌더링한다.
    """
    df = load_positions()
    m = build_ship_map(df)
    return m.get_root().render()
