"""LoL 매치업 상대 닉네임 추출기 (Streamlit MVP)."""

from __future__ import annotations

import io
import os
import time
from datetime import date, timedelta
from typing import Any

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from src.cache import MatchCache, normalize_riot_id_key
from src.champions import ChampionData, ChampionRepository
from src.matchup_filter import extract_focus_view, extract_matchup_result
from src.opgg import build_opgg_url
from src.riot_client import (
    RiotApiAuthError,
    RiotApiError,
    RiotApiNotFound,
    RiotApiRateLimited,
    RiotClient,
)
from src.static_data import (
    StaticData,
    StaticDataRepository,
    item_icon_url,
    stat_shard_icon_url,
)
from src.utils import (
    LANE_LABEL_TO_TEAM_POSITION,
    LANE_LABELS,
    RiotIdParseError,
    date_range_to_unix,
    days_ago_to_unix_range,
    parse_riot_id,
    unix_to_kst_datetime_str,
)


PAGE_SIZE = 100  # Match-V5 한 번에 100개까지
MAX_PAGES = 5  # 안전장치: 최대 500매치까지만 페이지네이션

# 검색 기간 프리셋: 라벨 → 일수
PERIOD_PRESETS: dict[str, int] = {
    "최근 1일": 1,
    "최근 3일": 3,
    "최근 7일": 7,
    "최근 14일": 14,
    "최근 30일": 30,
    "최근 90일": 90,
    "최근 180일": 180,
}
PERIOD_CUSTOM_LABEL = "직접 지정"
PERIOD_OPTIONS: tuple[str, ...] = tuple(PERIOD_PRESETS.keys()) + (PERIOD_CUSTOM_LABEL,)
DEFAULT_PERIOD_LABEL = "최근 30일"


