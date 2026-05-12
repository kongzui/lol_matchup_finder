"""Streamlit UI 반복 컴포넌트."""

from __future__ import annotations

import json
from html import escape
from typing import Any

import streamlit as st

from src.cache import MatchCache
from src.challenger_service import ChallengerSearchPayload
from src.champions import ChampionData, champion_icon_url
from src.config import AppConfig
from src.export import (
    build_challenger_results_csv_bytes,
    build_challenger_results_filename,
    build_results_csv_bytes,
    build_results_filename,
)
from src.matchup_filter import extract_focus_view
from src.opgg import build_opgg_url
from src.riot_client import RiotApiError, RiotClient
from src.search_service import SearchPayload, fetch_match_timeline
from src.static_data import StaticData, item_icon_url
from src.timeline import extract_player_build_timeline
from src.ui.match_detail import render_match_detail
from src.utils import unix_to_kst_datetime_str


_MATCH_DETAIL_OPEN_STATE_KEY = "open_match_detail_ids"


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


def _split_player_riot_id(row: dict[str, Any]) -> tuple[str, str]:
    riot_id = str(row.get("player_riot_id") or "")
    name = row.get("player_game_name") or riot_id.split("#", 1)[0]
    tag = row.get("player_tag_line") or (
        riot_id.split("#", 1)[1] if "#" in riot_id else ""
    )
    return name, tag


def _toggle_match_detail(detail_id: str) -> None:
    """매치 상세 패널의 열림 상태를 토글한다."""
    open_ids = set(st.session_state.get(_MATCH_DETAIL_OPEN_STATE_KEY, []))
    if detail_id in open_ids:
        open_ids.remove(detail_id)
    else:
        open_ids.add(detail_id)
    st.session_state[_MATCH_DETAIL_OPEN_STATE_KEY] = sorted(open_ids)


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


