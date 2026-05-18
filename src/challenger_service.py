"""챌린저 랭킹 기반 매치업 검색 서비스."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .cache import MatchCache
from .config import AppConfig
from .riot_client import RiotApiError, RiotApiNotFound, RiotClient
from .search_service import fetch_all_match_ids, fetch_match_detail
from .utils import days_ago_to_unix_range


DEFAULT_CHALLENGER_TOP_N = 300
DEFAULT_CHALLENGER_DAYS = 7
DEFAULT_MATCHES_PER_PLAYER = 50
CHALLENGER_QUEUE = "RANKED_SOLO_5x5"

ProgressCallback = Callable[[float], None]
StatusCallback = Callable[[str], None]


@dataclass(frozen=True)
class ChallengerSearchRequest:
    """챌린저 데이터 수집 조건."""

    top_n: int
    days: int
    matches_per_player: int


@dataclass(frozen=True)
class ChallengerSearchPayload:
    """챌린저 수집 결과와 렌더링에 필요한 부가 정보."""

    results: list[dict[str, Any]]
    scanned_players: int
    scanned_matches: int
    new_match_details: int
    cache_hits: int
    api_calls: int
    period_label: str
    top_n: int
    matches_per_player: int
    start_ts: int
    end_ts: int
    indexed_rows: int
    new_challengers: int
    reactivated_challengers: int
    deactivated_challengers: int


def _sort_challenger_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """챌린저 엔트리를 래더 표시용 순서로 정렬한다."""
    return sorted(
        entries,
        key=lambda row: (
            -int(row.get("leaguePoints") or 0),
            -int(row.get("wins") or 0),
            int(row.get("losses") or 0),
        ),
    )


def _resolve_summoner_puuid(
    client: RiotClient,
    cache: MatchCache,
    summoner_id: str,
) -> tuple[str | None, bool]:
    """summonerId로 PUUID를 조회하고 캐시 히트 여부를 함께 반환한다."""
    cached = cache.get_summoner_puuid(summoner_id)
    if cached:
        return cached, True

    try:
        summoner = client.get_summoner_by_id(summoner_id)
    except RiotApiNotFound:
        return None, False

    puuid = summoner.get("puuid")
    if not puuid:
        return None, False

    cache.save_summoner_puuid(summoner_id, puuid)
    return puuid, False


def run_challenger_search(
    *,
    config: AppConfig,
    request: ChallengerSearchRequest,
    cache: MatchCache,
    progress_cb: ProgressCallback,
    status_cb: StatusCallback,
) -> ChallengerSearchPayload:
    """챌린저 상위권의 최근 경기를 공통 DB에 수집한다."""
    if not config.api_key:
        raise RiotApiError(
            "RIOT_API_KEY가 설정되어 있지 않습니다. .env 파일에 Riot API Key를 넣어주세요."
        )

    start_ts, end_ts = days_ago_to_unix_range(request.days)

    cache_hits = 0
    api_calls = 0
    indexed_rows = 0
    new_match_details = 0
    snapshot_stats = {
        "new_count": 0,
        "reactivated_count": 0,
        "deactivated_count": 0,
    }

    status_cb("챌린저 랭킹을 불러오는 중...")
    with RiotClient(
        api_key=config.api_key,
        region=config.region,
        platform=config.platform,
    ) as client:
        league = client.get_challenger_league(CHALLENGER_QUEUE)
        api_calls += 1
        entries = league.get("entries") or []
        if not isinstance(entries, list):
            raise RiotApiError("챌린저 랭킹 응답이 비정상입니다.")

        ranked_entries = _sort_challenger_entries(entries)[: request.top_n]
        ranked_players: list[dict[str, Any]] = []
        match_ids: list[str] = []
        seen_match_ids: set[str] = set()

        total_players = len(ranked_entries)
        for idx, entry in enumerate(ranked_entries, start=1):
            progress_cb((idx - 1) / max(total_players, 1) * 0.25)
            status_cb(f"챌린저 PUUID 정리 중... ({idx}/{total_players})")

            puuid = entry.get("puuid")
            if not puuid:
                summoner_id = entry.get("summonerId")
                if not summoner_id:
                    continue

                puuid, hit = _resolve_summoner_puuid(client, cache, summoner_id)
                if hit:
                    cache_hits += 1
                else:
                    api_calls += 1
                    time.sleep(0.05)

            if not puuid:
                continue

            ranked_players.append(
                {
                    "puuid": puuid,
                    "rank": idx,
                    "league_points": int(entry.get("leaguePoints") or 0),
                    "wins": int(entry.get("wins") or 0),
                    "losses": int(entry.get("losses") or 0),
                    "fallback_name": entry.get("summonerName"),
                }
            )

        snapshot_stats = cache.save_challenger_snapshot(
            queue=CHALLENGER_QUEUE,
            top_n=request.top_n,
            players=ranked_players,
        )

        seeds = cache.get_active_collection_seeds("challenger")
        total_seeds = len(seeds)
        for idx, seed in enumerate(seeds, start=1):
            progress_cb(0.25 + (idx - 1) / max(total_seeds, 1) * 0.35)
            status_cb(f"챌린저 seed 최근 경기 수집 중... ({idx}/{total_seeds})")

            seed_start_ts = start_ts
            if seed.get("last_collected_at"):
                seed_start_ts = max(seed_start_ts, int(seed["last_collected_at"]))

            ids = fetch_all_match_ids(
                client=client,
                puuid=seed["puuid"],
                queue_id=config.queue_id,
                start_ts=seed_start_ts,
                end_ts=end_ts,
                max_total=request.matches_per_player,
            )
            api_calls += 1
            time.sleep(0.05)
            cache.mark_seed_collected(
                puuid=seed["puuid"],
                source="challenger",
                collected_at=end_ts,
            )

            for match_id in ids:
                cache.record_match_discovery(
                    match_id=match_id,
                    source_puuid=seed["puuid"],
                    source="challenger",
                )
                if match_id in seen_match_ids:
                    continue
                seen_match_ids.add(match_id)
                match_ids.append(match_id)

        total_matches = len(match_ids)
        for idx, match_id in enumerate(match_ids, start=1):
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

    return ChallengerSearchPayload(
        results=[],
        scanned_players=len(ranked_players),
        scanned_matches=len(match_ids),
        new_match_details=new_match_details,
        cache_hits=cache_hits,
        api_calls=api_calls,
        period_label=f"최근 {request.days}일",
        top_n=request.top_n,
        matches_per_player=request.matches_per_player,
        start_ts=start_ts,
        end_ts=end_ts,
        indexed_rows=indexed_rows,
        new_challengers=snapshot_stats["new_count"],
        reactivated_challengers=snapshot_stats["reactivated_count"],
        deactivated_challengers=snapshot_stats["deactivated_count"],
    )