# ---- 사용자 정의 스타일 (Hextech 다크 테마) ----
CUSTOM_CSS = """
<style>
/* 전체 배경 */
.stApp { background: #010a13; }
[data-testid="stHeader"] { background: transparent; }
[data-testid="stSidebar"] {
    background: #0a1428;
    border-right: 1px solid #1e2a3a;
}
.block-container { padding-top: 2rem; max-width: 1280px; }

/* 기본 글자 색 */
.stMarkdown, .stMarkdown p, label, .stCaption {
    color: #cdbe91 !important;
}

/* 입력/셀렉트 */
.stTextInput input, .stSelectbox div[data-baseweb="select"] > div,
.stDateInput input {
    background: #0f1923 !important;
    border: 1px solid #2a3f5f !important;
    color: #f0e6d2 !important;
    border-radius: 8px !important;
}
.stSelectbox label, .stTextInput label, .stDateInput label {
    color: #a09b8c !important; font-size: 12px !important;
    font-weight: 600 !important; letter-spacing: 0.5px;
}

/* 버튼 */
.stButton > button {
    background: linear-gradient(135deg, #c89b3c 0%, #b8853a 100%);
    color: #010a13;
    border: none;
    border-radius: 10px;
    font-weight: 700;
    padding: 10px 20px;
    transition: all 0.15s;
}
.stButton > button:hover {
    background: linear-gradient(135deg, #f0c878 0%, #c89b3c 100%);
    color: #010a13;
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(200, 155, 60, 0.3);
}
.stButton > button[kind="primary"] {
    font-size: 15px;
    padding: 14px 20px;
}

/* 사이드바 버튼은 다른 톤 */
[data-testid="stSidebar"] .stButton > button {
    background: #1e2a3a;
    color: #cdbe91;
}
[data-testid="stSidebar"] .stButton > button:hover {
    background: #2a3f5f;
    color: #f0e6d2;
    box-shadow: none;
}

/* 다운로드 버튼 */
.stDownloadButton > button {
    background: #1e2a3a;
    color: #cdbe91;
    border: 1px solid #2a3f5f;
    border-radius: 8px;
}
.stDownloadButton > button:hover {
    background: #2a3f5f;
    color: #f0e6d2;
}

/* 히어로 */
.matchup-hero {
    padding: 28px 32px;
    border-radius: 16px;
    background:
        radial-gradient(circle at 10% 20%, rgba(200, 155, 60, 0.15) 0%, transparent 45%),
        radial-gradient(circle at 90% 100%, rgba(70, 130, 200, 0.18) 0%, transparent 50%),
        linear-gradient(135deg, #0a1428 0%, #091428 50%, #0a1f3d 100%);
    border: 1px solid rgba(200, 155, 60, 0.25);
    margin-bottom: 24px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
}
.matchup-hero h1 {
    margin: 0;
    font-size: 30px;
    font-weight: 700;
    letter-spacing: -0.5px;
    background: linear-gradient(135deg, #f0e6d2 0%, #c89b3c 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}
.matchup-hero .sub {
    color: #a09b8c;
    margin-top: 8px;
    font-size: 14px;
    line-height: 1.5;
}
.matchup-hero .meta {
    margin-top: 14px;
    display: flex; gap: 8px; flex-wrap: wrap;
}
.hero-chip {
    padding: 4px 12px;
    background: rgba(200, 155, 60, 0.12);
    color: #c89b3c;
    border: 1px solid rgba(200, 155, 60, 0.35);
    border-radius: 999px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
}

/* 섹션 헤더 */
.section-title {
    color: #f0e6d2;
    font-size: 16px;
    font-weight: 700;
    margin: 28px 0 14px 0;
    padding-bottom: 10px;
    border-bottom: 1px solid #1e2a3a;
    display: flex; align-items: center; gap: 8px;
}
.section-title::before {
    content: "";
    display: inline-block;
    width: 3px; height: 16px;
    background: #c89b3c;
    border-radius: 2px;
}

/* 챔피언 프리뷰 */
.preview-row {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 28px;
    padding: 18px;
    background: #0f1923;
    border: 1px solid #1e2a3a;
    border-radius: 12px;
    margin: 8px 0 16px 0;
}
.preview-champ {
    display: flex; flex-direction: column; align-items: center; gap: 8px;
}
.preview-champ img {
    width: 72px; height: 72px;
    border-radius: 50%;
    border: 3px solid #2a3f5f;
    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
}
.preview-champ.ally img { border-color: #1abc9c; }
.preview-champ.enemy img { border-color: #e74c3c; }
.preview-champ .name {
    color: #f0e6d2;
    font-size: 14px;
    font-weight: 600;
}
.preview-vs {
    font-size: 20px;
    font-weight: 800;
    color: #c89b3c;
    letter-spacing: 2px;
    padding: 0 8px;
}

/* KPI */
.kpi-row {
    display: grid;
    grid-template-columns: 1.8fr 1fr 1fr 1fr 1fr;
    gap: 12px;
    margin: 8px 0 20px 0;
}
.kpi-card {
    padding: 18px 20px;
    background: #0f1923;
    border: 1px solid #1e2a3a;
    border-radius: 12px;
}
.kpi-card .label {
    color: #8a8f97;
    font-size: 10px;
    letter-spacing: 1.2px;
    text-transform: uppercase;
    margin-bottom: 6px;
    font-weight: 600;
}
.kpi-card .value {
    color: #f0e6d2;
    font-size: 26px;
    font-weight: 700;
    line-height: 1.1;
}
.kpi-card .sub {
    color: #6c7079;
    font-size: 12px;
    margin-top: 4px;
}
.kpi-card.winrate {
    background:
        radial-gradient(circle at 100% 0%, rgba(200, 155, 60, 0.15) 0%, transparent 60%),
        linear-gradient(135deg, #1a2942 0%, #0f1923 100%);
    border-color: rgba(200, 155, 60, 0.35);
}
.kpi-card.winrate .value {
    font-size: 36px;
    background: linear-gradient(135deg, #f0e6d2 0%, #c89b3c 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}
.kpi-card.win .value { color: #1abc9c; }
.kpi-card.loss .value { color: #e74c3c; }

/* 검색 조건 요약 */
.search-summary {
    padding: 14px 18px;
    background: #0f1923;
    border-left: 3px solid #c89b3c;
    border-radius: 6px;
    margin-bottom: 16px;
    display: flex;
    flex-wrap: wrap;
    gap: 12px 22px;
    font-size: 13px;
    color: #a09b8c;
}
.search-summary b { color: #f0e6d2; font-weight: 600; }

/* OP.GG 큰 링크 (검색 대상) */
.target-opgg {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 10px 16px;
    background: linear-gradient(135deg, #1e2a3a 0%, #0f1923 100%);
    color: #f0e6d2 !important;
    text-decoration: none !important;
    border: 1px solid rgba(200, 155, 60, 0.3);
    border-radius: 10px;
    font-size: 13px;
    font-weight: 600;
    margin-bottom: 16px;
    transition: all 0.15s;
}
.target-opgg:hover {
    border-color: #c89b3c;
    background: linear-gradient(135deg, #2a3f5f 0%, #1a2942 100%);
    transform: translateY(-1px);
}
.target-opgg .arrow { color: #c89b3c; }

/* 결과 카드 */
.match-card {
    position: relative;
    display: grid;
    grid-template-columns: 100px 1.4fr 80px 1fr 1.8fr;
    align-items: center;
    gap: 14px;
    padding: 14px 18px 14px 22px;
    background: #0f1923;
    border: 1px solid #141f2c;
    border-radius: 12px;
    margin-bottom: 6px;
    overflow: hidden;
    transition: all 0.15s;
}
.match-card:hover {
    border-color: #2a3f5f;
    transform: translateX(2px);
}
.match-card::before {
    content: "";
    position: absolute;
    left: 0; top: 0; bottom: 0;
    width: 5px;
}
.match-card.win::before { background: #1abc9c; }
.match-card.loss::before { background: #e74c3c; }

.match-date { font-size: 12px; }
.match-date .day { color: #cdbe91; font-weight: 600; }
.match-date .time { color: #6c7079; font-size: 11px; margin-top: 2px; }

.match-champs {
    display: flex; align-items: center; gap: 10px;
}
.match-champs .champ {
    display: flex; flex-direction: column; align-items: center; gap: 4px;
    min-width: 56px;
}
.match-champs img {
    width: 42px; height: 42px;
    border-radius: 50%;
    border: 2px solid #2a3f5f;
}
.match-champs .champ.ally img { border-color: rgba(26, 188, 156, 0.5); }
.match-champs .champ.enemy img { border-color: rgba(231, 76, 60, 0.5); }
.match-champs .champ .name {
    color: #cdbe91; font-size: 10px; max-width: 56px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.match-champs .vs {
    color: #6c7079;
    font-weight: 700;
    font-size: 11px;
}

.result-pill {
    display: inline-block;
    padding: 6px 0;
    width: 70px;
    text-align: center;
    border-radius: 8px;
    font-weight: 700;
    font-size: 13px;
    letter-spacing: 1px;
}
.result-pill.win {
    background: rgba(26, 188, 156, 0.15);
    color: #1abc9c;
    border: 1px solid rgba(26, 188, 156, 0.4);
}
.result-pill.loss {
    background: rgba(231, 76, 60, 0.15);
    color: #e74c3c;
    border: 1px solid rgba(231, 76, 60, 0.4);
}

.match-stats {
    color: #cdbe91;
    font-size: 13px;
    line-height: 1.4;
}
.match-stats .kda { font-weight: 700; font-size: 14px; color: #f0e6d2; }
.match-stats .cs { color: #8a8f97; font-size: 11px; margin-top: 2px; }

.match-enemy { min-width: 0; }
.match-enemy .name {
    color: #f0e6d2;
    font-size: 15px;
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.match-enemy .tag { color: #6c7079; font-size: 12px; margin-top: 2px; }

.opgg-link {
    display: inline-block;
    padding: 7px 0;
    width: 100%;
    text-align: center;
    background: rgba(200, 155, 60, 0.1);
    color: #c89b3c !important;
    border: 1px solid rgba(200, 155, 60, 0.35);
    border-radius: 8px;
    text-decoration: none !important;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.5px;
    transition: all 0.15s;
}
.opgg-link:hover {
    background: rgba(200, 155, 60, 0.25);
    border-color: #c89b3c;
}

/* 결과 비어있을 때 */
.empty-card {
    padding: 40px 30px;
    background: #0f1923;
    border: 1px dashed #2a3f5f;
    border-radius: 12px;
    text-align: center;
    color: #a09b8c;
}
.empty-card .title {
    color: #f0e6d2;
    font-size: 16px;
    font-weight: 600;
    margin-bottom: 10px;
}
.empty-card ul {
    text-align: left;
    display: inline-block;
    margin-top: 8px;
    font-size: 13px;
}

/* st.code 우측 정렬용 (복사 버튼) */
.copy-wrap { margin-top: -6px; }
.copy-wrap .stCode { margin-bottom: 4px; }

/* 진행 표시 */
.stProgress > div > div { background: linear-gradient(90deg, #c89b3c 0%, #f0c878 100%); }

/* alert info/success/error 톤 살짝 */
.stAlert { border-radius: 10px; }

/* ---- 매치 상세 패널 (Focus 뷰) ---- */
.detail-panel {
    padding: 14px 16px;
    background: #0a1421;
    border-radius: 10px;
    border: 1px solid #1e2a3a;
}
.detail-section-title {
    color: #8a8f97;
    font-size: 11px;
    letter-spacing: 1.2px;
    text-transform: uppercase;
    font-weight: 600;
    margin: 14px 0 10px 0;
    padding-bottom: 6px;
    border-bottom: 1px solid #141f2c;
}
.detail-section-title:first-child { margin-top: 0; }

.detail-player {
    position: relative;
    padding: 14px 16px 14px 22px;
    background: #0f1923;
    border-radius: 10px;
    margin-bottom: 10px;
    overflow: hidden;
}
.detail-player::before {
    content: "";
    position: absolute;
    left: 0; top: 0; bottom: 0;
    width: 4px;
}
.detail-player.win::before { background: #1abc9c; }
.detail-player.loss::before { background: #e74c3c; }

.detail-head {
    display: grid;
    grid-template-columns: 70px 50px 1fr auto;
    gap: 14px;
    align-items: center;
    margin-bottom: 10px;
}

.detail-champ { position: relative; width: 52px; height: 52px; }
.detail-champ img {
    width: 52px; height: 52px;
    border-radius: 50%;
    border: 2px solid #2a3f5f;
}
.detail-champ .lv {
    position: absolute;
    bottom: -2px; right: -2px;
    background: #0a1428;
    color: #f0e6d2;
    font-size: 10px;
    font-weight: 700;
    padding: 1px 5px;
    border-radius: 8px;
    border: 1px solid #2a3f5f;
}

.detail-spells {
    display: grid;
    grid-template-rows: 22px 22px;
    gap: 4px;
}
.detail-spells img {
    width: 22px; height: 22px;
    border-radius: 4px;
    border: 1px solid #2a3f5f;
}

.detail-items {
    display: grid;
    grid-template-columns: repeat(6, 30px) 8px 30px;
    gap: 4px;
}
.detail-item {
    width: 30px; height: 30px;
    background: #050d18;
    border: 1px solid #1e2a3a;
    border-radius: 4px;
    overflow: hidden;
}
.detail-item img { width: 100%; height: 100%; display: block; }
.detail-items .gap { width: 8px; }

.detail-stats {
    text-align: right;
    color: #cdbe91;
    font-size: 12px;
    line-height: 1.5;
}
.detail-stats .name {
    color: #f0e6d2; font-size: 14px; font-weight: 600;
}
.detail-stats .name .tag { color: #6c7079; font-size: 12px; margin-left: 2px; }
.detail-stats .kda { color: #f0e6d2; font-weight: 700; font-size: 13px; margin-top: 2px; }
.detail-stats .kda .ratio { color: #c89b3c; font-weight: 600; margin-left: 6px; }
.detail-stats .extras { color: #8a8f97; font-size: 11px; margin-top: 2px; }

.detail-runes {
    display: grid;
    grid-template-columns: 1.4fr 1fr 1fr;
    gap: 14px;
    padding-top: 10px;
    border-top: 1px solid #141f2c;
}
.rune-group {
    display: flex;
    align-items: center;
    gap: 8px;
}
.rune-group .tree-icon {
    width: 26px; height: 26px;
}
.rune-group .runes {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
}
.rune-group .runes img {
    width: 26px; height: 26px;
    border-radius: 50%;
    background: #050d18;
    padding: 1px;
}
.rune-group .runes img.keystone {
    width: 32px; height: 32px;
    border: 2px solid #c89b3c;
}
.rune-group.shards .runes img {
    width: 22px; height: 22px;
    border: 1px solid #2a3f5f;
}

/* 나머지 8명 요약 */
.detail-others {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
    margin-top: 8px;
}
.team-col {
    padding: 10px 12px;
    background: #0f1923;
    border-radius: 8px;
    border: 1px solid #1e2a3a;
}
.team-col .team-label {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-bottom: 8px;
}
.team-col.ally .team-label { color: #1abc9c; }
.team-col.enemy .team-label { color: #e74c3c; }
.team-row {
    display: grid;
    grid-template-columns: 28px 1fr auto;
    gap: 8px;
    align-items: center;
    padding: 4px 0;
    font-size: 12px;
    color: #cdbe91;
}
.team-row img {
    width: 26px; height: 26px;
    border-radius: 50%;
    border: 1px solid #2a3f5f;
}
.team-row .who { color: #cdbe91; }
.team-row .who .nick { color: #f0e6d2; font-weight: 500; }
.team-row .kda { color: #8a8f97; font-weight: 600; font-variant-numeric: tabular-nums; }

/* expander 톤 */
[data-testid="stExpander"] {
    background: transparent;
    border: 1px solid #141f2c;
    border-radius: 10px;
    margin-bottom: 14px;
}
[data-testid="stExpander"] summary {
    color: #cdbe91 !important;
    font-size: 12px;
}
</style>
"""


