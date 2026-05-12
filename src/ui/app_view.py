"""Streamlit 앱 화면 조립."""

from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from src.cache import MatchCache
from src.challenger_service import (
    DEFAULT_CHALLENGER_DAYS,
    DEFAULT_CHALLENGER_TOP_N,
    DEFAULT_MATCHES_PER_PLAYER,
    ChallengerSearchRequest,
    run_challenger_search,
)
from src.champions import ChampionData, ChampionRepository
from src.config import AppConfig
from src.riot_client import (
    RiotApiAuthError,
    RiotApiError,
    RiotApiNotFound,
    RiotApiRateLimited,
)
from src.search_service import (
    DEFAULT_PERIOD_LABEL,
    PERIOD_CUSTOM_LABEL,
    PERIOD_OPTIONS,
    SearchRequest,
    run_search,
)
from src.static_data import StaticData, StaticDataRepository
from src.ui.components import (
    render_challenger_results,
    render_matchup_header,
    render_results,
    render_section_title,
)
from src.utils import LANE_LABELS, RiotIdParseError


CACHE_VERSION = 4


@st.cache_resource(show_spinner=False)
def get_match_cache(db_path: str, cache_version: int) -> MatchCache:
    """SQLite 캐시 객체를 가져온다."""
    _ = cache_version
    return MatchCache(db_path)


@st.cache_resource(show_spinner=False)
def get_champion_repo(db_path: str, cache_version: int) -> ChampionRepository:
    _ = cache_version
    return ChampionRepository(db_path)


@st.cache_resource(show_spinner="챔피언 목록을 불러오는 중...", ttl=60 * 60)
def load_champion_data(
    db_path: str,
    force_refresh: bool = False,
    cache_version: int = CACHE_VERSION,
) -> ChampionData:
    _ = cache_version
    repo = get_champion_repo(db_path, cache_version)
    return repo.load(force_refresh=force_refresh)


@st.cache_resource(show_spinner=False)
def get_static_data_repo(db_path: str, cache_version: int) -> StaticDataRepository:
    _ = cache_version
    return StaticDataRepository(db_path)


@st.cache_resource(
    show_spinner="룬·소환사 주문 메타데이터를 불러오는 중...", ttl=60 * 60
)
def load_static_data(
    db_path: str,
    force_refresh: bool = False,
    cache_version: int = CACHE_VERSION,
) -> StaticData:
    _ = cache_version
    repo = get_static_data_repo(db_path, cache_version)
    return repo.load(force_refresh=force_refresh)


def _default_champion(champion_data: ChampionData, preferred: str) -> str:
    if preferred in champion_data.korean_names:
        return preferred
    return champion_data.korean_names[0]


def _render_sidebar(config: AppConfig) -> None:
    """환경 정보와 메타데이터 갱신 버튼을 표시한다."""
    with st.sidebar:
        st.markdown("### 환경 설정")
        if config.api_key:
            st.success("RIOT_API_KEY 감지됨")
        else:
            st.error(
                "RIOT_API_KEY 미설정\n\n.env 파일에 키를 추가한 뒤 앱을 다시 실행하세요."
            )

        st.caption(f"리전: `{config.region}` · 플랫폼: `{config.platform}`")
        st.caption(f"큐 ID: `{config.queue_id}` (솔로 랭크)")
        st.caption(f"DB: `{config.db_path}`")

        st.divider()
        st.markdown("### 챌린저 대시보드")
        st.caption(f"기본 랭킹 범위: `상위 {DEFAULT_CHALLENGER_TOP_N}명`")
        st.caption(f"기본 검색 기간: `최근 {DEFAULT_CHALLENGER_DAYS}일`")
        st.caption(f"기본 1인당 매치: `{DEFAULT_MATCHES_PER_PLAYER}판`")

        st.divider()
        if st.button("챔피언 목록 새로고침", use_container_width=True):
            load_champion_data.clear()
            try:
                champion_data = load_champion_data(
                    config.db_path,
                    force_refresh=True,
                    cache_version=CACHE_VERSION,
                )
                st.success(f"갱신 완료 · v{champion_data.version}")
            except Exception as exc:
                st.error(f"갱신 실패: {exc}")

        if st.button("룬·주문 메타 새로고침", use_container_width=True):
            load_static_data.clear()
            try:
                static_data = load_static_data(
                    config.db_path,
                    force_refresh=True,
                    cache_version=CACHE_VERSION,
                )
                st.success(f"갱신 완료 · v{static_data.version}")
            except Exception as exc:
                st.error(f"갱신 실패: {exc}")


