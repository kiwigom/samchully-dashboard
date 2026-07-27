# -*- coding: utf-8 -*-
"""
4.2.2 Flask 앱 설정

DATA_DIR: 1~3단계에서 만든/수집한 CSV들이 저장되는 폴더 경로.
기본값은 "flask_dashboard의 부모 폴더"이다 (지금 구조상 CSV들이 전부 그 폴더에 있기 때문).
환경변수 DASHBOARD_DATA_DIR로 다른 경로를 지정할 수도 있다.
"""
import os

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass  # python-dotenv가 없으면 그냥 시스템 환경변수만 사용 (Vercel/GitHub Actions는 항상 이 경로)

_PARENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("DASHBOARD_DATA_DIR", _PARENT_DIR)

TOP_N_NEWS = 10        # 뉴스 페이지에 표시할 항목별 최대 건수
FORECAST_MONTHS = 2    # 가격 예측 개월 수 (2.5의 "1~2개월 예측"에 대응)
LAG_MONTHS = 1         # 유가/환율이 도시가스요금에 반영되는 시차(개월)


# ------------------------------------------------------------------
# 1.1~1.4 뉴스 크롤링 대상 설정
# ------------------------------------------------------------------
CLIENTS = ["삼성전자", "기아", "현대자동차", "LG전자", "SK하이닉스"]
CLIENT_KEYWORDS = {
    "삼성전자": ["삼성전자"],
    "기아": ["기아", "기아자동차"],
    "현대자동차": ["현대자동차", "현대차"],
    "LG전자": ["LG전자"],
    "SK하이닉스": ["SK하이닉스"],
}

PEERS = ["코원도시가스", "경동", "미래엔서해", "JB"]
PEER_KEYWORDS = {
    "코원도시가스": ["코원도시가스"],
    "경동": ["경동도시가스", "경동나비엔"],
    "미래엔서해": ["미래엔서해", "미래엔"],
    "JB": ["JB에너지", "JB 도시가스"],
}

INTEREST_KEYWORDS = ["열병합", "데이터센터", "GHP"]
ENERGY_KEYWORDS = ["두바이유", "천연가스"]


# ------------------------------------------------------------------
# 2.1~2.4 가격 데이터 수집 설정
# ------------------------------------------------------------------
# 무료 API Key 발급: https://www.eia.gov/opendata/register.php
EIA_API_KEY = os.environ.get("EIA_API_KEY", "YOUR_EIA_API_KEY")
# 무료 API Key 발급: http://www.opinet.co.kr (회원가입 > API 신청)
OPINET_API_KEY = os.environ.get("OPINET_API_KEY", "YOUR_OPINET_API_KEY")
# 무료 API Key 발급: https://ecos.bok.or.kr
ECOS_API_KEY = os.environ.get("ECOS_API_KEY", "YOUR_ECOS_API_KEY")

# KOGAS에서 다운로드한 "용도별 도매요금표" CSV들이 있는 폴더
# (도시가스 산업용요금 계산의 원본 데이터. 뉴스/유가 CSV와는 별개 폴더에 있을 수 있음)
#
# - 로컬(내 PC)에서 실행할 때: 기본값은 실제 데스크탑 폴더를 그대로 가리킨다.
# - GitHub Actions(리눅스 러너)에서는 그 경로가 존재하지 않으므로, 저장소에 커밋해 둔
#   flask_dashboard/data/kogas_raw 폴더가 있으면 그쪽을 우선 사용한다.
#   (GitHub Actions로 도시가스요금까지 자동 수집하려면 KOGAS CSV들을 그 폴더에 커밋해야 한다.)
_REPO_KOGAS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "kogas_raw")
_DEFAULT_KOGAS_DIR = (
    _REPO_KOGAS_DIR
    if os.path.isdir(_REPO_KOGAS_DIR)
    else r"C:\Users\student\Desktop\삼천리 프로젝트\천연가스가격"
)
KOGAS_RAW_DIR = os.environ.get("KOGAS_RAW_DIR", _DEFAULT_KOGAS_DIR)


# ------------------------------------------------------------------
# 3.1~3.3 LNG선 AIS 위치 수집 설정
# ------------------------------------------------------------------
# 무료 API Key 발급: https://aisstream.io/apikeys (GitHub 로그인)
AISSTREAM_API_KEY = os.environ.get("AISSTREAM_API_KEY", "YOUR_AISSTREAM_API_KEY")

# TODO: 실제 추적하려는 LNG선으로 교체하세요.
VESSELS = [
    {"name": "LNG SEOKCHEON", "mmsi": "440123456", "imo": "9876543"},
    {"name": "LNG SAMCHEONLI", "mmsi": "440654321", "imo": "9123456"},
]

# 웹에서 "새로고침" 버튼을 눌렀을 때 AIS 신호를 수집할 시간(초).
# 너무 길면 페이지 응답이 오래 걸리므로 짧게 잡고, 더 오래/주기적으로 모으려면
# 3_3_위치데이터수집.py를 Windows 작업 스케줄러 등으로 별도 예약 실행하는 것을 권장.
AIS_DURATION_SEC = int(os.environ.get("AIS_DURATION_SEC", "60"))