def champion_icon_url(version: str, champion_key: str) -> str:
    """Data Dragon 챔피언 정사각형 아이콘 URL."""
    return (
        f"https://ddragon.leagueoflegends.com/cdn/{version}"
        f"/img/champion/{champion_key}.png"
    )


def _kda_ratio(k: int, d: int, a: int) -> str:
    """KDA 비율을 'X.XX' 또는 '∞' 로 문자열화한다."""
    if d == 0:
        return "Perfect" if (k + a) > 0 else "0.00"
    return f"{(k + a) / d:.2f}"


def _format_gold(gold: int) -> str:
    if gold >= 1000:
        return f"{gold / 1000:.1f}k"
    return str(gold)


def _items_html(version: str, items: list[int]) -> str:
    """아이템 6칸 + 갭 + 트링킷 1칸을 HTML 로 생성한다."""
    # Match-V5 의 item0..item6 중 item6 은 보통 트링킷이다.
    main_items = items[:6]
    trinket = items[6] if len(items) > 6 else 0
    slots: list[str] = []
    for i in main_items:
        url = item_icon_url(version, i)
        if url:
            slots.append(f'<div class="detail-item"><img src="{url}" /></div>')
        else:
            slots.append('<div class="detail-item"></div>')
    slots.append('<div class="gap"></div>')
    trinket_url = item_icon_url(version, trinket)
    if trinket_url:
        slots.append(f'<div class="detail-item"><img src="{trinket_url}" /></div>')
    else:
        slots.append('<div class="detail-item"></div>')
    return "".join(slots)


