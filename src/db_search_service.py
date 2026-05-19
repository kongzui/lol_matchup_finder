"""matchup_index 기반 DB조회 서비스."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .cache import MatchCache
from .champions import ChampionData
from .riot_client import RiotApiError
from .search_service import (
    DEFAULT_PERIOD_LABEL,
    PERIOD_CUSTOM_LABEL,
    PERIOD_PRESETS,
)
from .utils import LANE_LABEL_TO_TEAM_POSITION, date_range_to_unix


DB_ENEMY_ALL_LABEL = "전체"


@dataclass(frozen=True)
class IndexedMatchupSearchRequest:
    """DB조회 입력 조건."""

    my_champion_korean: str
    enemy_champion_korean: str
    lane_label: str
    period_kind: str
    custom_start: date
    custom_end: date
    current_patch_only: bool


@dataclass(frozen=True)
class IndexedMatchupSearchPayload:
    """DB조회 결과와 렌더링에 필요한 부가 정보."""

    results: list[dict[str, Any]]
    scanned_total: int
    cache_hits: int
    api_calls: int
    my_champion_key: str
    enemy_champion_key: str | None
    lane_label: str
    period_kind: str
    start_ts: int
    end_ts: int
    patch_prefix: str | None


def _patch_prefix(version: str) -> str | None:
    """Data Dragon 버전에서 major.minor 패치 prefix를 추출한다."""
    parts = version.split(".")
    if len(parts) < 2:
        return None
    return ".".join(parts[:2])


def run_indexed_matchup_search(
    *,
    request: IndexedMatchupSearchRequest,
    champion_data: ChampionData,
    cache: MatchCache,
) -> IndexedMatchupSearchPayload:
    """Riot API 호출 없이 matchup_index에서만 매치업을 조회한다."""
    my_champion_key = champion_data.to_english_key(request.my_champion_korean)
    if not my_champion_key:
        raise RiotApiError("선택한 내 챔피언을 인식하지 못했습니다.")

    enemy_champion_key = None
    if request.enemy_champion_korean != DB_ENEMY_ALL_LABEL:
        enemy_champion_key = champion_data.to_english_key(request.enemy_champion_korean)
        if not enemy_champion_key:
            raise RiotApiError("선택한 상대 챔피언을 인식하지 못했습니다.")

    lane_value = LANE_LABEL_TO_TEAM_POSITION[request.lane_label]
    if request.period_kind in PERIOD_PRESETS:
        days = PERIOD_PRESETS[request.period_kind]
        end_ts = int(datetime.now().timestamp())
        start_ts = end_ts - days * 24 * 60 * 60
    elif request.period_kind == PERIOD_CUSTOM_LABEL:
        if request.custom_start > request.custom_end:
            raise RiotApiError("시작일이 종료일보다 늦을 수 없습니다.")
        start_ts, end_ts = date_range_to_unix(
            datetime.combine(request.custom_start, datetime.min.time()),
            datetime.combine(request.custom_end, datetime.min.time()),
        )
    else:
        raise RiotApiError("검색 기간을 인식하지 못했습니다.")

    patch_prefix = (
        _patch_prefix(champion_data.version) if request.current_patch_only else None
    )
    results = cache.search_matchup_index(
        player_champion_key=my_champion_key,
        enemy_champion_key=enemy_champion_key,
        lane=lane_value,
        start_ts=start_ts,
        end_ts=end_ts,
        patch_prefix=patch_prefix,
    )

    return IndexedMatchupSearchPayload(
        results=results,
        scanned_total=len(results),
        cache_hits=0,
        api_calls=0,
        my_champion_key=my_champion_key,
        enemy_champion_key=enemy_champion_key,
        lane_label=request.lane_label,
        period_kind=request.period_kind or DEFAULT_PERIOD_LABEL,
        start_ts=start_ts,
        end_ts=end_ts,
        patch_prefix=patch_prefix,
    )
