"""매치업 검색 흐름을 담당하는 서비스."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .cache import MatchCache, normalize_riot_id_key
from .champions import ChampionData
from .config import AppConfig
from .matchup_filter import extract_matchup_result
from .riot_client import RiotApiError, RiotApiNotFound, RiotClient
from .utils import (
    LANE_LABEL_TO_TEAM_POSITION,
    date_range_to_unix,
    days_ago_to_unix_range,
    parse_riot_id,
)


PAGE_SIZE = 100  # Match-V5 한 번에 100개까지
MAX_PAGES = 5  # 안전장치: 최대 500매치까지만 페이지네이션

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
INDEX_MIN_TIER = "DIAMOND"
SOLO_RANK_QUEUE_TYPE = "RANKED_SOLO_5x5"
TIER_ORDER: dict[str, int] = {
    "IRON": 0,
    "BRONZE": 1,
    "SILVER": 2,
    "GOLD": 3,
    "PLATINUM": 4,
    "EMERALD": 5,
    "DIAMOND": 6,
    "MASTER": 7,
    "GRANDMASTER": 8,
    "CHALLENGER": 9,
}

ProgressCallback = Callable[[float], None]
StatusCallback = Callable[[str], None]


@dataclass(frozen=True)
class SearchRequest:
    """사용자가 입력한 검색 조건."""

    riot_id_raw: str
    period_kind: str
    custom_start: date
    custom_end: date
    my_champion_korean: str
    enemy_champion_korean: str
    lane_label: str
    max_matches: int


@dataclass(frozen=True)
class SearchPayload:
    """검색 결과와 결과 렌더링에 필요한 부가 정보."""

    results: list[dict[str, Any]]
    scanned_total: int
    cache_hits: int
    api_calls: int
    account: dict[str, Any]
    my_champion_key: str
    enemy_champion_key: str
    lane_label: str
    period_kind: str
    start_ts: int
    end_ts: int
    index_allowed: bool
    index_tier: str | None
    indexed_rows: int


def resolve_puuid(
    client: RiotClient,
    cache: MatchCache,
    game_name: str,
    tag_line: str,
) -> dict[str, Any]:
    """Riot ID로 PUUID를 조회하되 캐시를 먼저 사용한다."""
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


def fetch_all_match_ids(
    client: RiotClient,
    puuid: str,
    queue_id: int,
    start_ts: int,
    end_ts: int,
    max_total: int,
) -> list[str]:
    """Match-V5 페이지네이션으로 matchId 목록을 가져온다."""
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

        new_ids = [match_id for match_id in ids if match_id not in seen]
        for match_id in new_ids:
            seen.add(match_id)
        collected.extend(new_ids)
        if len(ids) < page_count:
            break

    return collected[:max_total]


def fetch_match_detail(
    client: RiotClient,
    cache: MatchCache,
    match_id: str,
) -> dict[str, Any] | None:
    """matchId로 상세 데이터를 가져오되 캐시를 먼저 사용한다."""
    cached = cache.get_match(match_id)
    if cached is not None:
        return cached

    try:
        match = client.get_match_by_id(match_id)
    except RiotApiNotFound:
        return None

    cache.save_match(match_id, match)
    return match


def fetch_match_timeline(
    client: RiotClient,
    cache: MatchCache,
    match_id: str,
) -> dict[str, Any] | None:
    """matchId로 타임라인 데이터를 가져오되 캐시를 먼저 사용한다."""
    cached = cache.get_match_timeline(match_id)
    if cached is not None:
        return cached

    try:
        timeline = client.get_match_timeline_by_id(match_id)
    except RiotApiNotFound:
        return None

    cache.save_match_timeline(match_id, timeline)
    return timeline


def _is_index_allowed(profile: dict[str, Any] | None) -> bool:
    tier = str((profile or {}).get("tier") or "").upper()
    min_tier_value = TIER_ORDER[INDEX_MIN_TIER]
    return TIER_ORDER.get(tier, -1) >= min_tier_value


def fetch_ranked_profile(
    client: RiotClient,
    cache: MatchCache,
    puuid: str,
    queue_id: int,
) -> tuple[dict[str, Any] | None, int, int]:
    """솔로 랭크 프로필을 가져오고 (profile, cache_hits, api_calls)를 반환한다."""
    cached = cache.get_ranked_profile(puuid, queue_id)
    if cached is not None and cached.get("tier"):
        return cached, 1, 0

    try:
        entries = client.get_league_entries_by_puuid(puuid)
    except RiotApiNotFound:
        entries = []
    api_calls = 1

    if not entries:
        try:
            summoner = client.get_summoner_by_puuid(puuid)
            summoner_id = summoner.get("id")
            entries = (
                client.get_league_entries_by_summoner_id(summoner_id)
                if summoner_id
                else []
            )
            api_calls += 2 if summoner_id else 1
        except RiotApiNotFound:
            entries = []
            api_calls += 1

    profile_entry = next(
        (entry for entry in entries if entry.get("queueType") == SOLO_RANK_QUEUE_TYPE),
        None,
    )
    cache.save_ranked_profile(
        puuid=puuid,
        queue_id=queue_id,
        tier=profile_entry.get("tier") if profile_entry else None,
        rank=profile_entry.get("rank") if profile_entry else None,
        league_points=profile_entry.get("leaguePoints") if profile_entry else None,
        wins=profile_entry.get("wins") if profile_entry else None,
        losses=profile_entry.get("losses") if profile_entry else None,
    )
    return cache.get_ranked_profile(puuid, queue_id), 0, api_calls


def run_search(
    *,
    config: AppConfig,
    request: SearchRequest,
    champion_data: ChampionData,
    cache: MatchCache,
    progress_cb: ProgressCallback,
    status_cb: StatusCallback,
) -> SearchPayload:
    """입력 조건에 맞는 매치업 결과를 검색한다."""
    if not config.api_key:
        raise RiotApiError(
            "RIOT_API_KEY가 설정되어 있지 않습니다. .env 파일에 Riot API Key를 넣어주세요."
        )

    game_name, tag_line = parse_riot_id(request.riot_id_raw)

    my_champion_key = champion_data.to_english_key(request.my_champion_korean)
    enemy_champion_key = champion_data.to_english_key(request.enemy_champion_korean)
    if not my_champion_key or not enemy_champion_key:
        raise RiotApiError(
            "선택한 챔피언을 인식하지 못했습니다. 목록을 새로고침해주세요."
        )

    if request.lane_label not in LANE_LABEL_TO_TEAM_POSITION:
        raise RiotApiError("선택한 라인을 인식하지 못했습니다.")
    lane_value = LANE_LABEL_TO_TEAM_POSITION[request.lane_label]

    if request.period_kind in PERIOD_PRESETS:
        start_ts, end_ts = days_ago_to_unix_range(PERIOD_PRESETS[request.period_kind])
    else:
        if request.custom_start > request.custom_end:
            raise RiotApiError("시작일이 종료일보다 늦을 수 없습니다.")
        start_ts, end_ts = date_range_to_unix(
            datetime.combine(request.custom_start, datetime.min.time()),
            datetime.combine(request.custom_end, datetime.min.time()),
        )

    status_cb("Riot ID로 PUUID를 조회 중...")
    with RiotClient(api_key=config.api_key, region=config.region) as client:
        account = resolve_puuid(client, cache, game_name, tag_line)
        puuid = account["puuid"]

        status_cb("솔로 랭크 티어를 확인하는 중...")
        ranked_profile, rank_cache_hits, rank_api_calls = fetch_ranked_profile(
            client,
            cache,
            puuid,
            config.queue_id,
        )
        index_allowed = _is_index_allowed(ranked_profile)

        status_cb("매치 ID 목록을 불러오는 중...")
        match_ids = fetch_all_match_ids(
            client=client,
            puuid=puuid,
            queue_id=config.queue_id,
            start_ts=start_ts,
            end_ts=end_ts,
            max_total=request.max_matches,
        )

        total = len(match_ids)
        results: list[dict[str, Any]] = []
        cache_hits = rank_cache_hits
        api_calls = rank_api_calls
        indexed_rows = 0

        if total == 0:
            status_cb("이 기간에는 매치가 없습니다.")
            progress_cb(1.0)
        else:
            for idx, match_id in enumerate(match_ids, start=1):
                status_cb(f"매치 분석 중... ({idx}/{total})")
                cache.record_match_discovery(
                    match_id=match_id,
                    source_puuid=puuid,
                    source="manual_user",
                )

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

                if index_allowed:
                    indexed_rows += cache.index_match(
                        match,
                        allowed_queue_id=config.queue_id,
                    )

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

    results.sort(key=lambda row: row.get("game_creation") or 0, reverse=True)

    cache.record_search(
        target_riot_id=f"{account['game_name']}#{account['tag_line']}",
        target_puuid=puuid,
        queue_id=config.queue_id,
        my_champion=my_champion_key,
        enemy_champion=enemy_champion_key,
        lane=lane_value,
        start_time=start_ts,
        end_time=end_ts,
        result_count=len(results),
    )

    return SearchPayload(
        results=results,
        scanned_total=total,
        cache_hits=cache_hits,
        api_calls=api_calls,
        account=account,
        my_champion_key=my_champion_key,
        enemy_champion_key=enemy_champion_key,
        lane_label=request.lane_label,
        period_kind=request.period_kind,
        start_ts=start_ts,
        end_ts=end_ts,
        index_allowed=index_allowed,
        index_tier=(ranked_profile or {}).get("tier"),
        indexed_rows=indexed_rows,
    )