def _load_required_data(config: AppConfig) -> tuple[ChampionData, StaticData] | None:
    """UI에 필요한 정적 데이터를 로딩한다."""
    try:
        champion_data = load_champion_data(
            config.db_path,
            cache_version=CACHE_VERSION,
        )
    except Exception as exc:
        st.error(f"챔피언 목록을 불러올 수 없습니다: {exc}")
        return None

    try:
        static_data = load_static_data(
            config.db_path,
            cache_version=CACHE_VERSION,
        )
    except Exception as exc:
        st.error(f"룬·소환사 주문 메타데이터를 불러올 수 없습니다: {exc}")
        return None

    return champion_data, static_data


def _render_search_controls(
    champion_data: ChampionData,
    default_riot_id: str | None,
) -> tuple[SearchRequest, bool]:
    """검색 입력 위젯을 그리고 요청 객체를 만든다."""
    if default_riot_id and "riot_id_input" not in st.session_state:
        st.session_state["riot_id_input"] = default_riot_id

    render_section_title("검색 조건")
    with st.container(border=True):
        riot_id_raw = st.text_input(
            "검색할 Riot ID",
            placeholder="예: Hide on bush#KR1",
            key="riot_id_input",
        )

        default_my = _default_champion(champion_data, "아리")
        default_enemy = _default_champion(champion_data, "사일러스")
        c_my, c_enemy, c_lane = st.columns([2.3, 2.3, 1.2])
        with c_my:
            my_champion_korean = st.selectbox(
                "내 챔피언",
                champion_data.korean_names,
                index=champion_data.korean_names.index(default_my),
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

        submitted = st.button(
            "검색 실행",
            type="primary",
            use_container_width=True,
        )

    request = SearchRequest(
        riot_id_raw=riot_id_raw,
        period_kind=period_kind,
        custom_start=custom_start,
        custom_end=custom_end,
        my_champion_korean=my_champion_korean,
        enemy_champion_korean=enemy_champion_korean,
        lane_label=lane_label,
        max_matches=int(max_matches),
    )
    return request, submitted


def _render_challenger_controls(
    champion_data: ChampionData,
) -> tuple[ChallengerSearchRequest, bool]:
    """챌린저 매치업 검색 입력 위젯을 그리고 요청 객체를 만든다."""
    render_section_title("챌린저 검색 조건")
    with st.container(border=True):
        default_my = _default_champion(champion_data, "아리")
        champion_options = ["전체", *champion_data.korean_names]

        c_my, c_enemy, c_lane = st.columns([2.3, 2.3, 1.2])
        with c_my:
            my_champion_korean = st.selectbox(
                "내 챔피언",
                champion_data.korean_names,
                index=champion_data.korean_names.index(default_my),
                key="challenger_my_champion",
            )
        with c_enemy:
            enemy_champion_korean = st.selectbox(
                "상대 챔피언",
                champion_options,
                index=0,
                key="challenger_enemy_champion",
            )
        with c_lane:
            lane_label = st.selectbox(
                "라인",
                LANE_LABELS,
                index=LANE_LABELS.index("미드"),
                key="challenger_lane",
            )

        c_rank, c_days, c_matches = st.columns(3)
        with c_rank:
            top_n = st.selectbox(
                "랭킹 범위",
                (50, 100, 200, 300),
                index=(50, 100, 200, 300).index(DEFAULT_CHALLENGER_TOP_N),
                help="KR 챌린저 랭킹 상위 N명을 조회합니다.",
                key="challenger_top_n",
            )
        with c_days:
            days = st.selectbox(
                "검색 기간",
                (3, 7, 14, 30),
                index=(3, 7, 14, 30).index(DEFAULT_CHALLENGER_DAYS),
                format_func=lambda value: f"최근 {value}일",
                key="challenger_days",
            )
        with c_matches:
            matches_per_player = st.selectbox(
                "1인당 최대 매치",
                (20, 50, 100),
                index=(20, 50, 100).index(DEFAULT_MATCHES_PER_PLAYER),
                help="플레이어별 최근 솔로 랭크 매치 중 최대 N개를 분석합니다.",
                key="challenger_matches_per_player",
            )

        current_patch_only = st.checkbox(
            "이번 패치만",
            value=True,
            help="현재 Data Dragon 패치와 같은 major.minor 버전의 경기만 포함합니다.",
            key="challenger_current_patch_only",
        )

        submitted = st.button(
            "챌린저 검색 실행",
            type="primary",
            use_container_width=True,
        )

    request = ChallengerSearchRequest(
        my_champion_korean=my_champion_korean,
        enemy_champion_korean=enemy_champion_korean,
        lane_label=lane_label,
        top_n=int(top_n),
        days=int(days),
        matches_per_player=int(matches_per_player),
        current_patch_only=bool(current_patch_only),
    )
    return request, submitted


def _run_submitted_search(
    *,
    config: AppConfig,
    request: SearchRequest,
    champion_data: ChampionData,
) -> None:
    """검색 버튼 클릭 시 검색을 실행하고 세션에 결과를 저장한다."""
    progress_bar = st.progress(0.0)
    status_box = st.empty()
    cache = get_match_cache(config.db_path, CACHE_VERSION)

    def progress_cb(value: float) -> None:
        progress_bar.progress(min(max(value, 0.0), 1.0))

    def status_cb(message: str) -> None:
        status_box.info(message)

    try:
        payload = run_search(
            config=config,
            request=request,
            champion_data=champion_data,
            cache=cache,
            progress_cb=progress_cb,
            status_cb=status_cb,
        )
    except RiotIdParseError as exc:
        st.error(str(exc))
        return
    except RiotApiAuthError as exc:
        st.error(str(exc))
        return
    except RiotApiNotFound as exc:
        st.error(str(exc))
        return
    except RiotApiRateLimited as exc:
        st.error(
            str(exc)
            + "\n이미 조회한 경기는 캐시에서 재사용되므로 다시 시도하면 더 빠릅니다."
        )
        return
    except RiotApiError as exc:
        st.error(str(exc))
        return
    finally:
        status_box.empty()
        progress_bar.empty()

    st.session_state["last_payload"] = payload


def _run_submitted_challenger_search(
    *,
    config: AppConfig,
    request: ChallengerSearchRequest,
    champion_data: ChampionData,
) -> None:
    """챌린저 검색 버튼 클릭 시 검색을 실행하고 세션에 결과를 저장한다."""
    progress_bar = st.progress(0.0)
    status_box = st.empty()
    cache = get_match_cache(config.db_path, CACHE_VERSION)

    def progress_cb(value: float) -> None:
        progress_bar.progress(min(max(value, 0.0), 1.0))

    def status_cb(message: str) -> None:
        status_box.info(message)

    try:
        payload = run_challenger_search(
            config=config,
            request=request,
            champion_data=champion_data,
            cache=cache,
            progress_cb=progress_cb,
            status_cb=status_cb,
        )
    except RiotApiAuthError as exc:
        st.error(str(exc))
        return
    except RiotApiNotFound as exc:
        st.error(str(exc))
        return
    except RiotApiRateLimited as exc:
        st.error(
            str(exc)
            + "\n이미 조회한 경기는 캐시에서 재사용되므로 다시 시도하면 더 빠릅니다."
        )
        return
    except RiotApiError as exc:
        st.error(str(exc))
        return
    finally:
        status_box.empty()
        progress_bar.empty()

    st.session_state["last_challenger_payload"] = payload


def render_app(config: AppConfig) -> None:
    """전체 Streamlit 앱 화면을 렌더링한다."""
    _render_sidebar(config)
    loaded = _load_required_data(config)
    if loaded is None:
        return

    champion_data, static_data = loaded
    cache = get_match_cache(config.db_path, CACHE_VERSION)
    default_riot_id = cache.get_latest_search_riot_id()
    riot_tab, challenger_tab = st.tabs(["Riot ID 검색", "챌린저 매치업"])

    with riot_tab:
        header_slot = st.empty()
        request, submitted = _render_search_controls(champion_data, default_riot_id)

        my_key = champion_data.to_english_key(request.my_champion_korean)
        enemy_key = champion_data.to_english_key(request.enemy_champion_korean)
        with header_slot.container():
            render_matchup_header(
                riot_id_raw=request.riot_id_raw,
                lane_label=request.lane_label,
                period_kind=request.period_kind,
                max_matches=request.max_matches,
                my_champion_korean=request.my_champion_korean,
                enemy_champion_korean=request.enemy_champion_korean,
                my_champion_key=my_key,
                enemy_champion_key=enemy_key,
                champion_version=champion_data.version,
            )

        if submitted:
            _run_submitted_search(
                config=config,
                request=request,
                champion_data=champion_data,
            )

        payload = st.session_state.get("last_payload")
        if payload is not None:
            render_results(payload, champion_data, cache, static_data, config)

    with challenger_tab:
        challenger_request, challenger_submitted = _render_challenger_controls(
            champion_data,
        )

        if challenger_submitted:
            _run_submitted_challenger_search(
                config=config,
                request=challenger_request,
                champion_data=champion_data,
            )

        challenger_payload = st.session_state.get("last_challenger_payload")
        if challenger_payload is not None:
            render_challenger_results(
                challenger_payload,
                champion_data,
                cache,
                static_data,
                config,
            )
