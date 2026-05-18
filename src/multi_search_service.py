"""여러 Riot ID를 직접 입력해 솔로 랭크 매치를 수집하는 서비스."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .cache import MatchCache, normalize_riot_id_key
from .config import AppConfig
from .riot_client import RiotApiError, RiotApiNotFound, RiotClient
from .search_service import fetch_all_match_ids, fetch_match_detail
from .utils import days_ago_to_unix_range, parse_riot_id


DEFAULT_MULTI_SEARCH_DAYS = 3
DEFAULT_MULTI_MATCHES_PER_PLAYER = 20
MULTI_SEARCH_SOURCE = "manual_multi"
MULTI_PERIOD_OPTIONS: tuple[int, ...] = (3, 7, 14, 30)
MULTI_MATCHES_PER_PLAYER_OPTIONS: tuple[int, ...] = (20, 50, 100)

ProgressCallback = Callable[[float], None]
StatusCallback = Callable[[str], None]


@dataclass(frozen=True)
class MultiSearchRequest:
    """멀티서치 수집 입력 조건."""

    riot_ids_raw: str
    days: int
    matches_per_player: int


@dataclass(frozen=True)
class MultiSearchFailure:
    """멀티서치에서 처리하지 못한 Riot ID."""

    riot_id_raw: str
    reason: str


@dataclass(frozen=True)
class MultiSearchPayload:
    """멀티서치 수집 결과와 렌더링에 필요한 부가 정보."""

    input_count: int
    success_count: int
    failure_count: int
    discovered_matches: int
    new_match_details: int
    cache_hits: int
    api_calls: int
    indexed_rows: int
    period_label: str
    matches_per_player: int
    start_ts: int
    end_ts: int
    failures: list[MultiSearchFailure]


def parse_multi_riot_ids(raw: str) -> list[str]:
    """줄바꿈 또는 쉼표로 구분된 Riot ID 목록을 중복 제거해 반환한다."""
    candidates = [part.strip() for part in re.split(r"[\n,]+", raw or "")]
    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _resolve_puuid_with_count(
    client: RiotClient,
    cache: MatchCache,
    game_name: str,
    tag_line: str,
) -> tuple[dict[str, Any], int, int]:
    """Riot ID로 PUUID를 조회하고 (account, cache_hits, api_calls)를 반환한다."""
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
    return (
        {
            "puuid": puuid,
            "game_name": account.get("gameName") or game_name,
            "tag_line": account.get("tagLine") or tag_line,
        },
        0,
        1,
    )


def _request_time_range(days: int) -> tuple[int, int]:
    return days_ago_to_unix_range(days)


def run_multi_search(
    *,
    config: AppConfig,
    request: MultiSearchRequest,
    cache: MatchCache,
    progress_cb: ProgressCallback,
    status_cb: StatusCallback,
) -> MultiSearchPayload:
    """입력한 Riot ID들의 솔로 랭크 매치를 수집하고 공통 DB에 인덱싱한다."""
    if not config.api_key:
        raise RiotApiError(
            "RIOT_API_KEY가 설정되어 있지 않습니다. .env 파일에 Riot API Key를 넣어주세요."
        )

    riot_ids = parse_multi_riot_ids(request.riot_ids_raw)
    if not riot_ids:
        raise RiotApiError("수집할 Riot ID를 한 명 이상 입력해주세요.")

    start_ts, end_ts = _request_time_range(request.days)
    cache_hits = 0
    api_calls = 0
    new_match_details = 0
    indexed_rows = 0
    success_count = 0
    failures: list[MultiSearchFailure] = []
    seen_match_ids: set[str] = set()

    with RiotClient(
        api_key=config.api_key,
        region=config.region,
        platform=config.platform,
    ) as client:
        resolved_accounts: list[dict[str, Any]] = []
        total_users = len(riot_ids)
        for idx, riot_id_raw in enumerate(riot_ids, start=1):
            progress_cb((idx - 1) / max(total_users, 1) * 0.25)
            status_cb(f"Riot ID 확인 중... ({idx}/{total_users})")

            try:
                game_name, tag_line = parse_riot_id(riot_id_raw)
                account, hit, calls = _resolve_puuid_with_count(
                    client,
                    cache,
                    game_name,
                    tag_line,
                )
            except ValueError as exc:
                failures.append(MultiSearchFailure(riot_id_raw, str(exc)))
                continue
            except RiotApiNotFound as exc:
                failures.append(MultiSearchFailure(riot_id_raw, str(exc)))
                continue

            cache_hits += hit
            api_calls += calls
            cache.save_manual_collection_user(
                puuid=account["puuid"],
                game_name=account["game_name"],
                tag_line=account["tag_line"],
            )
            resolved_accounts.append(account)

        total_resolved = len(resolved_accounts)
        for idx, account in enumerate(resolved_accounts, start=1):
            progress_cb(0.25 + (idx - 1) / max(total_resolved, 1) * 0.35)
            status_cb(f"솔로 랭크 matchId 수집 중... ({idx}/{total_resolved})")

            match_ids = fetch_all_match_ids(
                client=client,
                puuid=account["puuid"],
                queue_id=config.queue_id,
                start_ts=start_ts,
                end_ts=end_ts,
                max_total=request.matches_per_player,
            )
            api_calls += 1
            success_count += 1
            cache.mark_manual_collection_user_collected(
                puuid=account["puuid"],
                collected_at=end_ts,
            )
            time.sleep(0.05)

            for match_id in match_ids:
                cache.record_match_discovery(
                    match_id=match_id,
                    source_puuid=account["puuid"],
                    source=MULTI_SEARCH_SOURCE,
                )
                seen_match_ids.add(match_id)

        match_ids_to_fetch = list(seen_match_ids)
        total_matches = len(match_ids_to_fetch)
        for idx, match_id in enumerate(match_ids_to_fetch, start=1):
            progress_cb(0.60 + (idx / max(total_matches, 1)) * 0.35)
            status_cb(f"매치 상세 적재/인덱싱 중... ({idx}/{total_matches})")

            cached_before = cache.get_match(match_id) is not None
            match = fetch_match_detail(client, cache, match_id)
            if match is None:
                continue

            if cached_before:
                cache_hits += 1
            else:
                api_calls += 1
                new_match_details += 1
                time.sleep(0.05)

            indexed_rows += cache.index_match(match, allowed_queue_id=config.queue_id)

    progress_cb(1.0)
    return MultiSearchPayload(
        input_count=len(riot_ids),
        success_count=success_count,
        failure_count=len(failures),
        discovered_matches=len(seen_match_ids),
        new_match_details=new_match_details,
        cache_hits=cache_hits,
        api_calls=api_calls,
        indexed_rows=indexed_rows,
        period_label=f"최근 {request.days}일",
        matches_per_player=request.matches_per_player,
        start_ts=start_ts,
        end_ts=end_ts,
        failures=failures,
    )
