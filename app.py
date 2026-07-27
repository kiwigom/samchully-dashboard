# -*- coding: utf-8 -*-
"""
4. 1~3의 정보를 한눈에 볼 수 있는 파이썬 Flask 사이트 (+ 웹 크롤링/수집 통합)

4.1 사이트 구조 설계
4.1.1 페이지 구성 정의
    - "/"       대시보드   : 뉴스 헤드라인 + 가격 스냅샷 + 지도 바로가기 (한눈에 보기)
    - "/news"   뉴스       : 고객사/동종사/관심뉴스/에너지 뉴스 전체 목록 + [데이터 수집] 버튼
    - "/price"  가격분석   : 상관관계 표 + 실적/예측 그래프 + 유가·환율 입력기 + [데이터 수집] 버튼
    - "/map"    LNG선 지도 : Folium 지도(마커+이동경로) + [데이터 수집] 버튼

각 페이지의 [데이터 수집] 버튼은 1~3단계의 크롤링/수집 로직(뉴스 검색, 유가·환율·천연가스
API 조회, KOGAS 요금표 파싱, AIS 실시간 수집)을 그 자리에서 바로 실행해 CSV를 새로 만들고,
그 결과로 페이지를 다시 그려준다. 즉, 스크립트를 따로 실행할 필요 없이 이 사이트 하나로
"수집 -> 분석 -> 시각화"가 전부 끝난다.

4.1.2 화면 레이아웃 와이어프레임 (텍스트 스케치)
    +--------------------------------------------------+
    | 삼천리 통합 대시보드   [대시보드][뉴스][가격분석][지도] |
    +--------------------------------------------------+
    | 최신 뉴스 카드                                     |
    +--------------------------------------------------+
    | 도시가스 산업용요금 스냅샷 카드                      |
    +--------------------------------------------------+
    | LNG선 위치 카드                                    |
    +--------------------------------------------------+

4.2 Flask 프로젝트 셋업
4.2.1 프로젝트 폴더 구조
    flask_dashboard/
    ├── app.py
    ├── config.py              <- 크롤링 대상, API 키, 데이터 폴더 등 설정
    ├── services/
    │   ├── news_service.py    <- 1.1~1.4 크롤링 + 4.3.1 데이터 연동
    │   ├── price_service.py   <- 2.1~2.4 수집/파싱 + 4.3.2 분석·입력기
    │   └── ship_service.py    <- 3.3 AIS 수집 + 4.3.3 지도
    ├── templates/
    ├── static/
    └── requirements.txt

실행 방법 (4.5.1 로컬 테스트):
    pip install -r requirements.txt
    python app.py
    -> 브라우저에서 http://127.0.0.1:5000 접속

config.py의 API 키(EIA/오피넷/ECOS/aisstream)를 채워 넣어야 실제 수집이 됩니다.
키가 없어도 유가/환율은 자동으로 무료 대체 소스(yfinance/Frankfurter)로 동작합니다.
"""
import os

from flask import Flask, Response, flash, redirect, render_template, request, url_for

import config
from services import news_service, price_service, ship_service

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "samchully-dashboard-dev-key")


# ------------------------------------------------------------------
# 4.4.4 통합 메인 대시보드 페이지
# ------------------------------------------------------------------
@app.route("/")
def index():
    headlines = news_service.get_latest_headlines(n=6)
    price_result = price_service.analyze(lag_months=config.LAG_MONTHS, forecast_months=config.FORECAST_MONTHS)
    return render_template("index.html", headlines=headlines, price=price_result)


# ------------------------------------------------------------------
# 4.4.1 뉴스 대시보드 페이지
# ------------------------------------------------------------------
@app.route("/news")
def news_page():
    news = news_service.get_all_news(top_n=config.TOP_N_NEWS)
    return render_template("news.html", news=news)


@app.route("/refresh/news", methods=["POST"])
def refresh_news():
    """1.1~1.4 뉴스 크롤링을 그 자리에서 실행하고, 결과를 Supabase에 저장한다."""
    try:
        summary = news_service.collect_all_news(
            clients=config.CLIENTS,
            client_keywords=config.CLIENT_KEYWORDS,
            peers=config.PEERS,
            peer_keywords=config.PEER_KEYWORDS,
            interest_keywords=config.INTEREST_KEYWORDS,
            energy_keywords=config.ENERGY_KEYWORDS,
            top_n=config.TOP_N_NEWS,
        )
        total = sum(sum(v.values()) for v in summary.values())
        flash(f"뉴스 수집 완료: 총 {total}건 (고객사/동종사/관심뉴스/에너지)", "success")
    except Exception as e:
        flash(f"뉴스 수집 실패: {e}", "error")
    return redirect(url_for("news_page"))


# ------------------------------------------------------------------
# 4.4.2 가격 상관관계/유추 페이지 (+ 2.6 유가·환율 입력기)
# ------------------------------------------------------------------
@app.route("/price", methods=["GET", "POST"])
def price_page():
    result = price_service.analyze(lag_months=config.LAG_MONTHS, forecast_months=config.FORECAST_MONTHS)

    estimate = None
    error = None
    if request.method == "POST" and result.get("available") and result.get("model") is not None:
        try:
            oil_value = float(request.form.get("oil_price", ""))
            fx_value = float(request.form.get("fx_rate", ""))
            inputs = {result["oil_col"]: oil_value, "환율": fx_value}
            estimate = price_service.estimate_price(result["model"], result["feature_cols"], inputs)
        except (TypeError, ValueError):
            error = "숫자를 정확히 입력해주세요."

    return render_template("price.html", result=result, estimate=estimate, error=error)


@app.route("/refresh/price", methods=["POST"])
def refresh_price():
    """2.1~2.4 유가/환율/천연가스/도시가스요금 수집을 그 자리에서 실행하고, Supabase에 저장한다."""
    status = price_service.collect_all_price_data(
        kogas_raw_dir=config.KOGAS_RAW_DIR,
        eia_api_key=config.EIA_API_KEY,
        opinet_api_key=config.OPINET_API_KEY,
        ecos_api_key=config.ECOS_API_KEY,
    )
    summary = ", ".join(f"{k}: {v}" for k, v in status.items())
    has_fail = any("실패" in v for v in status.values())
    flash(summary, "error" if has_fail else "success")
    return redirect(url_for("price_page"))


# ------------------------------------------------------------------
# 4.4.3 LNG선 위치 지도 페이지
# ------------------------------------------------------------------
@app.route("/map")
def map_page():
    return render_template("map.html")


@app.route("/map/embed")
def map_embed():
    """Folium 지도를 파일로 저장하지 않고 메모리에서 바로 렌더링해서 iframe에 넣는다."""
    html = ship_service.generate_map_html()
    return Response(html, mimetype="text/html")


@app.route("/refresh/map", methods=["POST"])
def refresh_map():
    """3.3 AIS 실시간 위치 수집을 그 자리에서 config.AIS_DURATION_SEC초 동안 실행하고, Supabase에 저장한다."""
    try:
        count = ship_service.collect_positions(
            config.AISSTREAM_API_KEY, config.VESSELS, duration_sec=config.AIS_DURATION_SEC
        )
        flash(f"LNG선 위치 {count}건 수집 완료 ({config.AIS_DURATION_SEC}초 동안 대기)", "success")
    except Exception as e:
        flash(f"LNG선 위치 수집 실패: {e}", "error")
    return redirect(url_for("map_page"))


if __name__ == "__main__":
    # 4.5.1 로컬 테스트: debug=True는 개발 중에만 사용 (운영 배포 시에는 False로)
    app.run(debug=True, host="0.0.0.0", port=5000)
