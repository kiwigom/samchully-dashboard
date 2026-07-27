# -*- coding: utf-8 -*-
"""
Vercel 서버리스 진입점.

Vercel의 Python 런타임(@vercel/python)은 이 파일에서 WSGI 앱 객체 `app`을 찾아 실행한다.
실제 라우트/로직은 전부 상위 폴더의 app.py(및 services/*, templates/*, static/*)에 있고,
이 파일은 그것을 그대로 불러오기만 하는 얇은 래퍼다.
"""
import os
import sys

# api/ 폴더의 부모(=flask_dashboard 루트)를 import 경로에 추가해야
# "import config", "from services import ..." 가 그대로 동작한다.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402,F401