def _runes_html(player: dict[str, Any], static_data: StaticData) -> str:
    """주룬·보조룬·스탯샤드를 한 줄로 표시하는 HTML 을 만든다."""
    primary_tree_id = player.get("primary_tree_id")
    secondary_tree_id = player.get("secondary_tree_id")
    primary_runes = player.get("primary_runes") or []
    secondary_runes = player.get("secondary_runes") or []
    shards = [
        player.get("stat_offense"),
        player.get("stat_flex"),
        player.get("stat_defense"),
    ]

    def _img(url: str | None, alt: str, cls: str = "") -> str:
        if not url:
            return f'<div class="detail-item" title="{alt}"></div>'
        return f'<img class="{cls}" src="{url}" alt="{alt}" title="{alt}" />'

    primary_imgs = []
    for idx, rune_id in enumerate(primary_runes):
        url = static_data.rune_icon_url(rune_id)
        name = static_data.rune_name(rune_id)
        cls = "keystone" if idx == 0 else ""
        primary_imgs.append(_img(url, name, cls))
    secondary_imgs = [
        _img(static_data.rune_icon_url(rid), static_data.rune_name(rid))
        for rid in secondary_runes
    ]
    shard_imgs = [
        _img(stat_shard_icon_url(sid), str(sid) if sid else "") for sid in shards
    ]

    primary_tree_icon = static_data.tree_icon_url(primary_tree_id)
    secondary_tree_icon = static_data.tree_icon_url(secondary_tree_id)

    return f"""
<div class="detail-runes">
  <div class="rune-group">
    {_img(primary_tree_icon, static_data.tree_name(primary_tree_id), "tree-icon")}
    <div class="runes">{"".join(primary_imgs)}</div>
  </div>
  <div class="rune-group">
    {_img(secondary_tree_icon, static_data.tree_name(secondary_tree_id), "tree-icon")}
    <div class="runes">{"".join(secondary_imgs)}</div>
  </div>
  <div class="rune-group shards">
    <div class="runes">{"".join(shard_imgs)}</div>
  </div>
</div>
"""


def _render_player_block(
    player: dict[str, Any],
    champion_data: ChampionData,
    static_data: StaticData,
) -> str:
    """라이너 한 명의 상세 블록 HTML."""
    version = static_data.version or champion_data.version
    ko_name = champion_data.to_korean_name(player.get("champion_key", ""))
    champ_url = champion_icon_url(version, player.get("champion_key", ""))
    lv = player.get("champion_level") or 0

    spell1 = static_data.summoner_icon_url(player.get("summoner1_id"))
    spell2 = static_data.summoner_icon_url(player.get("summoner2_id"))
    spell1_name = static_data.summoner_name(player.get("summoner1_id"))
    spell2_name = static_data.summoner_name(player.get("summoner2_id"))

    items_html = _items_html(version, player.get("items") or [])
    runes_html = _runes_html(player, static_data)

    k = player.get("kills", 0)
    d = player.get("deaths", 0)
    a = player.get("assists", 0)
    cs = player.get("cs", 0)
    gold = player.get("gold", 0)
    damage = player.get("damage", 0)

    name = player.get("riot_id_game_name") or player.get("summoner_name") or "—"
    tag = player.get("riot_id_tag_line") or ""
    result_cls = "win" if player.get("win") else "loss"

    def _spell(url: str | None, name: str) -> str:
        if not url:
            return '<div class="detail-item"></div>'
        return f'<img src="{url}" alt="{name}" title="{name}" />'

    return f"""
<div class="detail-player {result_cls}">
  <div class="detail-head">
    <div class="detail-champ">
      <img src="{champ_url}" alt="{ko_name}" />
      <div class="lv">Lv {lv}</div>
    </div>
    <div class="detail-spells">
      {_spell(spell1, spell1_name)}
      {_spell(spell2, spell2_name)}
    </div>
    <div class="detail-items">{items_html}</div>
    <div class="detail-stats">
      <div class="name">{name}<span class="tag">#{tag}</span></div>
      <div class="kda">{k}/{d}/{a}<span class="ratio">{_kda_ratio(k, d, a)} KDA</span></div>
      <div class="extras">{ko_name} · CS {cs} · {_format_gold(gold)} gold · {_format_gold(damage)} dmg</div>
    </div>
  </div>
  {runes_html}
</div>
"""


def _render_team_summary(
    others_ally: list[dict[str, Any]],
    others_enemy: list[dict[str, Any]],
    champion_data: ChampionData,
    version: str,
) -> str:
    """나머지 8명 요약 블록 HTML."""

    def _row(p: dict[str, Any]) -> str:
        ko_name = champion_data.to_korean_name(p.get("champion_key", ""))
        champ_url = champion_icon_url(version, p.get("champion_key", ""))
        nick = p.get("riot_id_game_name") or p.get("summoner_name") or "—"
        tag = p.get("riot_id_tag_line") or ""
        k = p.get("kills", 0)
        d = p.get("deaths", 0)
        a = p.get("assists", 0)
        return f"""
<div class="team-row">
  <img src="{champ_url}" alt="{ko_name}" title="{ko_name}" />
  <div class="who"><span class="nick">{nick}</span><span style="color:#6c7079">#{tag}</span></div>
  <div class="kda">{k}/{d}/{a}</div>
</div>
"""

    ally_rows = "".join(_row(p) for p in others_ally)
    enemy_rows = "".join(_row(p) for p in others_enemy)
    return f"""
<div class="detail-others">
  <div class="team-col ally">
    <div class="team-label">아군 (나 제외)</div>
    {ally_rows}
  </div>
  <div class="team-col enemy">
    <div class="team-label">적군 (상대 라이너 제외)</div>
    {enemy_rows}
  </div>
</div>
"""


