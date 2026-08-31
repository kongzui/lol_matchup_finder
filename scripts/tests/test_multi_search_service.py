"""여러 유저 수집의 사람별 완료 처리를 검증한다."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.multi_search_service import (
    MultiSearchRequest,
    MultiSearchTarget,
    run_multi_search,
)
from src.riot_client import RiotApiAuthError, RiotApiError, RiotApiRateLimited


class _FakeRiotClient:
    """외부 연결 없이 컨텍스트 관리자 동작만 제공한다."""

    def __init__(self, **_kwargs: object) -> None:
        pass

    def __enter__(self) -> "_FakeRiotClient":
        return self

    def __exit__(self, *_args: object) -> None:
        pass


class _RecordingCache:
    """수집 완료 기록 순서를 확인하는 최소 캐시 대역이다."""

    def __init__(
        self,
        *,
        indexed_rows: int = 10,
        existing_index_ids: set[str] | None = None,
    ) -> None:
        self.collected: dict[str, int] = {}
        self.cursors: dict[str, dict[str, object]] = {}
        self.events: list[str] = []
        self.indexed_rows = indexed_rows
        self.existing_index_ids = existing_index_ids or set()

    def save_manual_collection_user(self, **_kwargs: object) -> None:
        pass

    def save_player_identity(self, **_kwargs: object) -> None:
        pass

    def record_match_discovery(self, **_kwargs: object) -> None:
        pass

    def get_match(self, _match_id: str) -> None:
        return None

    def index_match(self, match: dict[str, object], **_kwargs: object) -> int:
        self.events.append(f"index:{match['match_id']}")
        return self.indexed_rows

    def get_indexed_match_ids(self, match_ids: list[str]) -> set[str]:
        return set(match_ids) & self.existing_index_ids

    def mark_manual_collection_user_collected(
        self,
        *,
        puuid: str,
        collected_at: int,
    ) -> None:
        self.events.append(f"collected:{puuid}")
        self.collected[puuid] = collected_at
        self.cursors.pop(puuid, None)

    def get_manual_collection_cursor(self, puuid: str) -> dict[str, object] | None:
        return self.cursors.get(puuid)

    def save_manual_collection_cursor(
        self,
        *,
        puuid: str,
        start_at: int,
        end_at: int,
        target_at: int,
        offset: int,
        queue_id: int,
        region: str,
        matches_per_player: int,
    ) -> None:
        self.cursors[puuid] = {
            "start_at": start_at,
            "end_at": end_at,
            "target_at": target_at,
            "offset": offset,
            "queue_id": queue_id,
            "region": region,
            "matches_per_player": matches_per_player,
        }

    def clear_manual_collection_cursor(self, puuid: str) -> None:
        self.cursors.pop(puuid, None)

    def get_player_memos(self, _puuids: list[str]) -> dict[str, str]:
        return {}


def _resolve_account(
    _client: object,
    _cache: object,
    game_name: str,
    tag_line: str,
) -> tuple[dict[str, str], int, int]:
    """테스트 Riot ID를 이름과 같은 PUUID로 바꾼다."""
    return (
        {
            "puuid": game_name.lower(),
            "game_name": game_name,
            "tag_line": tag_line,
        },
        1,
        0,
    )


class MultiSearchCompletionTests(unittest.TestCase):
    """경기 저장이 끝난 사람만 성공으로 기록되는지 검증한다."""

    def setUp(self) -> None:
        self.config = SimpleNamespace(
            api_key="test-key",
            region="asia",
            platform="kr",
            queue_id=420,
        )

    def _run(
        self,
        *,
        riot_ids_raw: str,
        fetch_ids: object,
        fetch_detail: object,
        days_by_riot_id: dict[str, int] | None = None,
        matches_by_riot_id: dict[str, int] | None = None,
        known_targets: tuple[MultiSearchTarget, ...] = (),
        indexed_rows: int = 10,
        resolve_account: object = _resolve_account,
        initial_cursors: dict[str, dict[str, object]] | None = None,
        existing_index_ids: set[str] | None = None,
    ) -> tuple[object, _RecordingCache]:
        cache = _RecordingCache(
            indexed_rows=indexed_rows,
            existing_index_ids=existing_index_ids,
        )
        cache.cursors.update(initial_cursors or {})
        request = MultiSearchRequest(
            riot_ids_raw=riot_ids_raw,
            days=30,
            matches_per_player=100,
            days_by_riot_id=days_by_riot_id or {},
            matches_per_player_by_riot_id=matches_by_riot_id or {},
            known_targets=known_targets,
        )

        with (
            patch("src.multi_search_service.RiotClient", _FakeRiotClient),
            patch(
                "src.multi_search_service._resolve_puuid_with_count",
                side_effect=resolve_account,
            ),
            patch(
                "src.multi_search_service.fetch_all_match_ids",
                side_effect=fetch_ids,
            ),
            patch(
                "src.multi_search_service.fetch_match_detail",
                side_effect=fetch_detail,
            ),
            patch("src.multi_search_service.time.sleep"),
            patch("src.multi_search_service.time.time", return_value=1_800_000_000),
        ):
            payload = run_multi_search(
                config=self.config,
                request=request,
                cache=cache,
                progress_cb=lambda _value: None,
                status_cb=lambda _message: None,
            )

        return payload, cache

    def test_marks_success_after_detail_is_indexed(self) -> None:
        payload, cache = self._run(
            riot_ids_raw="One#KR1",
            fetch_ids=lambda **_kwargs: ["KR_1"],
            fetch_detail=lambda *_args: {"match_id": "KR_1"},
        )

        self.assertEqual(payload.success_count, 1)
        self.assertEqual(payload.failure_count, 0)
        self.assertEqual(
            cache.events,
            ["index:KR_1", "collected:one"],
        )

    def test_missing_detail_does_not_mark_user_as_collected(self) -> None:
        payload, cache = self._run(
            riot_ids_raw="One#KR1",
            fetch_ids=lambda **_kwargs: ["KR_MISSING"],
            fetch_detail=lambda *_args: None,
        )

        self.assertEqual(payload.success_count, 0)
        self.assertEqual(payload.failure_count, 1)
        self.assertEqual(cache.collected, {})
        self.assertIn("끝까지 저장하지 못했습니다", payload.failures[0].reason)

    def test_one_user_list_failure_does_not_block_another_user(self) -> None:
        def fetch_ids(*, puuid: str, **_kwargs: object) -> list[str]:
            if puuid == "fail":
                raise RiotApiError("일시적인 목록 오류")
            return ["KR_OK"]

        payload, cache = self._run(
            riot_ids_raw="Fail#KR1, Ok#KR1",
            fetch_ids=fetch_ids,
            fetch_detail=lambda *_args: {"match_id": "KR_OK"},
        )

        self.assertEqual(payload.success_count, 1)
        self.assertEqual(payload.failure_count, 1)
        self.assertEqual(set(cache.collected), {"ok"})
        self.assertEqual(payload.failures[0].riot_id_raw, "Fail#KR1")

    def test_uses_each_users_own_collection_window(self) -> None:
        calls: dict[str, tuple[int, int]] = {}

        def fetch_ids(
            *,
            puuid: str,
            start_ts: int,
            end_ts: int,
            max_total: int,
            **_kwargs: object,
        ) -> list[str]:
            calls[puuid] = (end_ts - start_ts, max_total)
            return []

        payload, cache = self._run(
            riot_ids_raw="Recent#KR1, Old#KR1",
            fetch_ids=fetch_ids,
            fetch_detail=lambda *_args: None,
            days_by_riot_id={"recent#kr1": 3, "old#kr1": 20},
            matches_by_riot_id={"recent#kr1": 15, "old#kr1": 100},
        )

        self.assertEqual(calls["recent"], (3 * 86_400, 16))
        self.assertEqual(calls["old"], (20 * 86_400, 101))
        self.assertEqual(payload.success_count, 2)
        self.assertEqual(set(cache.collected), {"recent", "old"})

    def test_limit_probe_does_not_mark_truncated_user_as_complete(self) -> None:
        payload, cache = self._run(
            riot_ids_raw="Busy#KR1",
            fetch_ids=lambda **_kwargs: ["KR_1", "KR_2", "KR_3"],
            fetch_detail=lambda *_args: {
                "match_id": _args[2],
                "info": {"gameCreation": 1_700_000_000_000},
            },
            matches_by_riot_id={"busy#kr1": 2},
        )

        self.assertEqual(payload.success_count, 0)
        self.assertEqual(payload.failure_count, 1)
        self.assertEqual(payload.discovered_matches, 2)
        self.assertEqual(cache.collected, {})
        self.assertIn("최대 2판보다 경기가 더 많습니다", payload.failures[0].reason)
        self.assertIn("busy", cache.cursors)
        self.assertEqual(cache.cursors["busy"]["offset"], 2)
        self.assertEqual(payload.continuation_advanced_count, 1)

    def test_saved_cursor_continues_older_range_and_finishes_target(self) -> None:
        calls: list[tuple[int, int, int]] = []

        def fetch_ids(
            *,
            start_ts: int,
            end_ts: int,
            start_offset: int,
            **_kwargs: object,
        ) -> list[str]:
            calls.append((start_ts, end_ts, start_offset))
            return []

        payload, cache = self._run(
            riot_ids_raw="One#KR1",
            fetch_ids=fetch_ids,
            fetch_detail=lambda *_args: None,
            initial_cursors={
                "one": {
                    "start_at": 100,
                    "end_at": 500,
                    "target_at": 900,
                    "offset": 200,
                    "queue_id": 420,
                    "region": "asia",
                    "matches_per_player": 100,
                }
            },
        )

        self.assertEqual(calls, [(100, 500, 200)])
        self.assertEqual(payload.success_count, 1)
        self.assertEqual(cache.collected, {"one": 900})
        self.assertEqual(cache.cursors, {})

    def test_saved_cursor_summary_uses_the_actual_resumed_scope(self) -> None:
        calls: list[tuple[int, int, int]] = []

        def fetch_ids(
            *, start_ts: int, end_ts: int, max_total: int, **_kwargs: object
        ) -> list[str]:
            calls.append((start_ts, end_ts, max_total))
            return []

        payload, _cache = self._run(
            riot_ids_raw="One#KR1",
            fetch_ids=fetch_ids,
            fetch_detail=lambda *_args: None,
            matches_by_riot_id={"one#kr1": 20},
            initial_cursors={
                "one": {
                    "start_at": 100,
                    "end_at": 900,
                    "target_at": 900,
                    "offset": 200,
                    "queue_id": 420,
                    "region": "asia",
                    "matches_per_player": 300,
                }
            },
        )

        self.assertEqual(calls, [(100, 900, 301)])
        self.assertEqual(payload.start_ts, 100)
        self.assertEqual(payload.end_ts, 900)
        self.assertEqual(payload.matches_per_player, 300)
        self.assertIn("이전 수집 이어받기 1명 포함", payload.period_label)

    def test_cursor_from_another_queue_starts_again_without_offset(self) -> None:
        calls: list[tuple[int, int]] = []

        def fetch_ids(
            *, start_ts: int, start_offset: int, **_kwargs: object
        ) -> list[str]:
            calls.append((start_ts, start_offset))
            return []

        payload, cache = self._run(
            riot_ids_raw="One#KR1",
            fetch_ids=fetch_ids,
            fetch_detail=lambda *_args: None,
            initial_cursors={
                "one": {
                    "start_at": 100,
                    "end_at": 500,
                    "target_at": 900,
                    "offset": 200,
                    "queue_id": 440,
                    "region": "asia",
                    "matches_per_player": 100,
                }
            },
        )

        self.assertEqual(calls, [(1_800_000_000 - 30 * 86_400, 0)])
        self.assertEqual(payload.success_count, 1)
        self.assertEqual(cache.cursors, {})

    def test_known_account_uses_puuid_without_resolving_riot_id(self) -> None:
        def fail_if_resolved(*_args: object) -> None:
            raise AssertionError("이미 아는 PUUID를 Riot ID로 다시 조회했습니다.")

        payload, cache = self._run(
            riot_ids_raw="",
            fetch_ids=lambda **_kwargs: [],
            fetch_detail=lambda *_args: None,
            known_targets=(
                MultiSearchTarget(
                    puuid="stable-puuid",
                    game_name="Known",
                    tag_line="KR1",
                    days=30,
                    matches_per_player=100,
                ),
            ),
            resolve_account=fail_if_resolved,
        )

        self.assertEqual(payload.success_count, 1)
        self.assertEqual(set(cache.collected), {"stable-puuid"})
        self.assertEqual(payload.resolved_users[0]["puuid"], "stable-puuid")

    def test_known_targets_with_same_old_riot_id_keep_both_puuids(self) -> None:
        payload, cache = self._run(
            riot_ids_raw="",
            fetch_ids=lambda **_kwargs: [],
            fetch_detail=lambda *_args: None,
            known_targets=(
                MultiSearchTarget("first-puuid", "Same", "KR1", 30, 100),
                MultiSearchTarget("second-puuid", "Same", "KR1", 30, 100),
            ),
        )

        self.assertEqual(payload.input_count, 2)
        self.assertEqual(payload.success_count, 2)
        self.assertEqual(set(cache.collected), {"first-puuid", "second-puuid"})

    def test_global_list_errors_stop_the_whole_job(self) -> None:
        for error in (
            RiotApiAuthError("API 키 만료", status_code=403),
            RiotApiRateLimited("호출 제한", status_code=429),
        ):
            with self.subTest(error=type(error).__name__):
                fetch_ids = Mock(side_effect=error)
                with self.assertRaises(type(error)):
                    self._run(
                        riot_ids_raw="One#KR1, Two#KR1",
                        fetch_ids=fetch_ids,
                        fetch_detail=lambda *_args: None,
                    )
                self.assertEqual(fetch_ids.call_count, 1)

    def test_global_detail_error_stops_the_whole_job(self) -> None:
        with self.assertRaises(RiotApiAuthError):
            self._run(
                riot_ids_raw="One#KR1",
                fetch_ids=lambda **_kwargs: ["KR_1"],
                fetch_detail=Mock(side_effect=RiotApiAuthError("API 키 만료")),
            )

    def test_shared_missing_match_fails_each_affected_user_once(self) -> None:
        detail = Mock(return_value=None)
        payload, cache = self._run(
            riot_ids_raw="One#KR1, Two#KR1",
            fetch_ids=lambda **_kwargs: ["KR_SHARED"],
            fetch_detail=detail,
        )

        self.assertEqual(payload.success_count, 0)
        self.assertEqual(payload.failure_count, 2)
        self.assertEqual(detail.call_count, 1)
        self.assertEqual(cache.collected, {})

    def test_zero_index_rows_does_not_mark_user_as_complete(self) -> None:
        payload, cache = self._run(
            riot_ids_raw="One#KR1",
            fetch_ids=lambda **_kwargs: ["KR_1"],
            fetch_detail=lambda *_args: {"match_id": "KR_1"},
            indexed_rows=0,
        )

        self.assertEqual(payload.success_count, 0)
        self.assertEqual(payload.failure_count, 1)
        self.assertEqual(cache.collected, {})
        self.assertIn("조회용 데이터로 저장하지 못했습니다", payload.failures[0].reason)

    def test_existing_index_is_not_rebuilt_and_still_counts_as_complete(self) -> None:
        """이미 조회 가능한 경기는 다시 인덱싱하지 않는다."""
        payload, cache = self._run(
            riot_ids_raw="One#KR1",
            fetch_ids=lambda **_kwargs: ["KR_EXISTING"],
            fetch_detail=lambda *_args: {"match_id": "KR_EXISTING"},
            existing_index_ids={"KR_EXISTING"},
        )

        self.assertEqual(payload.success_count, 1)
        self.assertEqual(payload.indexed_rows, 0)
        self.assertEqual(cache.events, ["collected:one"])


if __name__ == "__main__":
    unittest.main()
