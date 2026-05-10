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
from src.matchup_filter import extract_matchup_result
from src.opgg import build_opgg_url
from src.riot_client import (
    RiotApiAuthError,
    RiotApiError,
    RiotApiNotFound,
    RiotApiRateLimited,
    RiotClient,
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
def render_results(payload: dict[str, Any], champion_data: ChampionData) -> None:
    results = payload["results"]
    account = payload["account"]
    scanned_total = payload["scanned_total"]
    cache_hits = payload["cache_hits"]
    api_calls = payload["api_calls"]

    my_ko = champion_data.to_korean_name(payload["my_champion_key"])
    enemy_ko = champion_data.to_korean_name(payload["enemy_champion_key"])

    st.subheader("검색 조건")
    st.markdown(
        f"- 검색한 Riot ID: **{account['game_name']}#{account['tag_line']}**\n"
        f"- 내 챔피언: **{my_ko}**\n"
        f"- 상대 챔피언: **{enemy_ko}**\n"
        f"- 라인: **{payload['lane_label']}**\n"
        f"- 큐: 솔로 랭크\n"
        f"- 기간: {payload['period_kind']}"
    )

    st.subheader("결과 요약")
    cols = st.columns(4)
    cols[0].metric("매칭된 경기", f"{len(results)}판")
    cols[1].metric("스캔한 매치", f"{scanned_total}개")
    cols[2].metric("캐시 재사용", f"{cache_hits}회")
    cols[3].metric("API 호출", f"{api_calls}회")

    if not results:
        st.info(
            "조건에 맞는 경기를 찾지 못했습니다.\n\n"
            "확인해볼 것:\n"
            "- 검색 기간을 늘려보세요.\n"
            "- 내 챔피언/상대 챔피언 선택을 확인하세요.\n"
            "- 라인이 다르게 기록된 경기일 수 있습니다."
        )
        return

    # 검색 대상 Riot ID의 OP.GG 링크
    my_opgg_url = build_opgg_url(account["game_name"], account["tag_line"])
    st.markdown(
        f"[OP.GG 열기 — {account['game_name']}#{account['tag_line']}]"
        f"({my_opgg_url})"
    )

    st.subheader("결과 목록")

    # 컬럼 헤더: 날짜 / 결과 / 내 챔피언 / 상대 챔피언 / 매치 ID / 상대 Riot ID
    col_widths = [3, 1, 2, 2, 3, 4]
    header_cols = st.columns(col_widths)
    header_cols[0].markdown("**날짜**")
    header_cols[1].markdown("**결과**")
    header_cols[2].markdown("**내 챔피언**")
    header_cols[3].markdown("**상대 챔피언**")
    header_cols[4].markdown("**매치 ID**")
    header_cols[5].markdown("**상대 Riot ID**")

    for r in results:
        row_cols = st.columns(col_widths)
        date_text = (
            unix_to_kst_datetime_str(r["game_creation"])
            if r.get("game_creation")
            else r.get("game_date") or ""
        )
        row_cols[0].write(date_text)
        row_cols[1].write("승리" if r["win"] else "패배")
        row_cols[2].write(champion_data.to_korean_name(r["my_champion_key"]))
        row_cols[3].write(champion_data.to_korean_name(r["enemy_champion_key"]))
        row_cols[4].write(r["match_id"] or "")
        # st.code는 우측 상단에 복사 버튼을 자동으로 표시한다.
        # 표시/복사 모두 태그(#XXX)를 제외한 닉네임만 사용한다.
        enemy_name_only = (
            r.get("enemy_game_name") or r["enemy_riot_id"].split("#", 1)[0]
        )
        row_cols[5].code(enemy_name_only, language=None)

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
        label="CSV 다운로드",
        data=csv_buffer.getvalue().encode("utf-8-sig"),
        file_name=(
            f"matchup_{payload['my_champion_key']}_vs_"
            f"{payload['enemy_champion_key']}.csv"
        ),
        mime="text/csv",
    )