def _render_match_detail(
    focus: dict[str, Any],
    champion_data: ChampionData,
    static_data: StaticData,
) -> str:
    """매치 상세 패널 전체 HTML 을 만든다."""
    me_html = _render_player_block(focus["me"], champion_data, static_data)
    enemy = focus.get("enemy_laner")
    enemy_html = (
        _render_player_block(enemy, champion_data, static_data)
        if enemy
        else '<div class="detail-player loss"><i>상대 라이너 정보를 찾지 못했습니다.</i></div>'
    )
    others_html = _render_team_summary(
        focus.get("others_ally") or [],
        focus.get("others_enemy") or [],
        champion_data,
        static_data.version,
    )
    return f"""
<div class="detail-panel">
  <div class="detail-section-title">맞라이너 상세</div>
  {me_html}
  {enemy_html}
  <div class="detail-section-title">나머지 8명</div>
  {others_html}
</div>
"""


# ---- 환경 설정 로딩 ----
def load_env() -> dict[str, str]:
    load_dotenv()
    return {
        "api_key": os.environ.get("RIOT_API_KEY", "").strip(),
        "region": os.environ.get("RIOT_REGION", "asia").strip() or "asia",
        "platform": os.environ.get("RIOT_PLATFORM", "kr").strip() or "kr",
        "queue_id": int(os.environ.get("DEFAULT_QUEUE_ID", "420")),
        "db_path": os.environ.get("CACHE_DB_PATH", "data/matchup_finder.db"),
    }


# ---- 캐시된 리소스 ----
@st.cache_resource(show_spinner=False)
def get_match_cache(db_path: str) -> MatchCache:
    return MatchCache(db_path)


@st.cache_resource(show_spinner=False)
def get_champion_repo(db_path: str) -> ChampionRepository:
    return ChampionRepository(db_path)


@st.cache_data(show_spinner="챔피언 목록을 불러오는 중...", ttl=60 * 60)
def load_champion_data(db_path: str, force_refresh: bool = False) -> ChampionData:
    repo = get_champion_repo(db_path)
    return repo.load(force_refresh=force_refresh)


@st.cache_resource(show_spinner=False)
def get_static_data_repo(db_path: str) -> StaticDataRepository:
    return StaticDataRepository(db_path)


@st.cache_data(show_spinner="룬·소환사 주문 메타데이터를 불러오는 중...", ttl=60 * 60)
def load_static_data(db_path: str, force_refresh: bool = False) -> StaticData:
    repo = get_static_data_repo(db_path)
    return repo.load(force_refresh=force_refresh)


# ---- PUUID 조회 (캐시 우선) ----
def resolve_puuid(
    client: RiotClient,
    cache: MatchCache,
    game_name: str,
    tag_line: str,
) -> dict[str, Any]:
    cache_key = normalize_riot_id_key(game_name, tag_line)
    cached = cache.get_account(cache_key)
    if cached is not None:
        return {
            "puuid": cached["puuid"],
            "game_name": cached["game_name"] or game_name,
            "tag_line": cached["tag_line"] or tag_line,
        }

    account = client.get_account_by_riot_id(game_name, tag_line)
    puuid = account.get("puuid")
    if not puuid:
        raise RiotApiError("Account API 응답에 puuid가 없습니다.")

    cache.save_account(
        cache_key,
        puuid=puuid,
        game_name=account.get("gameName") or game_name,
        tag_line=account.get("tagLine") or tag_line,
    )
    return {
        "puuid": puuid,
        "game_name": account.get("gameName") or game_name,
        "tag_line": account.get("tagLine") or tag_line,
    }


# ---- matchId 페이지네이션 ----
def fetch_all_match_ids(
    client: RiotClient,
    puuid: str,
    queue_id: int,
    start_ts: int,
    end_ts: int,
    max_total: int,
) -> list[str]:
    collected: list[str] = []
    seen: set[str] = set()

    for page_index in range(MAX_PAGES):
        if len(collected) >= max_total:
            break
        remaining = max_total - len(collected)
        page_count = min(PAGE_SIZE, remaining)
        ids = client.get_match_ids(
            puuid=puuid,
            queue_id=queue_id,
            start_time=start_ts,
            end_time=end_ts,
            start=page_index * PAGE_SIZE,
            count=page_count,
        )
        if not ids:
            break
        new_ids = [m for m in ids if m not in seen]
        for m in new_ids:
            seen.add(m)
        collected.extend(new_ids)
        if len(ids) < page_count:
            break

    return collected[:max_total]


# ---- 단일 매치 상세 (캐시 우선) ----
def fetch_match_detail(
    client: RiotClient,
    cache: MatchCache,
    match_id: str,
) -> dict[str, Any] | None:
    cached = cache.get_match(match_id)
    if cached is not None:
        return cached

    try:
        match = client.get_match_by_id(match_id)
    except RiotApiNotFound:
        return None

    cache.save_match(match_id, match)
    return match


