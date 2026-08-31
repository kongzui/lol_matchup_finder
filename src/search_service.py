"""매치업 검색 흐름을 담당하는 서비스."""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from threading import Lock
from typing import Any, cast

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
_MATCH_FETCH_LOCKS = tuple(Lock() for _ in range(64))
_ACCOUNT_FETCH_LOCKS = tuple(Lock() for _ in range(32))
_TIMELINE_FETCH_LOCKS = tuple(Lock() for _ in range(64))
_RANK_FETCH_LOCKS = tuple(Lock() for _ in range(64))
_MATCH_INDEX_LOCKS = tuple(Lock() for _ in range(64))
_MATCH_NOT_FOUND_UNTIL = tuple({} for _ in _MATCH_FETCH_LOCKS)
_TIMELINE_NOT_FOUND_UNTIL = tuple({} for _ in _TIMELINE_FETCH_LOCKS)
_MATCH_CACHE_NOT_CHECKED = object()
_COLLECTION_EXECUTION_LOCK = Lock()
NOT_FOUND_RETRY_SECONDS = 30.0

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
SEARCH_ALL_LABEL = "전체"
SEARCH_ENEMY_ALL_LABEL = SEARCH_ALL_LABEL
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
    my_champion_key: str | None
    enemy_champion_key: str | None
    lane_label: str
    period_kind: str
    start_ts: int
    end_ts: int
    index_allowed: bool
    index_tier: str | None
    indexed_rows: int
    memos: dict[str, str]


def resolve_puuid(
    client: RiotClient,
    cache: MatchCache,
    game_name: str,
    tag_line: str,
) -> dict[str, Any]:
    """Riot ID로 PUUID를 조회하되 캐시를 먼저 사용한다."""
    account, _cache_hits, _api_calls = resolve_puuid_with_count(
        client,
        cache,
        game_name,
        tag_line,
    )
    return account


def resolve_puuid_with_count(
    client: RiotClient,
    cache: MatchCache,
    game_name: str,
    tag_line: str,
) -> tuple[dict[str, Any], int, int]:
    """동시 작업이 같은 Riot ID를 한 번만 확인하도록 PUUID를 조회한다."""
    cache_key = normalize_riot_id_key(game_name, tag_line)
    cached = cache.get_account(cache_key)
    if cached is not None:
        return (
            {
                "puuid": cached["puuid"],
                "game_name": cached["game_name"] or game_name,
                "tag_line": cached["tag_line"] or tag_line,
            },
            1,
            0,
        )

    fetch_lock = _ACCOUNT_FETCH_LOCKS[hash(cache_key) % len(_ACCOUNT_FETCH_LOCKS)]
    with fetch_lock:
        cached = cache.get_account(cache_key)
        if cached is not None:
            return (
                {
                    "puuid": cached["puuid"],
                    "game_name": cached["game_name"] or game_name,
                    "tag_line": cached["tag_line"] or tag_line,
                },
                1,
                0,
            )

        account = client.get_account_by_riot_id(game_name, tag_line)
        puuid = account.get("puuid")
        if not puuid:
            raise RiotApiError("Account API 응답에 puuid가 없습니다.")

        resolved = {
            "puuid": puuid,
            "game_name": account.get("gameName") or game_name,
            "tag_line": account.get("tagLine") or tag_line,
        }
        cache.save_account(
            cache_key,
            puuid=puuid,
            game_name=resolved["game_name"],
            tag_line=resolved["tag_line"],
        )
        return resolved, 0, 1


