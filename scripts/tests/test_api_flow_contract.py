"""시작·상세·DB 스냅샷 API가 숨은 외부 작업 없이 나뉘는지 검증한다."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException

import src.api.main as api_main
from src.api.main import DbSearchBody
from src.riot_client import RiotApiError


class StartupReadContractTests(unittest.TestCase):
    """읽기 API가 시작 준비를 몰래 대신하지 않아야 한다."""

    def test_meta_get_requires_explicit_startup_without_refreshing_resources(
        self,
    ) -> None:
        with (
            patch.object(api_main, "_data_dragon_checked", False),
            patch.object(api_main, "_ensure_data_dragon_checked") as ensure,
            self.assertRaises(HTTPException) as raised,
        ):
            api_main.get_options()

        self.assertEqual(raised.exception.status_code, 409)
        ensure.assert_not_called()


class MatchDetailRouteContractTests(unittest.TestCase):
    """기본·빌드·랭크 상세가 필요한 자료만 확인해야 한다."""

    def setUp(self) -> None:
        self.cache = Mock()
        self.match = {"metadata": {"matchId": "KR_1"}}
        self.focus = {
            "queue_id": 420,
            "game_duration": 1_800,
            "game_version": "16.1.1",
            "me": {"puuid": "me", "champion_key": "Ahri"},
            "enemy_laner": None,
            "others_ally": [],
            "others_enemy": [],
        }
        self.players = ({"puuid": "me"}, None, [], [])

    def test_base_detail_never_loads_timeline_or_ranks(self) -> None:
        with (
            patch.object(api_main, "_data_dragon_checked", True),
            patch.object(
                api_main,
                "_load_match_focus",
                return_value=(self.cache, self.match, self.focus),
            ),
            patch.object(
                api_main,
                "_focus_players_with_assets",
                return_value=self.players,
            ),
            patch.object(api_main, "fetch_match_timeline") as timeline,
            patch.object(api_main, "_attach_rank_profiles") as ranks,
        ):
            payload = api_main.get_match_detail("KR_1", "me")

        timeline.assert_not_called()
        ranks.assert_not_called()
        self.assertEqual(payload["detailLoad"]["timelineSource"], "not_requested")
        self.assertEqual(payload["detailLoad"]["externalApiCalls"], 0)

    def test_build_detail_does_not_load_ranks(self) -> None:
        self.cache.get_match_timeline.return_value = {"info": {"frames": []}}
        with (
            patch.object(api_main, "_data_dragon_checked", True),
            patch.object(
                api_main,
                "_load_match_focus",
                return_value=(self.cache, self.match, self.focus),
            ),
            patch.object(api_main, "extract_player_build_timeline", return_value=None),
            patch.object(api_main, "_attach_rank_profiles") as ranks,
        ):
            payload = api_main.get_match_build_detail("KR_1", "me")

        ranks.assert_not_called()
        self.assertEqual(payload["detailLoad"]["timelineSource"], "stored")

    def test_rank_detail_does_not_load_timeline(self) -> None:
        with (
            patch.object(api_main, "_data_dragon_checked", True),
            patch.object(
                api_main,
                "_load_match_focus",
                return_value=(self.cache, self.match, self.focus),
            ),
            patch.object(
                api_main,
                "_focus_players_with_assets",
                return_value=self.players,
            ),
            patch.object(
                api_main,
                "_attach_rank_profiles",
                return_value={"cache_hits": 1, "api_calls": 0, "error": None},
            ),
            patch.object(api_main, "fetch_match_timeline") as timeline,
        ):
            payload = api_main.get_match_rank_detail("KR_1", "me")

        timeline.assert_not_called()
        self.assertEqual(payload["detailLoad"]["rankCacheHits"], 1)

    def test_detail_before_startup_stops_before_loading_assets(self) -> None:
        with (
            patch.object(api_main, "_data_dragon_checked", False),
            patch.object(api_main, "_load_match_focus") as load,
            self.assertRaises(HTTPException) as raised,
        ):
            api_main.get_match_detail("KR_1", "me")

        self.assertEqual(raised.exception.status_code, 409)
        load.assert_not_called()

    def test_rank_api_error_keeps_unchecked_player_label(self) -> None:
        class FakeClient:
            request_count = 1

            def __enter__(self) -> "FakeClient":
                return self

            def __exit__(self, *_args: object) -> None:
                pass

        players = [{"puuid": "one"}, {"puuid": "two"}]
        cache = Mock()
        cache.get_ranked_profile.return_value = None
        config = SimpleNamespace(
            api_key="test-key",
            region="asia",
            platform="kr",
            queue_id=420,
        )
        with (
            patch.object(api_main, "RiotClient", return_value=FakeClient()),
            patch.object(
                api_main,
                "fetch_ranked_profile",
                side_effect=RiotApiError("일시적인 랭크 오류"),
            ),
        ):
            result = api_main._attach_rank_profiles(
                players,
                cache=cache,
                config=config,
            )

        self.assertEqual(result["error"], "일시적인 랭크 오류")
        self.assertEqual(
            [player["rankLabel"] for player in players],
            ["확인하지 못함", "확인하지 못함"],
        )


class DbSnapshotRequestTests(unittest.TestCase):
    """페이지와 내보내기가 처음 확정한 조회 기준을 재사용해야 한다."""

    def test_snapshot_request_does_not_resolve_users_again(self) -> None:
        body = DbSearchBody(
            myChampionKorean="전체",
            enemyChampionKorean="전체",
            laneLabel="미드",
            periodKind="전체 기간",
            riotIdsRaw="Changed#KR1",
            snapshot={
                "targetPuuids": ["fixed-puuid"],
                "startTs": 100,
                "endTs": 200,
                "patchPrefix": "16.1",
                "maxIndexId": 321,
                "targetLabel": "입력 유저 1명",
            },
        )
        cache = Mock()
        with patch.object(api_main, "_resolve_db_target_users") as resolve:
            request = api_main._db_request(body, cache=cache)

        resolve.assert_not_called()
        cache.list_player_memos.assert_not_called()
        self.assertEqual(request.target_puuids, ("fixed-puuid",))
        self.assertEqual(request.fixed_start_ts, 100)
        self.assertEqual(request.fixed_end_ts, 200)
        self.assertEqual(request.fixed_patch_prefix, "16.1")
        self.assertEqual(request.snapshot_max_index_id, 321)


if __name__ == "__main__":
    unittest.main()
