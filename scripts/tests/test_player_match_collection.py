"""앱 시작 시 매치 수집 계획 계산을 검증한다."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException

import src.api.main as api_main

from src.api.main import (
    AUTO_REFRESH_MAX_MATCHES_PER_PLAYER,
    DEFAULT_MULTI_MATCHES_PER_PLAYER,
    DEFAULT_MULTI_SEARCH_DAYS,
    JobRecord,
    STALE_COLLECTION_DAYS,
    _collection_plan,
    _days_since_collection,
    _latest_target_collection_at,
    _plan_collection_targets,
)
from src.multi_search_service import (
    MultiSearchFailure,
    MultiSearchRequest,
    MultiSearchTarget,
)


def _empty_identity_cache() -> Mock:
    """작업 키 계산에서 로컬 유저를 찾지 못하는 캐시 대역이다."""
    cache = Mock()
    cache.get_account.return_value = None
    cache.find_players_by_riot_id.return_value = []
    return cache


class PlayerMatchCollectionPlanTests(unittest.TestCase):
    """매치 수집 기간과 확인 조건을 검증한다."""

    def test_empty_database_uses_existing_multi_search_defaults(self) -> None:
        self.assertEqual(
            _collection_plan(None),
            (DEFAULT_MULTI_SEARCH_DAYS, DEFAULT_MULTI_MATCHES_PER_PLAYER, False),
        )

    def test_recent_database_still_has_manual_collection_plan(self) -> None:
        self.assertEqual(_collection_plan(0), (1, 15, False))
        self.assertEqual(_collection_plan(2), (2, 30, False))

    def test_long_gaps_stop_at_the_per_player_limit(self) -> None:
        self.assertEqual(_collection_plan(19), (19, 285, False))
        self.assertEqual(_collection_plan(21), (21, 300, False))

    def test_59_days_uses_automatic_calculation(self) -> None:
        self.assertEqual(_collection_plan(59), (59, 300, False))

    def test_60_days_requires_90_day_confirmation(self) -> None:
        self.assertEqual(
            _collection_plan(60),
            (STALE_COLLECTION_DAYS, AUTO_REFRESH_MAX_MATCHES_PER_PLAYER, True),
        )

    def test_latest_collection_time_ignores_missing_values(self) -> None:
        targets = [
            {"last_collected_at": None},
            {"last_collected_at": 100},
            {"last_collected_at": 300},
        ]
        self.assertEqual(_latest_target_collection_at(targets), 300)

    def test_days_since_collection_uses_the_user_completion_time(self) -> None:
        now = 1_800_000_000

        self.assertIsNone(_days_since_collection(None, now=now))
        self.assertEqual(
            _days_since_collection(now - 3 * 86_400, now=now),
            3,
        )

    def test_auto_plan_skips_only_recent_users(self) -> None:
        now = 1_800_000_000
        targets = [
            {"puuid": "recent", "last_collected_at": now - 86_400},
            {"puuid": "old", "last_collected_at": now - 10 * 86_400},
            {"puuid": "never", "last_collected_at": None},
        ]

        planned = _plan_collection_targets(
            targets,
            include_recent=False,
            now=now,
        )

        self.assertEqual([target["puuid"] for target in planned], ["old", "never"])
        self.assertEqual(planned[0]["collection_days"], 10)
        self.assertEqual(planned[0]["collection_matches_per_player"], 150)
        self.assertEqual(planned[1]["collection_days"], DEFAULT_MULTI_SEARCH_DAYS)
        self.assertEqual(
            planned[1]["collection_matches_per_player"],
            DEFAULT_MULTI_MATCHES_PER_PLAYER,
        )

    def test_auto_plan_collects_once_a_full_day_has_passed(self) -> None:
        now = 1_800_000_000
        targets = [
            {"puuid": "today", "last_collected_at": now - 86_400},
            {"puuid": "yesterday", "last_collected_at": now - 86_401},
        ]

        planned = _plan_collection_targets(
            targets,
            include_recent=False,
            now=now,
        )

        self.assertEqual([target["puuid"] for target in planned], ["yesterday"])
        self.assertEqual(planned[0]["collection_days"], 2)

    def test_pending_collection_is_planned_even_when_last_success_is_recent(
        self,
    ) -> None:
        now = 1_800_000_000
        target = {
            "puuid": "continue",
            "last_collected_at": now - 86_400,
            "pending_collection_start_at": now - 30 * 86_400,
            "pending_collection_end_at": now - 10,
            "pending_collection_target_at": now - 10,
            "pending_collection_offset": 100,
            "pending_collection_matches_per_player": 75,
        }

        planned = _plan_collection_targets(
            [target],
            include_recent=False,
            now=now,
        )

        self.assertEqual(len(planned), 1)
        self.assertEqual(planned[0]["collection_days"], 30)
        self.assertEqual(planned[0]["collection_matches_per_player"], 75)
        self.assertFalse(planned[0]["requires_confirmation"])

    def test_meta_replans_confirmation_after_settings_change(self) -> None:
        now = 1_800_000_000
        target = {
            "puuid": "stale",
            "last_collected_at": now - 100 * 86_400,
        }
        with (
            patch.dict(
                api_main._auto_refresh_status,
                {
                    "status": "skipped",
                    "message": "이전 상태",
                    "jobId": None,
                    "plannedTargetCount": 0,
                },
                clear=True,
            ),
            patch.object(api_main, "_collection_targets", return_value=[target]),
            patch.object(api_main.time, "time", return_value=now),
        ):
            payload = api_main._auto_refresh_payload(api_key_available=True)

        self.assertEqual(payload["status"], "stale")
        self.assertEqual(payload["plannedTargetCount"], 1)
        self.assertTrue(payload["requiresConfirmation"])

    def test_meta_removes_stale_plan_after_target_is_deleted(self) -> None:
        with (
            patch.dict(
                api_main._auto_refresh_status,
                {
                    "status": "stale",
                    "message": "이전 상태",
                    "jobId": None,
                    "plannedTargetCount": 1,
                    "requiresConfirmation": True,
                },
                clear=True,
            ),
            patch.object(api_main, "_collection_targets", return_value=[]),
        ):
            payload = api_main._auto_refresh_payload(api_key_available=True)

        self.assertEqual(payload["status"], "skipped")
        self.assertEqual(payload["targetCount"], 0)
        self.assertEqual(payload["plannedTargetCount"], 0)
        self.assertFalse(payload["requiresConfirmation"])


class PlayerMatchCollectionRequestTests(unittest.TestCase):
    """수동 수집의 장기 미실행 확인을 검증한다."""

    def setUp(self) -> None:
        api_main._auto_refresh_status["jobId"] = None
        api_main._auto_refresh_status["status"] = "skipped"

    def test_startup_auto_check_can_retry_after_a_planning_error(self) -> None:
        original_status = dict(api_main._auto_refresh_status)
        with (
            patch.object(api_main, "_auto_refresh_checked", False),
            patch.dict(api_main._auto_refresh_status, original_status, clear=True),
            patch.object(
                api_main,
                "_collection_targets",
                side_effect=[RuntimeError("임시 DB 오류"), []],
            ) as targets,
        ):
            with self.assertRaisesRegex(RuntimeError, "임시 DB 오류"):
                api_main._maybe_start_auto_refresh()

            self.assertFalse(api_main._auto_refresh_checked)
            self.assertEqual(api_main._auto_refresh_status["status"], "not_checked")
            api_main._maybe_start_auto_refresh()

            self.assertTrue(api_main._auto_refresh_checked)
            self.assertEqual(api_main._auto_refresh_status["status"], "skipped")
            self.assertEqual(targets.call_count, 2)

    def test_multi_search_job_without_saved_progress_is_failed(self) -> None:
        class InlineJobStore:
            """테스트 스레드 없이 runner를 바로 실행한다."""

            def __init__(self) -> None:
                self.job: JobRecord | None = None

            def create(
                self,
                kind: str,
                runner: object,
                **_kwargs: object,
            ) -> JobRecord:
                self.job = JobRecord(job_id="job", kind=kind, status="running")
                runner(self.job)  # type: ignore[operator]
                return self.job

            def update(self, _job_id: str, **changes: object) -> None:
                assert self.job is not None
                for key, value in changes.items():
                    setattr(self.job, key, value)

        jobs = InlineJobStore()
        result = SimpleNamespace(
            success_count=0,
            failure_count=1,
            discovered_matches=10,
            new_match_details=0,
            indexed_rows=0,
            continuation_advanced_count=0,
        )
        with (
            patch.object(api_main, "_jobs", jobs),
            patch.object(api_main, "run_multi_search", return_value=result),
            patch.object(api_main, "_get_config", return_value=object()),
            patch.object(api_main, "_get_cache", return_value=_empty_identity_cache()),
        ):
            job = api_main._create_multi_search_job(
                request=MultiSearchRequest(
                    riot_ids_raw="Fail#KR1",
                    days=30,
                    matches_per_player=100,
                )
            )

        self.assertEqual(job.status, "failed")
        self.assertEqual(job.message, "0명 완료 · 1명 확인 필요")
        self.assertIs(job.result, result)

    def test_multi_search_job_with_saved_continuation_is_partial(self) -> None:
        class InlineJobStore:
            """테스트 스레드 없이 runner를 바로 실행한다."""

            def __init__(self) -> None:
                self.job: JobRecord | None = None

            def create(
                self,
                kind: str,
                runner: object,
                **_kwargs: object,
            ) -> JobRecord:
                self.job = JobRecord(job_id="job", kind=kind, status="running")
                runner(self.job)  # type: ignore[operator]
                return self.job

            def update(self, _job_id: str, **changes: object) -> None:
                assert self.job is not None
                for key, value in changes.items():
                    setattr(self.job, key, value)

        jobs = InlineJobStore()
        result = SimpleNamespace(
            success_count=0,
            failure_count=1,
            discovered_matches=100,
            new_match_details=0,
            indexed_rows=0,
            continuation_advanced_count=1,
        )
        with (
            patch.object(api_main, "_jobs", jobs),
            patch.object(api_main, "run_multi_search", return_value=result),
            patch.object(api_main, "_get_config", return_value=object()),
            patch.object(api_main, "_get_cache", return_value=_empty_identity_cache()),
        ):
            job = api_main._create_multi_search_job(
                request=MultiSearchRequest(
                    riot_ids_raw="Busy#KR1",
                    days=30,
                    matches_per_player=100,
                )
            )

        self.assertEqual(job.status, "partial")
        self.assertEqual(job.message, "0명 완료 · 1명 확인 필요")
        self.assertIs(job.result, result)

    def test_known_target_dedupe_key_includes_puuid(self) -> None:
        """예전 Riot ID가 같아도 다른 PUUID 작업을 합치지 않는다."""
        captured_keys: list[str] = []

        def create(_kind: str, _runner: object, **kwargs: object) -> JobRecord:
            captured_keys.append(str(kwargs["dedupe_key"]))
            return JobRecord(
                job_id=f"job-{len(captured_keys)}",
                kind="collection",
                status="queued",
            )

        with patch.object(api_main._jobs, "create", side_effect=create):
            for puuid in ("first-puuid", "second-puuid"):
                api_main._create_multi_search_job(
                    request=MultiSearchRequest(
                        riot_ids_raw="",
                        days=30,
                        matches_per_player=100,
                        known_targets=(
                            MultiSearchTarget(
                                puuid=puuid,
                                game_name="OldName",
                                tag_line="KR1",
                                days=30,
                                matches_per_player=100,
                            ),
                        ),
                    )
                )

        self.assertNotEqual(captured_keys[0], captured_keys[1])
        self.assertIn("first-puuid", captured_keys[0])
        self.assertIn("second-puuid", captured_keys[1])

    def test_known_and_direct_target_share_the_same_collection_job_key(self) -> None:
        captured_keys: list[str] = []
        cache = Mock()
        cache.get_account.return_value = {
            "puuid": "stable-puuid",
            "game_name": "Player",
            "tag_line": "KR1",
        }

        def create(_kind: str, _runner: object, **kwargs: object) -> JobRecord:
            captured_keys.append(str(kwargs["dedupe_key"]))
            return JobRecord(
                job_id=f"job-{len(captured_keys)}",
                kind="collection",
                status="queued",
            )

        with (
            patch.object(api_main, "_get_cache", return_value=cache),
            patch.object(api_main._jobs, "create", side_effect=create),
        ):
            api_main._create_multi_search_job(
                request=MultiSearchRequest(
                    riot_ids_raw="",
                    days=30,
                    matches_per_player=100,
                    known_targets=(
                        MultiSearchTarget(
                            puuid="stable-puuid",
                            game_name="Player",
                            tag_line="KR1",
                            days=30,
                            matches_per_player=100,
                        ),
                    ),
                )
            )
            api_main._create_multi_search_job(
                request=MultiSearchRequest(
                    riot_ids_raw="Player#KR1",
                    days=30,
                    matches_per_player=100,
                )
            )

        self.assertEqual(captured_keys[0], captured_keys[1])

    def test_stale_registry_identity_does_not_merge_a_direct_collection(self) -> None:
        captured_keys: list[str] = []
        cache = Mock()
        cache.get_account.return_value = None
        cache.find_players_by_riot_id.return_value = [{"puuid": "old-puuid"}]

        def create(_kind: str, _runner: object, **kwargs: object) -> JobRecord:
            captured_keys.append(str(kwargs["dedupe_key"]))
            return JobRecord(
                job_id=f"job-{len(captured_keys)}",
                kind="collection",
                status="queued",
            )

        with (
            patch.object(api_main, "_get_cache", return_value=cache),
            patch.object(api_main._jobs, "create", side_effect=create),
        ):
            api_main._create_multi_search_job(
                request=MultiSearchRequest(
                    riot_ids_raw="",
                    days=30,
                    matches_per_player=100,
                    known_targets=(
                        MultiSearchTarget(
                            puuid="old-puuid",
                            game_name="ReusedName",
                            tag_line="KR1",
                            days=30,
                            matches_per_player=100,
                        ),
                    ),
                )
            )
            api_main._create_multi_search_job(
                request=MultiSearchRequest(
                    riot_ids_raw="ReusedName#KR1",
                    days=30,
                    matches_per_player=100,
                )
            )

        self.assertNotEqual(captured_keys[0], captured_keys[1])
        cache.find_players_by_riot_id.assert_not_called()

    def test_startup_collection_uses_only_users_that_need_refresh(self) -> None:
        now = 1_800_000_000
        recent = {
            "puuid": "recent",
            "riot_id_game_name": "Recent",
            "riot_id_tag_line": "KR1",
            "last_collected_at": now - 86_400,
        }
        old = {
            "puuid": "old",
            "riot_id_game_name": "Old",
            "riot_id_tag_line": "KR1",
            "last_collected_at": now - 10 * 86_400,
        }

        with (
            patch.object(api_main, "_auto_refresh_checked", False),
            patch.dict(api_main._auto_refresh_status, {}, clear=True),
            patch.object(api_main, "_collection_targets", return_value=[recent, old]),
            patch.object(
                api_main,
                "_get_config",
                return_value=SimpleNamespace(api_key="test-key"),
            ),
            patch.object(api_main.time, "time", return_value=now),
            patch.object(api_main, "_start_player_match_collection") as start,
        ):
            api_main._maybe_start_auto_refresh()

        start.assert_called_once_with(
            targets=[
                {
                    **old,
                    "collection_days": 10,
                    "collection_matches_per_player": 150,
                    "requires_confirmation": False,
                }
            ],
            days=10,
            matches_per_player=150,
            origin="startup-auto",
        )

    def test_startup_collection_includes_never_collected_user(self) -> None:
        target = {
            "puuid": "new",
            "riot_id_game_name": "New",
            "riot_id_tag_line": "KR1",
            "last_collected_at": None,
        }

        with (
            patch.object(api_main, "_auto_refresh_checked", False),
            patch.dict(api_main._auto_refresh_status, {}, clear=True),
            patch.object(api_main, "_collection_targets", return_value=[target]),
            patch.object(
                api_main,
                "_get_config",
                return_value=SimpleNamespace(api_key="test-key"),
            ),
            patch.object(api_main, "_start_player_match_collection") as start,
        ):
            api_main._maybe_start_auto_refresh()

        start.assert_called_once_with(
            targets=[
                {
                    **target,
                    "collection_days": DEFAULT_MULTI_SEARCH_DAYS,
                    "collection_matches_per_player": (DEFAULT_MULTI_MATCHES_PER_PLAYER),
                    "requires_confirmation": False,
                }
            ],
            days=DEFAULT_MULTI_SEARCH_DAYS,
            matches_per_player=DEFAULT_MULTI_MATCHES_PER_PLAYER,
            origin="startup-auto",
        )

    @patch.object(
        api_main,
        "_collection_targets",
        return_value=[{"puuid": "player", "last_collected_at": 0}],
    )
    @patch.object(
        api_main,
        "_get_config",
        return_value=SimpleNamespace(api_key="test-key"),
    )
    def test_stale_manual_collection_requires_confirmation(
        self,
        _config: object,
        _targets: object,
    ) -> None:
        with self.assertRaises(HTTPException) as context:
            api_main.create_player_match_collection_job(
                api_main.PlayerMatchCollectionBody(confirmed=False)
            )

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(
            context.exception.detail,
            "이번에 수집이 필요한 1명은 대상별 최근 최대 90일, "
            "최대 300판씩 수집할까요?",
        )

    @patch.object(
        api_main,
        "_collection_targets",
        return_value=[{"puuid": "player", "last_collected_at": 0}],
    )
    @patch.object(
        api_main,
        "_get_config",
        return_value=SimpleNamespace(api_key="test-key"),
    )
    @patch.object(
        api_main,
        "_start_player_match_collection",
        return_value=SimpleNamespace(job_id="collection-job"),
    )
    def test_confirmed_stale_collection_starts_90_day_job(
        self,
        start_collection: object,
        _config: object,
        _targets: object,
    ) -> None:
        result = api_main.create_player_match_collection_job(
            api_main.PlayerMatchCollectionBody(confirmed=True)
        )

        self.assertEqual(result, {"jobId": "collection-job"})
        start_collection.assert_called_once_with(
            targets=[
                {
                    "puuid": "player",
                    "last_collected_at": 0,
                    "collection_days": 90,
                    "collection_matches_per_player": 300,
                    "requires_confirmation": True,
                }
            ],
            days=90,
            matches_per_player=300,
            origin="manual-auto",
        )

    def test_stale_confirmation_does_not_add_recent_users(self) -> None:
        now = 1_800_000_000
        targets = [
            {
                "puuid": "stale",
                "last_collected_at": now - 100 * 86_400,
            },
            {
                "puuid": "recent",
                "last_collected_at": now - 86_400,
            },
        ]

        with (
            patch.object(api_main, "_collection_targets", return_value=targets),
            patch.object(
                api_main,
                "_get_config",
                return_value=SimpleNamespace(api_key="test-key"),
            ),
            patch.object(api_main.time, "time", return_value=now),
            patch.object(
                api_main,
                "_start_player_match_collection",
                return_value=SimpleNamespace(job_id="collection-job"),
            ) as start_collection,
        ):
            result = api_main.create_player_match_collection_job(
                api_main.PlayerMatchCollectionBody(confirmed=True)
            )

        self.assertEqual(result, {"jobId": "collection-job"})
        planned_targets = start_collection.call_args.kwargs["targets"]
        self.assertEqual([target["puuid"] for target in planned_targets], ["stale"])

    def test_collection_job_passes_known_puuid_to_multi_search(self) -> None:
        target = {
            "puuid": "stable-puuid",
            "riot_id_game_name": "Known",
            "riot_id_tag_line": "KR1",
            "last_collected_at": None,
            "collection_days": 30,
            "collection_matches_per_player": 100,
            "requires_confirmation": False,
        }
        created_job = JobRecord(
            job_id="collection-job",
            kind="player-match-collection",
            status="queued",
        )

        with patch.object(
            api_main,
            "_create_multi_search_job",
            return_value=created_job,
        ) as create_job:
            job = api_main._start_player_match_collection(
                targets=[target],
                days=30,
                matches_per_player=100,
                origin="manual-auto",
            )

        request = create_job.call_args.kwargs["request"]
        self.assertIs(job, created_job)
        self.assertEqual(len(request.known_targets), 1)
        self.assertEqual(request.known_targets[0].puuid, "stable-puuid")

    @patch.object(
        api_main,
        "_collection_targets",
        return_value=[{"last_collected_at": 300}],
    )
    def test_completed_collection_exposes_failure_details(
        self,
        _targets: object,
    ) -> None:
        result = SimpleNamespace(
            success_count=13,
            failure_count=1,
            failures=[MultiSearchFailure("실패유저#KR1", "Riot ID를 찾지 못했습니다.")],
        )
        job = JobRecord(
            job_id="completed-job",
            kind="player-match-collection",
            status="partial",
            result=result,
        )

        with (
            patch.dict(
                api_main._auto_refresh_status,
                {"jobId": job.job_id, "message": "매치 수집 완료"},
                clear=True,
            ),
            patch.object(api_main._jobs, "get", return_value=job),
            patch.object(api_main, "_plan_collection_targets", return_value=[]),
        ):
            payload = api_main._auto_refresh_payload()

        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["message"], "13명 완료 · 1명 확인 필요")
        self.assertEqual(
            payload["failures"],
            [
                {
                    "riot_id_raw": "실패유저#KR1",
                    "reason": "Riot ID를 찾지 못했습니다.",
                }
            ],
        )

    def test_terminal_collection_result_is_not_hidden_by_stale_plan(self) -> None:
        """다시 수집할 사람이 남아도 방금 끝난 결과를 먼저 보여준다."""
        now = 1_800_000_000
        target = {"puuid": "old", "last_collected_at": now - 100 * 86_400}
        result = SimpleNamespace(
            success_count=0,
            failure_count=1,
            failures=[MultiSearchFailure("Old#KR1", "상세 저장 실패")],
        )

        for terminal_status in ("failed", "partial"):
            with self.subTest(status=terminal_status):
                job = JobRecord(
                    job_id=f"{terminal_status}-job",
                    kind="player-match-collection",
                    status=terminal_status,
                    error="수집을 끝내지 못했어요"
                    if terminal_status == "failed"
                    else None,
                    result=result,
                )
                with (
                    patch.dict(
                        api_main._auto_refresh_status,
                        {"jobId": job.job_id, "message": "이전 상태"},
                        clear=True,
                    ),
                    patch.object(api_main._jobs, "get", return_value=job),
                    patch.object(
                        api_main, "_collection_targets", return_value=[target]
                    ),
                    patch.object(api_main.time, "time", return_value=now),
                ):
                    payload = api_main._auto_refresh_payload(api_key_available=True)

                self.assertEqual(payload["status"], terminal_status)
                self.assertTrue(payload["requiresConfirmation"])
                self.assertEqual(payload["plannedTargetCount"], 1)
                self.assertNotEqual(
                    payload["message"], "오랫동안 수집하지 않아 확인이 필요합니다."
                )


if __name__ == "__main__":
    unittest.main()