# ---- 검색 본체 ----
def run_search(
    *,
    cfg: dict[str, Any],
    riot_id_raw: str,
    period_kind: str,
    days: int,
    custom_start: date,
    custom_end: date,
    my_champion_korean: str,
    enemy_champion_korean: str,
    lane_label: str,
    max_matches: int,
    champion_data: ChampionData,
    progress_cb,
    status_cb,
) -> dict[str, Any]:
    if not cfg["api_key"]:
        raise RiotApiError(
            "RIOT_API_KEY가 설정되어 있지 않습니다. .env 파일에 Riot API Key를 넣어주세요."
        )

    game_name, tag_line = parse_riot_id(riot_id_raw)

    my_champion_key = champion_data.to_english_key(my_champion_korean)
    enemy_champion_key = champion_data.to_english_key(enemy_champion_korean)
    if not my_champion_key or not enemy_champion_key:
        raise RiotApiError(
            "선택한 챔피언을 인식하지 못했습니다. 목록을 새로고침해주세요."
        )

    lane_value = LANE_LABEL_TO_TEAM_POSITION[lane_label]

    if period_kind in PERIOD_PRESETS:
        start_ts, end_ts = days_ago_to_unix_range(PERIOD_PRESETS[period_kind])
    else:
        if custom_start > custom_end:
            raise RiotApiError("시작일이 종료일보다 늦을 수 없습니다.")
        from datetime import datetime

        start_ts, end_ts = date_range_to_unix(
            datetime.combine(custom_start, datetime.min.time()),
            datetime.combine(custom_end, datetime.min.time()),
        )

    cache = get_match_cache(cfg["db_path"])

    status_cb("Riot ID로 PUUID를 조회 중...")
    with RiotClient(api_key=cfg["api_key"], region=cfg["region"]) as client:
        account = resolve_puuid(client, cache, game_name, tag_line)
        puuid = account["puuid"]

        status_cb("매치 ID 목록을 불러오는 중...")
        match_ids = fetch_all_match_ids(
            client=client,
            puuid=puuid,
            queue_id=cfg["queue_id"],
            start_ts=start_ts,
            end_ts=end_ts,
            max_total=max_matches,
        )

        total = len(match_ids)
        results: list[dict[str, Any]] = []
        cache_hits = 0
        api_calls = 0

        if total == 0:
            status_cb("이 기간에는 매치가 없습니다.")
            progress_cb(1.0)
        else:
            for idx, match_id in enumerate(match_ids, start=1):
                status_cb(f"매치 분석 중... ({idx}/{total})")

                # 캐시 히트 카운트는 fetch 직전에 확인한다.
                cached_before = cache.get_match(match_id) is not None
                match = fetch_match_detail(client, cache, match_id)
                if match is None:
                    progress_cb(idx / total)
                    continue

                if cached_before:
                    cache_hits += 1
                else:
                    api_calls += 1
                    # 같은 1초에 너무 많이 부르지 않도록 가벼운 페이싱.
                    time.sleep(0.05)

                row = extract_matchup_result(
                    match=match,
                    target_puuid=puuid,
                    my_champion_key=my_champion_key,
                    enemy_champion_key=enemy_champion_key,
                    lane=lane_value,
                )
                if row is not None:
                    results.append(row)

                progress_cb(idx / total)

    # 최신순 정렬
    results.sort(key=lambda r: r.get("game_creation") or 0, reverse=True)

    cache.record_search(
        target_riot_id=f"{account['game_name']}#{account['tag_line']}",
        target_puuid=puuid,
        queue_id=cfg["queue_id"],
        my_champion=my_champion_key,
        enemy_champion=enemy_champion_key,
        lane=lane_value,
        start_time=start_ts,
        end_time=end_ts,
        result_count=len(results),
    )

    return {
        "results": results,
        "scanned_total": total,
        "cache_hits": cache_hits,
        "api_calls": api_calls,
        "account": account,
        "my_champion_key": my_champion_key,
        "enemy_champion_key": enemy_champion_key,
        "lane_label": lane_label,
        "period_kind": period_kind,
        "start_ts": start_ts,
        "end_ts": end_ts,
    }


