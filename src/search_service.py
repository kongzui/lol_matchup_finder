"""매치업 검색 흐름을 담당하는 서비스."""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import contextmanager
from threading import Lock
from typing import Any, cast

from .cache import MatchCache, normalize_riot_id_key
from .riot_client import RiotApiError, RiotApiNotFound, RiotClient


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
SEARCH_ALL_LABEL = "전체"
SOLO_RANK_QUEUE_TYPE = "RANKED_SOLO_5x5"
ProgressCallback = Callable[[float], None]
StatusCallback = Callable[[str], None]


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


def index_memo_player_matches(
    cache: MatchCache,
    queue_id: int,
    puuid: str | None = None,
) -> int:
    """관심 유저의 저장 경기는 티어와 관계없이 조회 항목으로 채운다."""
    indexed_rows = 0
    for match_id in cache.list_unindexed_memo_match_ids(puuid):
        match = cache.get_match(match_id)
        if match is None:
            continue
        added_rows, _available = ensure_match_indexed(
            cache,
            match,
            match_id=match_id,
            queue_id=queue_id,
        )
        indexed_rows += added_rows
    return indexed_rows


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
