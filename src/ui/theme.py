"""Streamlit 테마 CSS 적용."""

from __future__ import annotations

from pathlib import Path

import streamlit as st


_STYLE_PATH = Path(__file__).with_name("styles.css")


def apply_theme() -> None:
    """분리된 CSS 파일을 읽어 Streamlit 앱에 적용한다."""
    css = _STYLE_PATH.read_text(encoding="utf-8")
    st.markdown(f"<style>\n{css}\n</style>", unsafe_allow_html=True)