# ---- 결과 표시 ----
def render_results(
    payload: dict[str, Any],
    champion_data: ChampionData,
    cache: MatchCache,
    static_data: StaticData,
) -> None:
    results = payload["results"]
    account = payload["account"]
    scanned_total = payload["scanned_total"]
    cache_hits = payload["cache_hits"]
    api_calls = payload["api_calls"]

    my_ko = champion_data.to_korean_name(payload["my_champion_key"])
    enemy_ko = champion_data.to_korean_name(payload["enemy_champion_key"])

    # 검색 조건 요약 (한 줄 카드)
    st.markdown(
        f"""
<div class="search-summary">
  <div>🎯 검색 대상: <b>{account["game_name"]}#{account["tag_line"]}</b></div>
  <div>👤 내 챔피언: <b>{my_ko}</b></div>
  <div>⚔️ 상대 챔피언: <b>{enemy_ko}</b></div>
  <div>📍 라인: <b>{payload["lane_label"]}</b></div>
  <div>📅 기간: <b>{payload["period_kind"]}</b></div>
</div>
        """,
        unsafe_allow_html=True,
    )

    # KPI 띠
    total = len(results)
    wins = sum(1 for r in results if r["win"])
    losses = total - wins
    winrate = (wins / total * 100) if total > 0 else 0.0
    winrate_text = f"{winrate:.1f}%" if total > 0 else "—"

    st.markdown(
        f"""
<div class="kpi-row">
  <div class="kpi-card winrate">
    <div class="label">승률</div>
    <div class="value">{winrate_text}</div>
    <div class="sub">매칭된 경기 {total}판 기준</div>
  </div>
  <div class="kpi-card win">
    <div class="label">승</div>
    <div class="value">{wins}</div>
  </div>
  <div class="kpi-card loss">
    <div class="label">패</div>
    <div class="value">{losses}</div>
  </div>
  <div class="kpi-card">
    <div class="label">스캔한 매치</div>
    <div class="value">{scanned_total}</div>
  </div>
  <div class="kpi-card">
    <div class="label">캐시 / API</div>
    <div class="value">{cache_hits} / {api_calls}</div>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )

    if not results:
        st.markdown(
            """
<div class="empty-card">
  <div class="title">😅 조건에 맞는 경기를 찾지 못했어요</div>
  <ul>
    <li>검색 기간을 더 길게 잡아 보세요.</li>
    <li>내 챔피언 / 상대 챔피언 선택이 맞는지 확인해 보세요.</li>
    <li>해당 라인이 아니라 다른 라인으로 기록된 경기일 수 있습니다.</li>
  </ul>
</div>
            """,
            unsafe_allow_html=True,
        )
        return

    # 검색 대상 Riot ID의 OP.GG 링크 (큰 버튼)
    my_opgg_url = build_opgg_url(account["game_name"], account["tag_line"])
    st.markdown(
        f"""
<a class="target-opgg" href="{my_opgg_url}" target="_blank">
  🔗 OP.GG 열기 — {account["game_name"]}#{account["tag_line"]}
  <span class="arrow">↗</span>
</a>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="section-title">결과 목록</div>', unsafe_allow_html=True)

    version = champion_data.version
    for r in results:
        result_cls = "win" if r["win"] else "loss"
        result_text = "승리" if r["win"] else "패배"

        date_text = (
            unix_to_kst_datetime_str(r["game_creation"])
            if r.get("game_creation")
            else r.get("game_date") or ""
        )
        # 'YYYY-MM-DD HH:MM' → 날짜와 시간으로 분리
        if " " in date_text:
            day_part, time_part = date_text.split(" ", 1)
        else:
            day_part, time_part = date_text, ""

        my_key = r["my_champion_key"]
        enemy_key = r["enemy_champion_key"]
        my_ko_name = champion_data.to_korean_name(my_key)
        enemy_ko_name = champion_data.to_korean_name(enemy_key)
        my_icon = champion_icon_url(version, my_key)
        enemy_icon = champion_icon_url(version, enemy_key)

        kda = f"{r['kills']}/{r['deaths']}/{r['assists']}"
        duration_min = max(int(r.get("game_duration", 0) // 60), 0)
        cs_text = f"CS {r['cs']} · {duration_min}분"

        enemy_name_only = (
            r.get("enemy_game_name") or r["enemy_riot_id"].split("#", 1)[0]
        )
        enemy_tag = r.get("enemy_tag_line") or (
            r["enemy_riot_id"].split("#", 1)[1] if "#" in r["enemy_riot_id"] else ""
        )

        # 카드 + 우측 복사 버튼 (st.code 의 내장 복사 기능 사용)
        row_cols = st.columns([6, 1.2])
        with row_cols[0]:
            st.markdown(
                f"""
<div class="match-card {result_cls}">
  <div class="match-date">
    <div class="day">{day_part}</div>
    <div class="time">{time_part}</div>
  </div>
  <div class="match-champs">
    <div class="champ ally">
      <img src="{my_icon}" alt="{my_ko_name}" />
      <div class="name">{my_ko_name}</div>
    </div>
    <div class="vs">VS</div>
    <div class="champ enemy">
      <img src="{enemy_icon}" alt="{enemy_ko_name}" />
      <div class="name">{enemy_ko_name}</div>
    </div>
  </div>
  <div><span class="result-pill {result_cls}">{result_text}</span></div>
  <div class="match-stats">
    <div class="kda">{kda}</div>
    <div class="cs">{cs_text}</div>
  </div>
  <div class="match-enemy">
    <div class="name">{enemy_name_only}</div>
    <div class="tag">#{enemy_tag}</div>
  </div>
</div>
                """,
                unsafe_allow_html=True,
            )
        with row_cols[1]:
            st.markdown('<div class="copy-wrap">', unsafe_allow_html=True)
            st.caption("닉네임 복사")
            st.code(enemy_name_only, language=None)
            st.markdown("</div>", unsafe_allow_html=True)

        # 매치 상세 패널 (캐시에서 매치 JSON 을 불러와 Focus 뷰 렌더)
        with st.expander("📊 이 매치 상세 보기", expanded=False):
            match_full = cache.get_match(r["match_id"]) if r.get("match_id") else None
            focus = (
                extract_focus_view(match_full, account["puuid"]) if match_full else None
            )
            if focus is not None:
                st.markdown(
                    _render_match_detail(focus, champion_data, static_data),
                    unsafe_allow_html=True,
                )
            else:
                st.caption("이 매치의 상세 데이터를 찾을 수 없습니다.")

    # CSV 다운로드 (계획서의 컬럼 형식을 따른다)
    csv_rows = []
    for r in results:
        csv_rows.append(
            {
                "game_date": r["game_date"],
                "my_champion": r["my_champion_key"],
                "enemy_champion": r["enemy_champion_key"],
                "enemy_riot_id": r["enemy_riot_id"],
                "result": "win" if r["win"] else "loss",
                "match_id": r["match_id"] or "",
            }
        )
    csv_df = pd.DataFrame(csv_rows)
    csv_buffer = io.StringIO()
    csv_df.to_csv(csv_buffer, index=False, encoding="utf-8")
    st.download_button(
        label="📥 CSV 다운로드",
        data=csv_buffer.getvalue().encode("utf-8-sig"),
        file_name=(
            f"matchup_{payload['my_champion_key']}_vs_"
            f"{payload['enemy_champion_key']}.csv"
        ),
        mime="text/csv",
    )


# ---- 메인 ----
def main() -> None:
    st.set_page_config(
        page_title="LoL 매치업 상대 닉네임 추출기",
        page_icon="🎯",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    cfg = load_env()

    # 사이드바: 환경/캐시 정보 (접혀 있는 상태가 기본)
    with st.sidebar:
        st.markdown("### ⚙️ 환경 설정")
        if cfg["api_key"]:
            st.success("✓ RIOT_API_KEY 감지됨")
        else:
            st.error(
                "✗ RIOT_API_KEY 미설정\n\n"
                ".env 파일에 키를 추가한 뒤 앱을 다시 실행하세요."
            )

        st.caption(f"리전: `{cfg['region']}` · 플랫폼: `{cfg['platform']}`")
        st.caption(f"큐 ID: `{cfg['queue_id']}` (솔로 랭크)")
        st.caption(f"DB: `{cfg['db_path']}`")

        st.divider()
        if st.button("챔피언 목록 새로고침", use_container_width=True):
            load_champion_data.clear()
            try:
                champion_data = load_champion_data(cfg["db_path"], force_refresh=True)
                st.success(f"갱신 완료 · v{champion_data.version}")
            except Exception as exc:
                st.error(f"갱신 실패: {exc}")
        if st.button("룬·주문 메타 새로고침", use_container_width=True):
            load_static_data.clear()
            try:
                static_data = load_static_data(cfg["db_path"], force_refresh=True)
                st.success(f"갱신 완료 · v{static_data.version}")
            except Exception as exc:
                st.error(f"갱신 실패: {exc}")

    # 챔피언 목록 / 룬·소환사 주문 메타데이터 로딩
    try:
        champion_data = load_champion_data(cfg["db_path"])
    except Exception as exc:
        st.error(f"챔피언 목록을 불러올 수 없습니다: {exc}")
        return
    try:
        static_data = load_static_data(cfg["db_path"])
    except Exception as exc:
        st.error(f"룬·소환사 주문 메타데이터를 불러올 수 없습니다: {exc}")
        return

    # 히어로
    st.markdown(
        f"""
<div class="matchup-hero">
  <h1>🎯 LoL 매치업 상대 닉네임 추출기</h1>
  <div class="sub">
    내가 X로 플레이한 매치 중 상대 라이너가 Y였던 경기만 골라
    상대 Riot ID 목록을 한눈에 보여줍니다.
  </div>
  <div class="meta">
    <span class="hero-chip">🇰🇷 한국 서버</span>
    <span class="hero-chip">🏆 솔로 랭크</span>
    <span class="hero-chip">📦 Data Dragon v{champion_data.version}</span>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )

    # 기본값
    default_my = (
        "아리"
        if "아리" in champion_data.korean_names
        else champion_data.korean_names[0]
    )
    default_enemy = (
        "사일러스"
        if "사일러스" in champion_data.korean_names
        else champion_data.korean_names[0]
    )

    st.markdown('<div class="section-title">검색 조건</div>', unsafe_allow_html=True)

    # 1) 검색할 Riot ID
    riot_id_raw = st.text_input(
        "검색할 Riot ID",
        placeholder="예: Hide on bush#KR1",
        key="riot_id_input",
    )

    # 2) 챔피언 + 라인 (반응형으로 프리뷰 즉시 갱신)
    c_my, c_vs, c_enemy, c_lane = st.columns([3, 0.5, 3, 2])
    with c_my:
        my_champion_korean = st.selectbox(
            "내 챔피언",
            champion_data.korean_names,
            index=champion_data.korean_names.index(default_my),
        )
    with c_vs:
        st.markdown(
            '<div style="text-align:center; padding-top:34px; color:#c89b3c;'
            ' font-weight:800; font-size:18px; letter-spacing:2px;">VS</div>',
            unsafe_allow_html=True,
        )
    with c_enemy:
        enemy_champion_korean = st.selectbox(
            "상대 챔피언",
            champion_data.korean_names,
            index=champion_data.korean_names.index(default_enemy),
        )
    with c_lane:
        lane_label = st.selectbox(
            "라인",
            LANE_LABELS,
            index=LANE_LABELS.index("미드"),
        )

    # 3) 챔피언 프리뷰 (선택 즉시 아이콘 표시)
    my_key_preview = champion_data.to_english_key(my_champion_korean)
    enemy_key_preview = champion_data.to_english_key(enemy_champion_korean)
    if my_key_preview and enemy_key_preview:
        st.markdown(
            f"""
<div class="preview-row">
  <div class="preview-champ ally">
    <img src="{champion_icon_url(champion_data.version, my_key_preview)}"
         alt="{my_champion_korean}" />
    <div class="name">{my_champion_korean}</div>
  </div>
  <div class="preview-vs">VS</div>
  <div class="preview-champ enemy">
    <img src="{champion_icon_url(champion_data.version, enemy_key_preview)}"
         alt="{enemy_champion_korean}" />
    <div class="name">{enemy_champion_korean}</div>
  </div>
</div>
            """,
            unsafe_allow_html=True,
        )

    # 4) 기간 + 매치 수
    p_period, p_max = st.columns([2, 1])
    with p_period:
        period_kind = st.selectbox(
            "검색 기간",
            PERIOD_OPTIONS,
            index=PERIOD_OPTIONS.index(DEFAULT_PERIOD_LABEL),
        )
    with p_max:
        max_matches = st.selectbox(
            "최대 검색 매치 수",
            (50, 100, 200, 300),
            index=1,
            help="이 기간 내 솔로 랭크 매치 중 최근 N개를 분석합니다.",
        )

    # 5) 직접 지정일 때만 날짜 입력 표시
    today = date.today()
    default_start = today - timedelta(days=90)
    if period_kind == PERIOD_CUSTOM_LABEL:
        d_start, d_end = st.columns(2)
        with d_start:
            custom_start = st.date_input("시작일", value=default_start)
        with d_end:
            custom_end = st.date_input("종료일", value=today)
    else:
        custom_start = default_start
        custom_end = today

    # 6) 검색 버튼
    st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
    submitted = st.button(
        "🔍 검색 실행",
        type="primary",
        use_container_width=True,
    )

    if submitted:
        progress_bar = st.progress(0.0)
        status_box = st.empty()

        def progress_cb(value: float) -> None:
            progress_bar.progress(min(max(value, 0.0), 1.0))

        def status_cb(message: str) -> None:
            status_box.info(message)

        try:
            payload = run_search(
                cfg=cfg,
                riot_id_raw=riot_id_raw,
                period_kind=period_kind,
                days=0,
                custom_start=custom_start,
                custom_end=custom_end,
                my_champion_korean=my_champion_korean,
                enemy_champion_korean=enemy_champion_korean,
                lane_label=lane_label,
                max_matches=int(max_matches),
                champion_data=champion_data,
                progress_cb=progress_cb,
                status_cb=status_cb,
            )
        except RiotIdParseError as exc:
            status_box.empty()
            progress_bar.empty()
            st.error(str(exc))
            return
        except RiotApiAuthError as exc:
            status_box.empty()
            progress_bar.empty()
            st.error(str(exc))
            return
        except RiotApiNotFound as exc:
            status_box.empty()
            progress_bar.empty()
            st.error(str(exc))
            return
        except RiotApiRateLimited as exc:
            status_box.empty()
            progress_bar.empty()
            st.error(
                str(exc)
                + "\n이미 조회한 경기는 캐시에서 재사용되므로 다시 시도하면 더 빠릅니다."
            )
            return
        except RiotApiError as exc:
            status_box.empty()
            progress_bar.empty()
            st.error(str(exc))
            return

        status_box.empty()
        progress_bar.empty()
        st.session_state["last_payload"] = payload

    payload = st.session_state.get("last_payload")
    if payload is not None:
        cache = get_match_cache(cfg["db_path"])
        render_results(payload, champion_data, cache, static_data)


if __name__ == "__main__":
    main()
