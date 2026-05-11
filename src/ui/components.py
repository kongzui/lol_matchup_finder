"""Streamlit UI 반복 컴포넌트."""

from __future__ import annotations

import json
from html import escape
from typing import Any

import streamlit as st
from streamlit.components.v1 import html as components_html

from src.cache import MatchCache
from src.champions import ChampionData, champion_icon_url
from src.export import build_results_csv_bytes, build_results_filename
from src.matchup_filter import extract_focus_view
from src.opgg import build_opgg_url
from src.search_service import SearchPayload
from src.static_data import StaticData
from src.utils import unix_to_kst_datetime_str
from src.ui.match_detail import render_match_detail


def _h(value: Any) -> str:
    return escape(str(value or ""), quote=True)


def _js(value: Any) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def _split_riot_id(row: dict[str, Any]) -> tuple[str, str]:
    name = row.get("enemy_game_name") or row.get("enemy_riot_id", "").split("#", 1)[0]
    tag = row.get("enemy_tag_line") or (
        row["enemy_riot_id"].split("#", 1)[1]
        if "#" in row.get("enemy_riot_id", "")
        else ""
    )
    return name, tag


def render_section_title(title: str) -> None:
    """섹션 제목을 그린다."""
    st.markdown(f'<div class="section-title">{_h(title)}</div>', unsafe_allow_html=True)


def render_matchup_header(
    *,
    riot_id_raw: str,
    lane_label: str,
    period_kind: str,
    max_matches: int,
    my_champion_korean: str,
    enemy_champion_korean: str,
    my_champion_key: str | None,
    enemy_champion_key: str | None,
    champion_version: str,
) -> None:
    """검색 조건을 OP.GG 전적 헤더처럼 보여준다."""
    target = riot_id_raw.strip() or "검색할 Riot ID"
    my_icon = (
        champion_icon_url(champion_version, my_champion_key) if my_champion_key else ""
    )
    enemy_icon = (
        champion_icon_url(champion_version, enemy_champion_key)
        if enemy_champion_key
        else ""
    )

    st.markdown(
        f"""
<section class="matchup-header">
  <div class="target-block">
    <div class="eyebrow">MATCHUP FINDER</div>
    <div class="target-name">{_h(target)}</div>
    <div class="target-meta">
      <span>한국 서버</span><span>솔로 랭크</span><span>{_h(lane_label)}</span>
      <span>{_h(period_kind)}</span><span>최대 {int(max_matches)}판</span>
    </div>
  </div>
  <div class="versus-block">
    <div class="champ-side ally">
      <img src="{_h(my_icon)}" alt="{_h(my_champion_korean)}" />
      <div>
        <span>내 챔피언</span>
        <strong>{_h(my_champion_korean)}</strong>
      </div>
    </div>
    <div class="vs-mark">VS</div>
    <div class="champ-side enemy">
      <img src="{_h(enemy_icon)}" alt="{_h(enemy_champion_korean)}" />
      <div>
        <span>상대 챔피언</span>
        <strong>{_h(enemy_champion_korean)}</strong>
      </div>
    </div>
  </div>
</section>
        """,
        unsafe_allow_html=True,
    )


def render_result_summary(payload: SearchPayload, champion_data: ChampionData) -> None:
    """검색 결과 요약과 KPI를 표시한다."""
    results = payload.results
    wins = sum(1 for row in results if row["win"])
    total = len(results)
    losses = total - wins
    winrate = f"{(wins / total * 100):.1f}%" if total else "-"
    my_ko = champion_data.to_korean_name(payload.my_champion_key)
    enemy_ko = champion_data.to_korean_name(payload.enemy_champion_key)
    account = payload.account

    st.markdown(
        f"""
<section class="result-summary">
  <div class="summary-main">
    <span>검색 결과</span>
    <strong>{_h(account["game_name"])}#{_h(account["tag_line"])}</strong>
    <p>{_h(my_ko)} vs {_h(enemy_ko)} · {_h(payload.lane_label)} · {_h(payload.period_kind)}</p>
  </div>
  <div class="kpi-row">
    <div class="kpi-card primary"><span>발견</span><strong>{total}</strong><em>경기</em></div>
    <div class="kpi-card"><span>승률</span><strong>{winrate}</strong><em>{wins}승 {losses}패</em></div>
    <div class="kpi-card"><span>스캔</span><strong>{payload.scanned_total}</strong><em>매치</em></div>
    <div class="kpi-card"><span>캐시 / API</span><strong>{payload.cache_hits} / {payload.api_calls}</strong><em>호출 절약</em></div>
  </div>
</section>
        """,
        unsafe_allow_html=True,
    )