# ---- 메인 ----
def main() -> None:
    st.set_page_config(page_title="LoL 매치업 상대 닉네임 추출기", page_icon="🎯")
    st.title("LoL 매치업 상대 닉네임 추출기")
    st.caption(
        "내가 X로 플레이한 매치 중 상대 라이너가 Y였던 경기만 골라, "
        "상대 Riot ID 목록을 출력합니다. 한국 서버 / 솔로 랭크 전용."
    )

    cfg = load_env()

    # 사이드바: 환경/캐시 정보
    with st.sidebar:
        st.header("환경 설정")
        st.write(f"리전: `{cfg['region']}` / 플랫폼: `{cfg['platform']}`")
        st.write(f"큐 ID: `{cfg['queue_id']}` (솔로 랭크)")
        st.write(f"DB: `{cfg['db_path']}`")

        if cfg["api_key"]:
            st.success("RIOT_API_KEY 감지됨")
        else:
            st.error(
                "RIOT_API_KEY가 설정되어 있지 않습니다.\n\n"
                ".env 파일에 키를 추가한 뒤 앱을 다시 실행하세요."
            )

        st.divider()
        if st.button("챔피언 목록 새로고침"):
            load_champion_data.clear()
            try:
                champion_data = load_champion_data(cfg["db_path"], force_refresh=True)
                st.success(f"챔피언 목록 갱신 완료 (버전 {champion_data.version})")
            except Exception as exc:
                st.error(f"챔피언 목록 갱신 실패: {exc}")

    # 챔피언 목록 로딩
    try:
        champion_data = load_champion_data(cfg["db_path"])
    except Exception as exc:
        st.error(f"챔피언 목록을 불러올 수 없습니다: {exc}")
        return

    st.caption(f"챔피언 데이터: Data Dragon 버전 {champion_data.version} (ko_KR)")

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

    with st.form("search_form"):
        riot_id_raw = st.text_input(
            "검색할 Riot ID",
            placeholder="예: Hide on bush#KR1",
            value=st.session_state.get("last_riot_id", ""),
        )

        col1, col2 = st.columns(2)
        with col1:
            period_kind = st.selectbox(
                "검색 기간",
                PERIOD_OPTIONS,
                index=PERIOD_OPTIONS.index(DEFAULT_PERIOD_LABEL),
            )
        with col2:
            max_matches = st.selectbox(
                "최대 검색 매치 수",
                (50, 100, 200, 300),
                index=1,
                help="이 기간 내 솔로 랭크 매치 중 최근 N개를 분석합니다.",
            )

        today = date.today()
        default_start = today - timedelta(days=90)
        col_a, col_b = st.columns(2)
        with col_a:
            custom_start = st.date_input(
                "시작일 (직접 지정 시)",
                value=default_start,
                disabled=(period_kind != PERIOD_CUSTOM_LABEL),
            )
        with col_b:
            custom_end = st.date_input(
                "종료일 (직접 지정 시)",
                value=today,
                disabled=(period_kind != PERIOD_CUSTOM_LABEL),
            )

        col_x, col_y, col_z = st.columns(3)
        with col_x:
            my_champion_korean = st.selectbox(
                "내 챔피언",
                champion_data.korean_names,
                index=champion_data.korean_names.index(default_my),
            )
        with col_y:
            enemy_champion_korean = st.selectbox(
                "상대 챔피언",
                champion_data.korean_names,
                index=champion_data.korean_names.index(default_enemy),
            )
        with col_z:
            lane_label = st.selectbox(
                "라인",
                LANE_LABELS,
                index=LANE_LABELS.index("미드"),
            )

        submitted = st.form_submit_button("검색", type="primary")

    if submitted:
        st.session_state["last_riot_id"] = riot_id_raw
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
        render_results(payload, champion_data)


if __name__ == "__main__":
    main()
