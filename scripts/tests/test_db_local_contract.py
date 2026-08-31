"""저장된 매치업 조회가 Riot API를 호출하지 않는지 검증한다."""

from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import src.api.main as api_main
from src.api.main import DbSearchBody
from src.riot_client import RiotApiError


def _body(riot_ids_raw: str) -> DbSearchBody:
    """필수 필터와 Riot ID를 담은 DB 조회 본문을 만든다."""
    return DbSearchBody(
        myChampionKorean="전체",
        enemyChampionKorean="전체",
        laneLabel="탑",
        riotIdsRaw=riot_ids_raw,
    )


def _resolve(body: DbSearchBody, cache: Mock) -> tuple[tuple[str, ...], int, int]:
    """해석기에서 기존 config 인자가 제거되는 과정도 호환한다."""
    kwargs: dict[str, object] = {"cache": cache}
    if "config" in inspect.signature(api_main._resolve_db_target_users).parameters:
        kwargs["config"] = SimpleNamespace(
            api_key="configured-but-must-not-be-used",
            region="asia",
            platform="kr",
        )
    return api_main._resolve_db_target_users(body, **kwargs)


class DbLocalOnlyContractTests(unittest.TestCase):
    """직접 입력한 Riot ID도 로컬에서만 찾아야 한다."""

    def test_account_cache_hit_never_opens_riot_client(self) -> None:
        cache = Mock()
        cache.get_account.return_value = {
            "puuid": "cached-puuid",
            "game_name": "Cached",
            "tag_line": "KR1",
        }

        with patch.object(
            api_main,
            "RiotClient",
            side_effect=AssertionError("DB 조회에서 RiotClient를 열면 안 돼요."),
        ) as riot_client:
            puuids, local_hits, api_calls = _resolve(_body("Cached#KR1"), cache)

        self.assertEqual(puuids, ("cached-puuid",))
        self.assertEqual(local_hits, 1)
        self.assertEqual(api_calls, 0)
        cache.find_players_by_riot_id.assert_not_called()
        riot_client.assert_not_called()

    def test_player_registry_is_the_local_fallback(self) -> None:
        cache = Mock()
        cache.get_account.return_value = None
        cache.find_players_by_riot_id.return_value = [
            {
                "puuid": "registered-puuid",
                "riot_id_game_name": "Registered",
                "riot_id_tag_line": "KR1",
            }
        ]

        with patch.object(
            api_main,
            "RiotClient",
            side_effect=AssertionError("DB 조회에서 RiotClient를 열면 안 돼요."),
        ) as riot_client:
            puuids, local_hits, api_calls = _resolve(
                _body("Registered#KR1"),
                cache,
            )

        self.assertEqual(puuids, ("registered-puuid",))
        self.assertEqual(local_hits, 1)
        self.assertEqual(api_calls, 0)
        cache.find_players_by_riot_id.assert_called_once_with("Registered", "KR1")
        cache.save_account.assert_not_called()
        riot_client.assert_not_called()

    def test_unknown_user_explains_how_to_collect_data_without_calling_api(
        self,
    ) -> None:
        cache = Mock()
        cache.get_account.return_value = None
        cache.find_players_by_riot_id.return_value = []

        with (
            patch.object(
                api_main,
                "RiotClient",
                side_effect=AssertionError("DB 조회에서 RiotClient를 열면 안 돼요."),
            ) as riot_client,
            self.assertRaises(RiotApiError) as raised,
        ):
            _resolve(_body("Unknown#KR1"), cache)

        message = str(raised.exception)
        self.assertIn("Unknown#KR1", message)
        self.assertIn("저장된 유저", message)
        self.assertIn("새 데이터 가져오기", message)
        cache.save_account.assert_not_called()
        riot_client.assert_not_called()

    def test_duplicate_puuid_is_returned_once_in_input_order(self) -> None:
        cache = Mock()
        cache.get_account.side_effect = [
            {
                "puuid": "same-puuid",
                "game_name": "FirstName",
                "tag_line": "KR1",
            },
            None,
        ]
        cache.find_players_by_riot_id.return_value = [
            {
                "puuid": "same-puuid",
                "riot_id_game_name": "OldName",
                "riot_id_tag_line": "KR1",
            }
        ]

        with patch.object(
            api_main,
            "RiotClient",
            side_effect=AssertionError("DB 조회에서 RiotClient를 열면 안 돼요."),
        ) as riot_client:
            puuids, local_hits, api_calls = _resolve(
                _body("FirstName#KR1\nOldName#KR1"),
                cache,
            )

        self.assertEqual(puuids, ("same-puuid",))
        self.assertEqual(local_hits, 2)
        self.assertEqual(api_calls, 0)
        riot_client.assert_not_called()

    def test_ambiguous_saved_riot_id_stops_instead_of_choosing_a_puuid(self) -> None:
        cache = Mock()
        cache.get_account.return_value = None
        cache.find_players_by_riot_id.return_value = [
            {"puuid": "old-owner"},
            {"puuid": "new-owner"},
        ]

        with self.assertRaises(RiotApiError) as raised:
            _resolve(_body("Shared#KR1"), cache)

        self.assertIn("서로 다른 저장 유저 2명", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