def render_empty_state() -> None:
    """조건에 맞는 결과가 없을 때 안내를 표시한다."""
    st.markdown(
        """
<div class="empty-card">
  <strong>조건에 맞는 경기를 찾지 못했습니다.</strong>
  <p>검색 기간을 늘리거나 챔피언/라인 선택이 맞는지 확인해 주세요.</p>
</div>
        """,
        unsafe_allow_html=True,
    )


def render_result_card(
    row: dict[str, Any],
    champion_data: ChampionData,
) -> str:
    """복사 버튼이 포함된 결과 카드 HTML을 만든다."""
    result_cls = "win" if row["win"] else "loss"
    result_text = "승리" if row["win"] else "패배"

    date_text = (
        unix_to_kst_datetime_str(row["game_creation"])
        if row.get("game_creation")
        else row.get("game_date") or ""
    )
    if " " in date_text:
        day_part, time_part = date_text.split(" ", 1)
    else:
        day_part, time_part = date_text, ""

    my_key = row["my_champion_key"]
    enemy_key = row["enemy_champion_key"]
    my_ko = champion_data.to_korean_name(my_key)
    enemy_ko = champion_data.to_korean_name(enemy_key)
    my_icon = champion_icon_url(champion_data.version, my_key)
    enemy_icon = champion_icon_url(champion_data.version, enemy_key)

    kills = row["kills"]
    deaths = row["deaths"]
    assists = row["assists"]
    duration_min = max(int(row.get("game_duration", 0) // 60), 0)
    enemy_name, enemy_tag = _split_riot_id(row)
    enemy_riot_id = f"{enemy_name}#{enemy_tag}" if enemy_tag else enemy_name
    enemy_opgg_url = build_opgg_url(enemy_name, enemy_tag) if enemy_tag else ""

    return f"""
<style>
body {{
    margin: 0;
    background: transparent;
    font-family: "Source Sans Pro", sans-serif;
}}
.match-row {{
    box-sizing: border-box;
    display: grid;
    grid-template-columns: 94px minmax(220px, 1.45fr) 72px minmax(92px, 0.65fr) minmax(190px, 1fr) 86px;
    gap: 12px;
    align-items: center;
    min-height: 86px;
    padding: 12px 14px;
    border: 1px solid #252a35;
    border-left-width: 4px;
    border-radius: 8px;
    background: #11141b;
}}
.match-row.win {{ border-left-color: #20c997; }}
.match-row.loss {{ border-left-color: #ff6b6b; }}
.match-date strong,
.match-date span,
.score-block strong,
.score-block span,
.enemy-block strong,
.enemy-block span {{
    display: block;
}}
.match-date strong {{
    color: #e8ecf3;
    font-size: 12px;
}}
.match-date span {{
    margin-top: 3px;
    color: #7f899c;
    font-size: 11px;
}}
.matchup-mini {{
    display: grid;
    grid-template-columns: minmax(72px, 1fr) 28px minmax(72px, 1fr);
    gap: 8px;
    align-items: center;
}}
.matchup-mini div {{
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
}}
.matchup-mini img {{
    width: 38px;
    height: 38px;
    border-radius: 50%;
    border: 1px solid #303746;
}}
.matchup-mini span,
.enemy-block strong {{
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}}
.matchup-mini span {{
    color: #d9dee8;
    font-size: 12px;
    font-weight: 700;
}}
.matchup-mini b {{
    color: #737d90;
    font-size: 11px;
    text-align: center;
}}
.result-pill {{
    display: inline-flex;
    justify-content: center;
    align-items: center;
    min-height: 30px;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 850;
}}
.result-pill.win {{
    background: rgba(32, 201, 151, 0.12);
    color: #20c997;
}}
.result-pill.loss {{
    background: rgba(255, 107, 107, 0.12);
    color: #ff6b6b;
}}
.score-block strong {{
    color: #f1f4f8;
    font-size: 15px;
}}
.score-block span,
.enemy-block span {{
    margin-top: 4px;
    color: #8d96a8;
    font-size: 12px;
}}
.enemy-block {{
    min-width: 0;
}}
.enemy-block strong {{
    color: #f1f4f8;
    font-size: 15px;
}}
.row-actions {{
    display: flex;
    justify-content: flex-end;
    gap: 6px;
}}
.icon-action {{
    box-sizing: border-box;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 34px;
    height: 34px;
    border: 1px solid #303746;
    border-radius: 6px;
    background: #171c26;
    color: #dce1ea;
    font-size: 14px;
    font-weight: 800;
    line-height: 1;
    text-decoration: none;
    cursor: pointer;
}}
.icon-action:hover {{
    border-color: #20c997;
    color: #ffffff;
}}
@media (max-width: 760px) {{
    .match-row {{
        grid-template-columns: 1fr;
        min-height: auto;
    }}
    .row-actions {{
        justify-content: flex-start;
    }}
}}
</style>
<script>
async function copyRiotId(button) {{
    const value = {_js(enemy_riot_id)};
    try {{
        await navigator.clipboard.writeText(value);
        const oldText = button.textContent;
        button.textContent = "OK";
        window.setTimeout(() => button.textContent = oldText, 900);
    }} catch (error) {{
        const input = document.createElement("input");
        input.value = value;
        document.body.appendChild(input);
        input.select();
        document.execCommand("copy");
        input.remove();
    }}
}}
</script>
<div class="match-row {result_cls}">
  <div class="match-date">
    <strong>{_h(day_part)}</strong>
    <span>{_h(time_part)}</span>
  </div>
  <div class="matchup-mini">
    <div><img src="{_h(my_icon)}" alt="{_h(my_ko)}" /><span>{_h(my_ko)}</span></div>
    <b>VS</b>
    <div><img src="{_h(enemy_icon)}" alt="{_h(enemy_ko)}" /><span>{_h(enemy_ko)}</span></div>
  </div>
  <div class="result-pill {result_cls}">{result_text}</div>
  <div class="score-block">
    <strong>{kills}/{deaths}/{assists}</strong>
    <span>CS {int(row["cs"])} · {duration_min}분</span>
  </div>
  <div class="enemy-block">
    <strong>{_h(enemy_name)}</strong>
    <span>#{_h(enemy_tag)}</span>
  </div>
  <div class="row-actions">
    <button class="icon-action" type="button" title="Riot ID 복사" onclick="copyRiotId(this)">⧉</button>
    <a class="icon-action" href="{_h(enemy_opgg_url)}" target="_blank" title="OP.GG 열기">↗</a>
  </div>
</div>
"""


def render_results(
    payload: SearchPayload,
    champion_data: ChampionData,
    cache: MatchCache,
    static_data: StaticData,
) -> None:
    """결과 요약, 목록, 상세 패널, CSV 다운로드를 표시한다."""
    render_result_summary(payload, champion_data)

    account = payload.account
    target_opgg_url = build_opgg_url(account["game_name"], account["tag_line"])
    st.markdown(
        f"""
<a class="target-opgg" href="{_h(target_opgg_url)}" target="_blank">
  검색 유저 OP.GG 열기
</a>
        """,
        unsafe_allow_html=True,
    )

    if not payload.results:
        render_empty_state()
        return

    render_section_title("결과 목록")
    for row in payload.results:
        components_html(render_result_card(row, champion_data), height=92)
        with st.expander("매치 상세 보기", expanded=False):
            match_full = (
                cache.get_match(row["match_id"]) if row.get("match_id") else None
            )
            focus = (
                extract_focus_view(match_full, account["puuid"]) if match_full else None
            )
            if focus is not None:
                st.markdown(
                    render_match_detail(focus, champion_data, static_data),
                    unsafe_allow_html=True,
                )
            else:
                st.caption("이 매치의 상세 데이터를 찾을 수 없습니다.")

    st.download_button(
        label="CSV 다운로드",
        data=build_results_csv_bytes(payload),
        file_name=build_results_filename(payload),
        mime="text/csv",
        use_container_width=True,
    )
