"""matchup_index 기반 DB조회 서비스."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .cache import MatchCache
from .champions import ChampionData
from .riot_client import RiotApiError
from .search_service import (
    PERIOD_CUSTOM_LABEL,
    PERIOD_PRESETS,
    SEARCH_ALL_LABEL,
)
from .utils import LANE_LABEL_TO_TEAM_POSITION, date_range_to_unix


DB_ALL_LABEL = SEARCH_ALL_LABEL
DB_ENEMY_ALL_LABEL = DB_ALL_LABEL
DB_PERIOD_ALL_LABEL = DB_ALL_LABEL
DB_PERIOD_OPTIONS: tuple[str, ...] = (
    DB_PERIOD_ALL_LABEL,
    *PERIOD_PRESETS.keys(),
    PERIOD_CUSTOM_LABEL,
)
DB_DEFAULT_PERIOD_LABEL = DB_PERIOD_ALL_LABEL


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
    target_puuids: tuple[str, ...]
    resolve_cache_hits: int
    resolve_api_calls: int
    result_limit: int | None = None
    result_offset: int = 0
    fixed_start_ts: int | None = None
    fixed_end_ts: int | None = None
    fixed_patch_prefix: str | None = None
    snapshot_max_index_id: int | None = None
    target_label: str = "관심 유저"


@dataclass(frozen=True)
class IndexedMatchupSearchPayload:
    """DB조회 결과와 렌더링에 필요한 부가 정보."""

    results: list[dict[str, Any]]
    scanned_total: int
    cache_hits: int
    api_calls: int
    my_champion_key: str | None
    enemy_champion_key: str | None
    lane_label: str
    period_kind: str
    start_ts: int
    end_ts: int
    patch_prefix: str | None
    target_label: str
    memos: dict[str, str]
    total: int
    wins: int
    losses: int
    target_puuids: tuple[str, ...]
    snapshot_max_index_id: int


@dataclass(frozen=True)
class _IndexedMatchupFilters:
    """목록·요약·전체 복사가 공유하는 확정 SQL 조건."""

    my_champion_key: str | None
    enemy_champion_key: str | None
    lane_value: str
    start_ts: int
    end_ts: int
    patch_prefix: str | None
    snapshot_max_index_id: int


def _patch_prefix(version: str) -> str | None:
    """Data Dragon 버전에서 major.minor 패치 prefix를 추출한다."""
    parts = version.split(".")
    if len(parts) < 2:
        return None
    return ".".join(parts[:2])


def _resolve_filters(
    request: IndexedMatchupSearchRequest,
    champion_data: ChampionData,
    cache: MatchCache,
) -> _IndexedMatchupFilters:
    """한 조회에서 사용할 챔피언·기간·스냅샷 조건을 확정한다."""
    my_champion_key: str | None = None
    if request.my_champion_korean != DB_ALL_LABEL:
        my_champion_key = champion_data.to_english_key(request.my_champion_korean)
        if not my_champion_key:
            raise RiotApiError("선택한 기준 챔피언을 인식하지 못했습니다.")

    enemy_champion_key = None
    if request.enemy_champion_korean != DB_ENEMY_ALL_LABEL:
        enemy_champion_key = champion_data.to_english_key(request.enemy_champion_korean)
        if not enemy_champion_key:
            raise RiotApiError("선택한 상대 챔피언을 인식하지 못했습니다.")

    if request.lane_label not in LANE_LABEL_TO_TEAM_POSITION:
        raise RiotApiError("선택한 라인을 인식하지 못했습니다.")
    lane_value = LANE_LABEL_TO_TEAM_POSITION[request.lane_label]
    if request.fixed_start_ts is not None and request.fixed_end_ts is not None:
        start_ts = request.fixed_start_ts
        end_ts = request.fixed_end_ts
    elif request.period_kind == DB_PERIOD_ALL_LABEL:
        start_ts = 0
        end_ts = int(datetime.now().timestamp())
    elif request.period_kind in PERIOD_PRESETS:
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
        request.fixed_patch_prefix
        if request.fixed_start_ts is not None
        else (
            _patch_prefix(champion_data.version) if request.current_patch_only else None
        )
    )
    snapshot_max_index_id = (
        request.snapshot_max_index_id
        if request.snapshot_max_index_id is not None
        else cache.get_matchup_index_max_id()
    )
    return _IndexedMatchupFilters(
        my_champion_key=my_champion_key,
        enemy_champion_key=enemy_champion_key,
        lane_value=lane_value,
        start_ts=start_ts,
        end_ts=end_ts,
        patch_prefix=patch_prefix,
        snapshot_max_index_id=snapshot_max_index_id,
    )


def run_indexed_matchup_search(
    *,
    request: IndexedMatchupSearchRequest,
    champion_data: ChampionData,
    cache: MatchCache,
) -> IndexedMatchupSearchPayload:
    """Riot API 호출 없이 matchup_index에서만 매치업을 조회한다."""
    filters = _resolve_filters(request, champion_data, cache)
    results = cache.search_matchup_index(
        player_champion_key=filters.my_champion_key,
        enemy_champion_key=filters.enemy_champion_key,
        lane=filters.lane_value,
        start_ts=filters.start_ts,
        end_ts=filters.end_ts,
        patch_prefix=filters.patch_prefix,
        player_puuids=list(request.target_puuids),
        max_index_id=filters.snapshot_max_index_id,
        limit=request.result_limit,
        offset=request.result_offset,
    )
    stats = cache.get_matchup_index_stats(
        player_champion_key=filters.my_champion_key,
        enemy_champion_key=filters.enemy_champion_key,
        lane=filters.lane_value,
        start_ts=filters.start_ts,
        end_ts=filters.end_ts,
        patch_prefix=filters.patch_prefix,
        player_puuids=list(request.target_puuids),
        max_index_id=filters.snapshot_max_index_id,
    )
    memo_puuids = [
        puuid
        for row in results
        for puuid in (row.get("player_puuid"), row.get("enemy_puuid"))
        if puuid
    ]

    return IndexedMatchupSearchPayload(
        results=results,
        scanned_total=stats["total"],
        cache_hits=request.resolve_cache_hits,
        api_calls=request.resolve_api_calls,
        my_champion_key=filters.my_champion_key,
        enemy_champion_key=filters.enemy_champion_key,
        lane_label=request.lane_label,
        period_kind=request.period_kind or DB_DEFAULT_PERIOD_LABEL,
        start_ts=filters.start_ts,
        end_ts=filters.end_ts,
        patch_prefix=filters.patch_prefix,
        target_label=request.target_label,
        memos=cache.get_player_memos(memo_puuids),
        total=stats["total"],
        wins=stats["wins"],
        losses=stats["losses"],
        target_puuids=request.target_puuids,
        snapshot_max_index_id=filters.snapshot_max_index_id,
    )


def find_indexed_enemy_riot_ids(
    *,
    request: IndexedMatchupSearchRequest,
    champion_data: ChampionData,
    cache: MatchCache,
) -> list[str]:
    """같은 확정 조건에서 고유 상대 Riot ID 열만 읽는다."""
    filters = _resolve_filters(request, champion_data, cache)
    return cache.search_matchup_enemy_riot_ids(
        player_champion_key=filters.my_champion_key,
        enemy_champion_key=filters.enemy_champion_key,
        lane=filters.lane_value,
        start_ts=filters.start_ts,
        end_ts=filters.end_ts,
        patch_prefix=filters.patch_prefix,
        player_puuids=list(request.target_puuids),
        max_index_id=filters.snapshot_max_index_id,
    )