def render_challenger_result_summary(
    payload: ChallengerSearchPayload,
    champion_data: ChampionData,
) -> None:
    """챌린저 검색 결과 요약과 KPI를 표시한다."""
    results = payload.results
    wins = sum(1 for row in results if row["win"])
    total = len(results)
    losses = total - wins
    winrate = f"{(wins / total * 100):.1f}%" if total else "-"
    my_ko = champion_data.to_korean_name(payload.my_champion_key)
    enemy_ko = (
        champion_data.to_korean_name(payload.enemy_champion_key)
        if payload.enemy_champion_key
        else "전체"
    )
    patch_text = payload.patch_prefix or "전체 패치"

    st.markdown(
        f"""
<section class="result-summary">
  <div class="summary-main">
    <span>챌린저 매치업</span>
    <strong>상위 {payload.top_n}명 · {my_ko} vs {enemy_ko}</strong>
    <p>{_h(payload.lane_label)} · {_h(payload.period_label)} · 패치 {_h(patch_text)}</p>
  </div>
  <div class="kpi-row">
    <div class="kpi-card primary"><span>발견</span><strong>{total}</strong><em>경기</em></div>
    <div class="kpi-card"><span>승률</span><strong>{winrate}</strong><em>{wins}승 {losses}패</em></div>
    <div class="kpi-card"><span>스캔</span><strong>{payload.scanned_players}</strong><em>플레이어</em></div>
    <div class="kpi-card"><span>매치/API</span><strong>{payload.scanned_matches} / {payload.api_calls}</strong><em>캐시 {payload.cache_hits}</em></div>
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
    static_data: StaticData,
    *,
    detail_id: str,
    row_index: int,
    detail_open: bool = False,
    key_prefix: str = "match_detail",
) -> None:
    """결과 카드 요약을 Streamlit 네이티브 요소로 표시한다."""
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
    player_name, player_tag = _split_player_riot_id(row)
    player_riot_id = f"{player_name}#{player_tag}" if player_tag else player_name
    is_challenger_row = bool(row.get("player_puuid"))
    side_icons_html = _result_side_icons_html(row, static_data)
    loadout_html = _result_loadout_html(row, static_data)
    champion_level = int(row.get("my_champion_level") or 0)

    meta_col, summary_col, enemy_col, action_col = st.columns(
        [0.14, 0.43, 0.29, 0.14],
        gap="small",
        vertical_alignment="top",
    )
    with meta_col:
        st.markdown(
            f"""
<div class="match-meta">
  <strong class="{result_cls}">{result_text}</strong>
  <span>{_h(day_part)}</span>
  <span>{_h(time_part)}</span>
  <em>{duration_min}분</em>
</div>
            """,
            unsafe_allow_html=True,
        )

    with summary_col:
        if is_challenger_row:
            rank_text = row.get("player_rank")
            lp_text = row.get("player_league_points")
            st.markdown(
                f"""
<div class="player-target">
  <span>챌린저 플레이어</span>
  <strong>{_h(player_name)}</strong>
  <em>#{_h(player_tag)} · {rank_text}위 · {lp_text} LP</em>
</div>
                """,
                unsafe_allow_html=True,
            )
        st.markdown(
            f"""
<div class="my-summary">
  <div class="champ-portrait">
    <img src="{_h(my_icon)}" alt="{_h(my_ko)}" title="{_h(my_ko)}" />
    <span>{champion_level}</span>
  </div>
  <div class="side-icons">{side_icons_html}</div>
  <div class="score-block">
    <strong>{kills} / <b>{deaths}</b> / {assists}</strong>
    <span>CS {int(row["cs"])} · {duration_min}분 · {int(row.get("damage_to_champions") or 0):,} 피해</span>
  </div>
</div>
            """,
            unsafe_allow_html=True,
        )
        if loadout_html:
            st.markdown(loadout_html, unsafe_allow_html=True)

    with enemy_col:
        st.markdown(
            f"""
<div class="enemy-target">
  <img src="{_h(enemy_icon)}" alt="{_h(enemy_ko)}" title="{_h(enemy_ko)}" />
  <div>
    <span>상대 라이너</span>
    <strong>{_h(enemy_name)}</strong>
    <em>#{_h(enemy_tag)} · {_h(enemy_ko)}</em>
  </div>
</div>
            """,
            unsafe_allow_html=True,
        )

    with action_col:
        if is_challenger_row:
            player_copy_col, enemy_copy_col, opgg_col, detail_col = st.columns(
                4,
                gap="small",
            )
            with player_copy_col:
                st.iframe(_copy_riot_id_button_html(player_riot_id), height=36)
            with enemy_copy_col:
                st.iframe(_copy_riot_id_button_html(enemy_riot_id), height=36)
        else:
            enemy_copy_col, opgg_col, detail_col = st.columns(3, gap="small")
            with enemy_copy_col:
                st.iframe(_copy_riot_id_button_html(enemy_riot_id), height=36)

        with opgg_col:
            st.link_button(
                "↗",
                enemy_opgg_url or "#",
                help="OP.GG 열기",
                disabled=not bool(enemy_opgg_url),
                use_container_width=True,
            )
        with detail_col:
            st.button(
                "▴" if detail_open else "▾",
                key=f"{key_prefix}_toggle_{row_index}",
                help="매치 상세 닫기" if detail_open else "매치 상세 펼치기",
                on_click=_toggle_match_detail,
                args=(detail_id,),
                use_container_width=True,
            )


def _copy_riot_id_button_html(enemy_riot_id: str) -> str:
    """Riot ID를 클립보드에 복사하는 작은 HTML 버튼을 만든다."""
    return f"""
<style>
body {{
    margin: 0;
    background: transparent;
    font-family: "Source Sans Pro", sans-serif;
}}
.copy-action {{
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
    cursor: pointer;
}}
.copy-action:hover {{
    border-color: #20c997;
    color: #ffffff;
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
<button class="copy-action" type="button" title="Riot ID 복사" onclick="copyRiotId(this)">⧉</button>
"""


def _result_loadout_html(row: dict[str, Any], static_data: StaticData) -> str:
    """결과 카드에 표시할 최종 아이템 요약 HTML."""
    version = static_data.version
    item_icons = []
    for item_id in (row.get("my_items") or [])[:7]:
        url = item_icon_url(version, item_id)
        if url:
            item_icons.append(
                f'<img class="loadout-icon" src="{_h(url)}" alt="{item_id}" title="{item_id}" />'
            )
        else:
            item_icons.append('<div class="loadout-empty"></div>')

    if not item_icons:
        return ""

    return f"""
<div class="loadout-strip">
  <div class="loadout-group">{"".join(item_icons)}</div>
</div>
"""


def _result_side_icons_html(row: dict[str, Any], static_data: StaticData) -> str:
    """초상화 옆에 표시할 소환사 주문과 핵심 룬 아이콘."""
    spell_icons = []
    for spell_id in (row.get("my_summoner1_id"), row.get("my_summoner2_id")):
        url = static_data.summoner_icon_url(spell_id)
        name = static_data.summoner_name(spell_id)
        spell_icons.append(
            f'<img src="{_h(url)}" alt="{_h(name)}" title="{_h(name)}" />'
            if url
            else '<div class="side-icon-empty"></div>'
        )

    rune_icons = []
    primary_runes = row.get("my_primary_runes") or []
    rune_id = primary_runes[0] if primary_runes else None
    rune_url = static_data.rune_icon_url(rune_id)
    rune_name = static_data.rune_name(rune_id)
    rune_icons.append(
        f'<img src="{_h(rune_url)}" alt="{_h(rune_name)}" title="{_h(rune_name)}" />'
        if rune_url
        else '<div class="side-icon-empty"></div>'
    )

    tree_url = static_data.tree_icon_url(row.get("my_secondary_tree_id"))
    tree_name = static_data.tree_name(row.get("my_secondary_tree_id"))
    rune_icons.append(
        f'<img src="{_h(tree_url)}" alt="{_h(tree_name)}" title="{_h(tree_name)}" />'
        if tree_url
        else '<div class="side-icon-empty"></div>'
    )

    return f"""
<div class="side-icon-col">{"".join(spell_icons)}</div>
<div class="side-icon-col">{"".join(rune_icons)}</div>
"""


def _render_linked_match_detail(
    *,
    row: dict[str, Any],
    account: dict[str, Any],
    champion_data: ChampionData,
    cache: MatchCache,
    static_data: StaticData,
    config: AppConfig,
) -> None:
    """열린 카드 아래에 연결형 매치 상세를 표시한다."""
    match_full = cache.get_match(row["match_id"]) if row.get("match_id") else None
    focus = extract_focus_view(match_full, account["puuid"]) if match_full else None
    if focus is None:
        st.markdown(
            """
<div class="linked-detail-empty">
  이 매치의 상세 데이터를 찾을 수 없습니다.
</div>
            """,
            unsafe_allow_html=True,
        )
        return

    match_id = row.get("match_id") or ""
    timeline = cache.get_match_timeline(match_id) if match_id else None
    if timeline is None and match_id:
        try:
            with RiotClient(
                api_key=config.api_key,
                region=config.region,
            ) as client:
                timeline = fetch_match_timeline(client, cache, match_id)
        except RiotApiError as exc:
            st.warning(f"타임라인을 불러오지 못했습니다: {exc}")

    build_timeline = (
        extract_player_build_timeline(
            match_full,
            timeline,
            account["puuid"],
        )
        if timeline is not None
        else None
    )
    detail_html = render_match_detail(
        focus,
        champion_data,
        static_data,
        build_timeline,
    ).replace(
        '<div class="detail-panel">',
        '<div class="detail-panel linked-detail-panel">',
        1,
    )
    st.markdown(detail_html, unsafe_allow_html=True)


def _render_challenger_linked_match_detail(
    *,
    row: dict[str, Any],
    champion_data: ChampionData,
    cache: MatchCache,
    static_data: StaticData,
    config: AppConfig,
) -> None:
    """챌린저 결과 카드 아래에 연결형 매치 상세를 표시한다."""
    match_full = cache.get_match(row["match_id"]) if row.get("match_id") else None
    player_puuid = row.get("player_puuid")
    focus = (
        extract_focus_view(match_full, player_puuid)
        if match_full and player_puuid
        else None
    )
    if focus is None:
        st.markdown(
            """
<div class="linked-detail-empty">
  이 매치의 상세 데이터를 찾을 수 없습니다.
</div>
            """,
            unsafe_allow_html=True,
        )
        return

    match_id = row.get("match_id") or ""
    timeline = cache.get_match_timeline(match_id) if match_id else None
    if timeline is None and match_id:
        try:
            with RiotClient(
                api_key=config.api_key,
                region=config.region,
                platform=config.platform,
            ) as client:
                timeline = fetch_match_timeline(client, cache, match_id)
        except RiotApiError as exc:
            st.warning(f"타임라인을 불러오지 못했습니다: {exc}")

    build_timeline = (
        extract_player_build_timeline(
            match_full,
            timeline,
            player_puuid,
        )
        if timeline is not None
        else None
    )
    detail_html = render_match_detail(
        focus,
        champion_data,
        static_data,
        build_timeline,
    ).replace(
        '<div class="detail-panel">',
        '<div class="detail-panel linked-detail-panel">',
        1,
    )
    st.markdown(detail_html, unsafe_allow_html=True)


def render_results(
    payload: SearchPayload,
    champion_data: ChampionData,
    cache: MatchCache,
    static_data: StaticData,
    config: AppConfig,
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
    detail_ids = [
        str(row.get("match_id") or f"row-{idx}")
        for idx, row in enumerate(payload.results)
    ]
    valid_detail_ids = set(detail_ids)
    open_ids = (
        set(st.session_state.get(_MATCH_DETAIL_OPEN_STATE_KEY, [])) & valid_detail_ids
    )
    st.session_state[_MATCH_DETAIL_OPEN_STATE_KEY] = sorted(open_ids)

    for idx, (row, detail_id) in enumerate(
        zip(payload.results, detail_ids, strict=True)
    ):
        detail_open = detail_id in open_ids

        with st.container(
            border=False,
            key=f"match_result_card_{'win' if row['win'] else 'loss'}_{idx}",
        ):
            render_result_card(
                row,
                champion_data,
                static_data,
                detail_id=detail_id,
                row_index=idx,
                detail_open=detail_open,
            )
            if detail_open:
                _render_linked_match_detail(
                    row=row,
                    account=account,
                    champion_data=champion_data,
                    cache=cache,
                    static_data=static_data,
                    config=config,
                )

    st.download_button(
        label="CSV 다운로드",
        data=build_results_csv_bytes(payload),
        file_name=build_results_filename(payload),
        mime="text/csv",
        use_container_width=True,
    )


def render_challenger_results(
    payload: ChallengerSearchPayload,
    champion_data: ChampionData,
    cache: MatchCache,
    static_data: StaticData,
    config: AppConfig,
) -> None:
    """챌린저 검색 결과 요약, 목록, 상세 패널, CSV 다운로드를 표시한다."""
    render_challenger_result_summary(payload, champion_data)

    if not payload.results:
        render_empty_state()
        return

    render_section_title("챌린저 결과 목록")
    detail_ids = [
        f"challenger-{row.get('match_id') or idx}-{row.get('player_puuid') or idx}"
        for idx, row in enumerate(payload.results)
    ]
    valid_detail_ids = set(detail_ids)
    open_ids = (
        set(st.session_state.get(_MATCH_DETAIL_OPEN_STATE_KEY, [])) & valid_detail_ids
    )
    st.session_state[_MATCH_DETAIL_OPEN_STATE_KEY] = sorted(open_ids)

    for idx, (row, detail_id) in enumerate(
        zip(payload.results, detail_ids, strict=True)
    ):
        detail_open = detail_id in open_ids

        with st.container(
            border=False,
            key=f"challenger_match_result_card_{'win' if row['win'] else 'loss'}_{idx}",
        ):
            render_result_card(
                row,
                champion_data,
                static_data,
                detail_id=detail_id,
                row_index=idx,
                detail_open=detail_open,
                key_prefix="challenger_match_detail",
            )
            if detail_open:
                _render_challenger_linked_match_detail(
                    row=row,
                    champion_data=champion_data,
                    cache=cache,
                    static_data=static_data,
                    config=config,
                )

    st.download_button(
        label="CSV 다운로드",
        data=build_challenger_results_csv_bytes(payload),
        file_name=build_challenger_results_filename(payload),
        mime="text/csv",
        use_container_width=True,
    )
