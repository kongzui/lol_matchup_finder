"""LoL 매치업 상대 닉네임 추출기 Streamlit 실행기."""

from __future__ import annotations

import streamlit as st

from src.config import load_config
from src.ui.app_view import render_app
from src.ui.theme import apply_theme


def main() -> None:
    """Streamlit 앱을 설정하고 화면 렌더링을 위임한다."""
    st.set_page_config(
        page_title="LoL 매치업 상대 닉네임 추출기",
        page_icon="🎯",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    apply_theme()
    render_app(load_config())


if __name__ == "__main__":
    main()