def fetch_all_match_ids(
    client: RiotClient,
    puuid: str,
    queue_id: int,
    start_ts: int,
    end_ts: int,
    max_total: int,
    start_offset: int = 0,
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
            start=start_offset + page_index * PAGE_SIZE,
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
    cached_match: dict[str, Any] | None | object = _MATCH_CACHE_NOT_CHECKED,
) -> dict[str, Any] | None:
    """matchId로 상세 데이터를 가져오되 캐시를 먼저 사용한다."""
    cached = (
        cache.get_match(match_id)
        if cached_match is _MATCH_CACHE_NOT_CHECKED
        else cast(dict[str, Any] | None, cached_match)
    )
    if cached is not None:
        return cached

    lock_index = hash(match_id) % len(_MATCH_FETCH_LOCKS)
    fetch_lock = _MATCH_FETCH_LOCKS[lock_index]
    not_found_until = _MATCH_NOT_FOUND_UNTIL[lock_index]
    with fetch_lock:
        cached = cache.get_match(match_id)
        if cached is not None:
            return cached
        retry_at = not_found_until.get(match_id)
        if retry_at is not None:
            if retry_at > time.monotonic():
                return None
            not_found_until.pop(match_id, None)

        try:
            match = client.get_match_by_id(match_id)
        except RiotApiNotFound:
            not_found_until[match_id] = time.monotonic() + NOT_FOUND_RETRY_SECONDS
            return None

        cache.save_match(match_id, match)
        not_found_until.pop(match_id, None)
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

    lock_index = hash(match_id) % len(_TIMELINE_FETCH_LOCKS)
    fetch_lock = _TIMELINE_FETCH_LOCKS[lock_index]
    not_found_until = _TIMELINE_NOT_FOUND_UNTIL[lock_index]
    with fetch_lock:
        cached = cache.get_match_timeline(match_id)
        if cached is not None:
            return cached
        retry_at = not_found_until.get(match_id)
        if retry_at is not None:
            if retry_at > time.monotonic():
                return None
            not_found_until.pop(match_id, None)

        try:
            timeline = client.get_match_timeline_by_id(match_id)
        except RiotApiNotFound:
            not_found_until[match_id] = time.monotonic() + NOT_FOUND_RETRY_SECONDS
            return None

        cache.save_match_timeline(match_id, timeline)
        not_found_until.pop(match_id, None)
        return timeline


def ensure_match_indexed(
    cache: MatchCache,
    match: dict[str, Any],
    *,
    match_id: str,
    queue_id: int,
) -> tuple[int, bool]:
    """동시 작업도 같은 경기 인덱스를 한 번만 만들고 사용 가능 여부를 반환한다."""
    index_lock = _MATCH_INDEX_LOCKS[hash(match_id) % len(_MATCH_INDEX_LOCKS)]
    with index_lock:
        if match_id in cache.get_indexed_match_ids([match_id]):
            return 0, True
        indexed_rows = cache.index_match(match, allowed_queue_id=queue_id)
        return indexed_rows, indexed_rows > 0


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
    if cached is not None:
        return cached, 1, 0

    rank_key = f"{puuid}:{queue_id}"
    fetch_lock = _RANK_FETCH_LOCKS[hash(rank_key) % len(_RANK_FETCH_LOCKS)]
    with fetch_lock:
        cached = cache.get_ranked_profile(puuid, queue_id)
        if cached is not None:
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
            (
                entry
                for entry in entries
                if entry.get("queueType") == SOLO_RANK_QUEUE_TYPE
            ),
            None,
        )
        cache.save_ranked_profile(
            puuid=puuid,
            queue_id=queue_id,
            tier=profile_entry.get("tier") if profile_entry else None,
            rank=profile_entry.get("rank") if profile_entry else None,
            league_points=(
                profile_entry.get("leaguePoints") if profile_entry else None
            ),
            wins=profile_entry.get("wins") if profile_entry else None,
            losses=profile_entry.get("losses") if profile_entry else None,
        )
        return cache.get_ranked_profile(puuid, queue_id), 0, api_calls


@contextmanager
def collection_execution(status_cb: StatusCallback):
    """Riot 경기 목록을 다루는 수집 작업을 서버에서 한 번에 하나만 실행한다."""
    acquired = _COLLECTION_EXECUTION_LOCK.acquire(blocking=False)
    if not acquired:
        status_cb("다른 경기 데이터 작업이 끝나길 기다리는 중...")
        _COLLECTION_EXECUTION_LOCK.acquire()
    try:
        yield
    finally:
        _COLLECTION_EXECUTION_LOCK.release()


