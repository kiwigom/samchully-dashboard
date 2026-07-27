# -*- coding: utf-8 -*-
"""
GitHub Actions(또는 로컬 cron)에서 주기적으로 실행하는 수집 스크립트.

Vercel 서버리스는 실행시간 제한(Hobby 10초/Pro 60초)이 있어 뉴스 크롤링(1~2분),
AIS 수집(60초 대기) 같은 오래 걸리는 작업을 처리할 수 없다. 그래서 실제 배포 사이트는
Supabase에서 "이미 수집된 데이터"를 읽어서 보여주기만 하고, 수집 자체는 이 스크립트를
GitHub Actions 스케줄로 돌려서 Supabase에 채워 넣는다.

사용법:
    python scripts/run_collect.py news    # 1.1~1.4 뉴스만
    python scripts/run_collect.py price   # 2.1~2.4 유가/환율/천연가스/도시가스요금만
    python scripts/run_collect.py ship    # 3.3 LNG선 AIS 위치만
    python scripts/run_collect.py all     # 전부 (기본값)

필요 환경변수 (.env 또는 GitHub repo secrets):
    SUPABASE_URL, SUPABASE_KEY
    EIA_API_KEY, OPINET_API_KEY, ECOS_API_KEY   (없어도 유가/환율은 대체 소스로 동작)
    AISSTREAM_API_KEY                            (없으면 ship 수집은 건너뜀)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from services import news_service, price_service, ship_service  # noqa: E402


def run_news():
    summary = news_service.collect_all_news(
        clients=config.CLIENTS,
        client_keywords=config.CLIENT_KEYWORDS,
        peers=config.PEERS,
        peer_keywords=config.PEER_KEYWORDS,
        interest_keywords=config.INTEREST_KEYWORDS,
        energy_keywords=config.ENERGY_KEYWORDS,
        top_n=config.TOP_N_NEWS,
    )
    print("[뉴스 수집 완료]", summary)


def run_price():
    status = price_service.collect_all_price_data(
        kogas_raw_dir=config.KOGAS_RAW_DIR,
        eia_api_key=config.EIA_API_KEY,
        opinet_api_key=config.OPINET_API_KEY,
        ecos_api_key=config.ECOS_API_KEY,
    )
    print("[가격 수집 완료]", status)


def run_ship():
    if not config.AISSTREAM_API_KEY or config.AISSTREAM_API_KEY == "YOUR_AISSTREAM_API_KEY":
        print("[LNG선 위치 수집 건너뜀] AISSTREAM_API_KEY가 설정되지 않았습니다.")
        return
    try:
        count = ship_service.collect_positions(
            config.AISSTREAM_API_KEY, config.VESSELS, duration_sec=config.AIS_DURATION_SEC
        )
        print(f"[LNG선 위치 수집 완료] {count}건")
    except Exception as e:
        print(f"[LNG선 위치 수집 실패] {e}")


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "all"

    if target in ("news", "all"):
        run_news()
    if target in ("price", "all"):
        run_price()
    if target in ("ship", "all"):
        run_ship()

    if target not in ("news", "price", "ship", "all"):
        print(f"알 수 없는 대상: {target} (news / price / ship / all 중 하나를 입력하세요)")
        sys.exit(1)


if __name__ == "__main__":
    main()
