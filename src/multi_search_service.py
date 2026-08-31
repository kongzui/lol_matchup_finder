"""여러 Riot ID를 직접 입력해 솔로 랭크 매치를 수집하는 서비스."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .cache import MatchCache, normalize_riot_id_key
from .config import AppConfig
from .riot_client import (
    RiotApiAuthError,
    RiotApiError,
    RiotApiNotFound,
    RiotApiRateLimited,
    RiotClient,
)
from .search_service import (
    collection_execution,
    ensure_match_indexed,
    fetch_all_match_ids,
    fetch_match_detail,
    resolve_puuid_with_count,
)
from .utils import days_ago_to_unix_range, parse_riot_id


DEFAULT_MULTI_SEARCH_DAYS = 30
DEFAULT_MULTI_MATCHES_PER_PLAYER = 100
MULTI_SEARCH_SOURCE = "manual_multi"
MULTI_PERIOD_OPTIONS: tuple[int, ...] = (1, 3, 7, 14, 30, 90, 180)
MULTI_MATCHES_PER_PLAYER_OPTIONS: tuple[int, ...] = (20, 50, 100, 200, 300)

ProgressCallback = Callable[[float], None]
StatusCallback = Callable[[str], None]


@dataclass(frozen=True)
class MultiSearchTarget:
    """이미 PUUID를 아는 수집 대상."""

    puuid: str
    game_name: str
    tag_line: str
    days: int
    matches_per_player: int

    @property
    def riot_id_raw(self) -> str:
        return f"{self.game_name}#{self.tag_line}"


@dataclass(frozen=True)
class MultiSearchRequest:
    """직접 입력 Riot ID 또는 이미 확인된 PUUID 수집 조건."""

    riot_ids_raw: str
    days: int
    matches_per_player: int
    days_by_riot_id: dict[str, int] = field(default_factory=dict)
    matches_per_player_by_riot_id: dict[str, int] = field(default_factory=dict)
    known_targets: tuple[MultiSearchTarget, ...] = ()


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
    continuation_advanced_count: int
    period_label: str
    matches_per_player: int
    start_ts: int
    end_ts: int
    resolved_users: list[dict[str, str]]
    memos: dict[str, str]
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
    return resolve_puuid_with_count(client, cache, game_name, tag_line)


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
    """여러 명 수집도 공용 수집 순서 안에서 실행한다."""
    with collection_execution(status_cb):
        return _run_multi_search_unlocked(
            config=config,
            request=request,
            cache=cache,
            progress_cb=progress_cb,
            status_cb=status_cb,
        )


def _run_multi_search_unlocked(
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
    input_count = len(request.known_targets) + len(riot_ids)
    if input_count == 0:
        raise RiotApiError("수집할 Riot ID를 한 명 이상 입력해주세요.")

    start_ts, end_ts = _request_time_range(request.days)
    cache_hits = 0
    api_calls = 0
    new_match_details = 0
    indexed_rows = 0
    continuation_advanced_count = 0
    success_count = 0
    failures: list[MultiSearchFailure] = []
    seen_match_ids: set[str] = set()
    continued_puuids: set[str] = set()

    with RiotClient(
        api_key=config.api_key,
        region=config.region,
        platform=config.platform,
    ) as client:
        resolved_accounts: list[dict[str, Any]] = []
        seen_puuids: set[str] = set()
        total_users = input_count

        for idx, target in enumerate(request.known_targets, start=1):
            progress_cb((idx - 1) / max(total_users, 1) * 0.25)
            status_cb(f"저장된 유저 확인 중... ({idx}/{total_users})")
            account = {
                "puuid": target.puuid,
                "game_name": target.game_name,
                "tag_line": target.tag_line,
                "riot_id_raw": target.riot_id_raw,
                "days": target.days,
                "matches_per_player": target.matches_per_player,
            }
            cache.save_manual_collection_user(
                puuid=account["puuid"],
                game_name=account["game_name"],
                tag_line=account["tag_line"],
            )
            cache.save_player_identity(
                puuid=account["puuid"],
                game_name=account["game_name"],
                tag_line=account["tag_line"],
            )
            seen_puuids.add(account["puuid"])
            resolved_accounts.append(account)

        for raw_idx, riot_id_raw in enumerate(riot_ids, start=1):
            idx = len(request.known_targets) + raw_idx
            progress_cb((idx - 1) / max(total_users, 1) * 0.25)
            status_cb(f"Riot ID 확인 중... ({idx}/{total_users})")

            try:
                game_name, tag_line = parse_riot_id(riot_id_raw)
                riot_id_key = normalize_riot_id_key(game_name, tag_line)
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
            account["riot_id_raw"] = riot_id_raw
            account["days"] = request.days_by_riot_id.get(
                riot_id_key,
                request.days,
            )
            account["matches_per_player"] = request.matches_per_player_by_riot_id.get(
                riot_id_key,
                request.matches_per_player,
            )
            if account["puuid"] in seen_puuids:
                continue
            cache.save_manual_collection_user(
                puuid=account["puuid"],
                game_name=account["game_name"],
                tag_line=account["tag_line"],
            )
            cache.save_player_identity(
                puuid=account["puuid"],
                game_name=account["game_name"],
                tag_line=account["tag_line"],
            )
            seen_puuids.add(account["puuid"])
            resolved_accounts.append(account)

        total_resolved = len(resolved_accounts)
        match_ids_by_puuid: dict[str, list[str]] = {}
        truncated_puuids: set[str] = set()
        match_ids_to_fetch: list[str] = []
        for idx, account in enumerate(resolved_accounts, start=1):
            progress_cb(0.25 + (idx - 1) / max(total_resolved, 1) * 0.35)
            status_cb(f"솔로 랭크 경기 목록 확인 중... ({idx}/{total_resolved})")

            default_start_ts = end_ts - account["days"] * 24 * 60 * 60
            cursor = cache.get_manual_collection_cursor(account["puuid"])
            cursor_matches_context = bool(
                cursor
                and cursor.get("queue_id") == config.queue_id
                and cursor.get("region") == config.region
                and cursor.get("matches_per_player")
            )
            if (
                cursor_matches_context
                and cursor["start_at"] < cursor["end_at"] <= cursor["target_at"]
            ):
                account_start_ts = cursor["start_at"]
                account_end_ts = cursor["end_at"]
                account_target_ts = cursor["target_at"]
                account_offset = max(cursor.get("offset", 0), 0)
                account["matches_per_player"] = int(cursor["matches_per_player"])
                continued_puuids.add(account["puuid"])
            else:
                if cursor:
                    cache.clear_manual_collection_cursor(account["puuid"])
                account_start_ts = default_start_ts
                account_end_ts = end_ts
                account_target_ts = end_ts
                account_offset = 0
            account["collection_start_ts"] = account_start_ts
            account["collection_end_ts"] = account_end_ts
            account["collection_target_ts"] = account_target_ts
            account["collection_offset"] = account_offset
            matches_per_player = account["matches_per_player"]
            try:
                match_ids = fetch_all_match_ids(
                    client=client,
                    puuid=account["puuid"],
                    queue_id=config.queue_id,
                    start_ts=account_start_ts,
                    end_ts=account_end_ts,
                    max_total=matches_per_player + 1,
                    start_offset=account_offset,
                )
            except (RiotApiAuthError, RiotApiRateLimited):
                raise
            except RiotApiError as exc:
                failures.append(
                    MultiSearchFailure(
                        account["riot_id_raw"],
                        f"경기 목록을 확인하지 못했습니다: {exc}",
                    )
                )
                continue

            api_calls += 1
            time.sleep(0.05)
            if len(match_ids) > matches_per_player:
                truncated_puuids.add(account["puuid"])
                match_ids = match_ids[:matches_per_player]
            match_ids_by_puuid[account["puuid"]] = match_ids

            for match_id in match_ids:
                cache.record_match_discovery(
                    match_id=match_id,
                    source_puuid=account["puuid"],
                    source=MULTI_SEARCH_SOURCE,
                )
                if match_id not in seen_match_ids:
                    seen_match_ids.add(match_id)
                    match_ids_to_fetch.append(match_id)

        total_matches = len(match_ids_to_fetch)
        indexed_match_ids = cache.get_indexed_match_ids(match_ids_to_fetch)
        failed_match_reasons: dict[str, str] = {}
        for idx, match_id in enumerate(match_ids_to_fetch, start=1):
            progress_cb(0.60 + (idx / max(total_matches, 1)) * 0.35)
            status_cb(f"경기 내용을 저장하는 중... ({idx}/{total_matches})")

            cached_match = cache.get_match(match_id)
            cached_before = cached_match is not None
            try:
                match = fetch_match_detail(client, cache, match_id, cached_match)
            except (RiotApiAuthError, RiotApiRateLimited):
                raise
            except RiotApiError as exc:
                failed_match_reasons[match_id] = (
                    f"매치 상세 저장 중 Riot API 오류가 발생했습니다: {exc}"
                )
                continue

            if match is None:
                failed_match_reasons[match_id] = "매치 상세를 찾지 못했습니다."
                continue

            if cached_before:
                cache_hits += 1
            else:
                api_calls += 1
                new_match_details += 1
                time.sleep(0.05)

            if match_id not in indexed_match_ids:
                match_indexed_rows, index_available = ensure_match_indexed(
                    cache,
                    match,
                    match_id=match_id,
                    queue_id=config.queue_id,
                )
                if not index_available:
                    failed_match_reasons[match_id] = (
                        "매치 상세를 조회용 데이터로 저장하지 못했습니다."
                    )
                    continue
                indexed_rows += match_indexed_rows

        for account in resolved_accounts:
            match_ids = match_ids_by_puuid.get(account["puuid"])
            if match_ids is None:
                continue

            incomplete_reasons: list[str] = []
            failed_match_ids = [
                match_id for match_id in match_ids if match_id in failed_match_reasons
            ]
            if failed_match_ids:
                first_reason = failed_match_reasons[failed_match_ids[0]]
                incomplete_reasons.append(
                    f"경기 {len(failed_match_ids)}건을 끝까지 저장하지 "
                    f"못했습니다. {first_reason}"
                )

            is_truncated = account["puuid"] in truncated_puuids
            if is_truncated and not failed_match_ids:
                next_offset = account["collection_offset"] + len(match_ids)
                cache.save_manual_collection_cursor(
                    puuid=account["puuid"],
                    start_at=account["collection_start_ts"],
                    end_at=account["collection_end_ts"],
                    target_at=account["collection_target_ts"],
                    offset=next_offset,
                    queue_id=config.queue_id,
                    region=config.region,
                    matches_per_player=account["matches_per_player"],
                )
                continuation_advanced_count += 1
                incomplete_reasons.append(
                    f"설정한 최대 {account['matches_per_player']}판보다 "
                    "경기가 더 많습니다. 이번 묶음은 저장했고, 다시 수집하면 "
                    "그다음 경기부터 이어서 확인합니다."
                )

            if incomplete_reasons:
                if failed_match_ids:
                    cache.save_manual_collection_cursor(
                        puuid=account["puuid"],
                        start_at=account["collection_start_ts"],
                        end_at=account["collection_end_ts"],
                        target_at=account["collection_target_ts"],
                        offset=account["collection_offset"],
                        queue_id=config.queue_id,
                        region=config.region,
                        matches_per_player=account["matches_per_player"],
                    )
                failures.append(
                    MultiSearchFailure(
                        account["riot_id_raw"],
                        " ".join(incomplete_reasons),
                    )
                )
                continue

            cache.mark_manual_collection_user_collected(
                puuid=account["puuid"],
                collected_at=account["collection_target_ts"],
            )
            success_count += 1

        api_calls = int(getattr(client, "request_count", api_calls))

    actual_start_ts = min(
        (
            int(account["collection_start_ts"])
            for account in resolved_accounts
            if "collection_start_ts" in account
        ),
        default=start_ts,
    )
    actual_end_ts = max(
        (
            int(account["collection_end_ts"])
            for account in resolved_accounts
            if "collection_end_ts" in account
        ),
        default=end_ts,
    )
    actual_matches_per_player = max(
        (int(account["matches_per_player"]) for account in resolved_accounts),
        default=request.matches_per_player,
    )
    if continued_puuids:
        actual_max_days = max(
            (
                max(
                    (
                        int(account["collection_end_ts"])
                        - int(account["collection_start_ts"])
                        + 86_399
                    )
                    // 86_400,
                    1,
                )
                for account in resolved_accounts
                if "collection_start_ts" in account
            ),
            default=request.days,
        )
        period_label = (
            f"이전 수집 이어받기 {len(continued_puuids)}명 포함 · "
            f"실제 최대 {actual_max_days}일"
        )
    else:
        period_label = (
            f"대상별 최근 최대 {request.days}일"
            if request.known_targets or request.days_by_riot_id
            else f"최근 {request.days}일"
        )

    progress_cb(1.0)
    return MultiSearchPayload(
        input_count=input_count,
        success_count=success_count,
        failure_count=len(failures),
        discovered_matches=len(seen_match_ids),
        new_match_details=new_match_details,
        cache_hits=cache_hits,
        api_calls=api_calls,
        indexed_rows=indexed_rows,
        continuation_advanced_count=continuation_advanced_count,
        period_label=period_label,
        matches_per_player=actual_matches_per_player,
        start_ts=actual_start_ts,
        end_ts=actual_end_ts,
        resolved_users=[
            {
                "puuid": account["puuid"],
                "riot_id": f"{account['game_name']}#{account['tag_line']}",
            }
            for account in resolved_accounts
        ],
        memos=cache.get_player_memos(
            [account["puuid"] for account in resolved_accounts]
        ),
        failures=failures,
    )