def run_search(
    *,
    config: AppConfig,
    request: SearchRequest,
    champion_data: ChampionData,
    cache: MatchCache,
    progress_cb: ProgressCallback,
    status_cb: StatusCallback,
) -> SearchPayload:
    """개별 검색도 공용 수집 순서를 사용해 다른 수집과 상태가 겹치지 않게 한다."""
    with collection_execution(status_cb):
        return _run_search_unlocked(
            config=config,
            request=request,
            champion_data=champion_data,
            cache=cache,
            progress_cb=progress_cb,
            status_cb=status_cb,
        )


def _run_search_unlocked(
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

    my_champion_key: str | None = None
    if request.my_champion_korean != SEARCH_ALL_LABEL:
        my_champion_key = champion_data.to_english_key(request.my_champion_korean)
        if not my_champion_key:
            raise RiotApiError("선택한 기준 챔피언을 인식하지 못했습니다.")

    enemy_champion_key = None
    if request.enemy_champion_korean != SEARCH_ENEMY_ALL_LABEL:
        enemy_champion_key = champion_data.to_english_key(request.enemy_champion_korean)
        if not enemy_champion_key:
            raise RiotApiError("선택한 상대 챔피언을 인식하지 못했습니다.")

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

    status_cb("유저 정보를 확인하는 중...")
    with RiotClient(api_key=config.api_key, region=config.region) as client:
        account, account_cache_hits, _account_api_calls = resolve_puuid_with_count(
            client,
            cache,
            game_name,
            tag_line,
        )
        puuid = account["puuid"]
        cache.save_player_identity(
            puuid=puuid,
            game_name=account["game_name"],
            tag_line=account["tag_line"],
        )

        status_cb("솔로 랭크 티어를 확인하는 중...")
        ranked_profile, rank_cache_hits, rank_api_calls = fetch_ranked_profile(
            client,
            cache,
            puuid,
            config.queue_id,
        )
        index_allowed = _is_index_allowed(ranked_profile)

        status_cb("솔로 랭크 경기 목록을 확인하는 중...")
        match_ids = fetch_all_match_ids(
            client=client,
            puuid=puuid,
            queue_id=config.queue_id,
            start_ts=start_ts,
            end_ts=end_ts,
            max_total=request.max_matches,
        )

        total = len(match_ids)
        indexed_match_ids = (
            cache.get_indexed_match_ids(match_ids) if index_allowed else set()
        )
        results: list[dict[str, Any]] = []
        cache_hits = rank_cache_hits + account_cache_hits
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
                cached_match = cache.get_match(match_id)
                cached_before = cached_match is not None
                match = fetch_match_detail(client, cache, match_id, cached_match)
                if match is None:
                    progress_cb(idx / total)
                    continue

                if cached_before:
                    cache_hits += 1
                else:
                    api_calls += 1
                    # 같은 1초에 너무 많이 부르지 않도록 가벼운 페이싱.
                    time.sleep(0.05)

                if index_allowed and match_id not in indexed_match_ids:
                    added_rows, _available = ensure_match_indexed(
                        cache,
                        match,
                        match_id=match_id,
                        queue_id=config.queue_id,
                    )
                    indexed_rows += added_rows

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

        api_calls = int(getattr(client, "request_count", api_calls))

    results.sort(key=lambda row: row.get("game_creation") or 0, reverse=True)
    memo_puuids = [puuid]
    memo_puuids.extend(row["enemy_puuid"] for row in results if row.get("enemy_puuid"))

    cache.record_search(
        target_riot_id=f"{account['game_name']}#{account['tag_line']}",
        target_puuid=puuid,
        queue_id=config.queue_id,
        my_champion=my_champion_key or SEARCH_ALL_LABEL,
        enemy_champion=enemy_champion_key or SEARCH_ENEMY_ALL_LABEL,
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
        memos=cache.get_player_memos(memo_puuids),
    )
