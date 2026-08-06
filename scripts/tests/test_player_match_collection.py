"""앱 시작 시 매치 수집 계획 계산을 검증한다."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

import src.api.main as api_main

from src.api.main import (
    AUTO_REFRESH_MAX_MATCHES_PER_PLAYER,
    DEFAULT_MULTI_MATCHES_PER_PLAYER,
    DEFAULT_MULTI_SEARCH_DAYS,
    JobRecord,
    STALE_COLLECTION_DAYS,
    _collection_plan,
    _latest_target_collection_at,
)
from src.multi_search_service import MultiSearchFailure


class PlayerMatchCollectionPlanTests(unittest.TestCase):
    """매치 수집 기간과 확인 조건을 검증한다."""

    def test_empty_database_uses_existing_multi_search_defaults(self) -> None:
        self.assertEqual(
            _collection_plan(None),
            (DEFAULT_MULTI_SEARCH_DAYS, DEFAULT_MULTI_MATCHES_PER_PLAYER, False),
        )

    def test_recent_database_still_has_manual_collection_plan(self) -> None:
        self.assertEqual(_collection_plan(0), (1, 5, False))
        self.assertEqual(_collection_plan(2), (2, 10, False))

    def test_59_days_uses_automatic_calculation(self) -> None:
        self.assertEqual(_collection_plan(59), (59, 295, False))

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


class PlayerMatchCollectionRequestTests(unittest.TestCase):
    """수동 수집의 장기 미실행 확인을 검증한다."""

    def setUp(self) -> None:
        api_main._auto_refresh_status["jobId"] = None

    @patch.object(api_main, "_days_since_latest_match", return_value=60)
    @patch.object(api_main, "_collection_targets", return_value=[{"puuid": "player"}])
    @patch.object(
        api_main,
        "_get_config",
        return_value=SimpleNamespace(api_key="test-key"),
    )
    def test_stale_manual_collection_requires_confirmation(
        self,
        _config: object,
        _targets: object,
        _days: object,
    ) -> None:
        with self.assertRaises(HTTPException) as context:
            api_main.create_player_match_collection_job(
                api_main.PlayerMatchCollectionBody(confirmed=False)
            )

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(
            context.exception.detail,
            "최근 90일, 최대 300판씩 수집할까요?",
        )

    @patch.object(api_main, "_days_since_latest_match", return_value=60)
    @patch.object(api_main, "_collection_targets", return_value=[{"puuid": "player"}])
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
        _days: object,
    ) -> None:
        result = api_main.create_player_match_collection_job(
            api_main.PlayerMatchCollectionBody(confirmed=True)
        )

        self.assertEqual(result, {"jobId": "collection-job"})
        start_collection.assert_called_once_with(
            targets=[{"puuid": "player"}],
            days=90,
            matches_per_player=300,
        )

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
            status="succeeded",
            result=result,
        )

        with (
            patch.dict(
                api_main._auto_refresh_status,
                {"jobId": job.job_id, "message": "매치 수집 완료"},
                clear=True,
            ),
            patch.object(api_main._jobs, "get", return_value=job),
        ):
            payload = api_main._auto_refresh_payload()

        self.assertEqual(payload["message"], "13명 성공 · 1명 실패")
        self.assertEqual(
            payload["failures"],
            [
                {
                    "riot_id_raw": "실패유저#KR1",
                    "reason": "Riot ID를 찾지 못했습니다.",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
