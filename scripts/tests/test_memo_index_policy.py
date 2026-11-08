"""관심 유저 경기의 티어 예외와 사람 단위 조회 대상을 검증한다."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import src.api.main as api_main  # noqa: E402
from src.api.main import DbSearchBody  # noqa: E402
from src.cache import MatchCache  # noqa: E402
from src.search_service import index_memo_player_matches  # noqa: E402


def _match(match_id: str, player: str, enemy: str) -> dict[str, Any]:
    """미드 라이너 두 명만 있는 최소 솔로 랭크 경기를 만든다."""

    def participant(puuid: str, team_id: int) -> dict[str, Any]:
        return {
            "puuid": puuid,
            "teamId": team_id,
            "teamPosition": "MIDDLE",
            "championName": "Ahri",
            "riotIdGameName": puuid,
            "riotIdTagline": "KR1",
            "win": team_id == 100,
        }

    return {
        "metadata": {"matchId": match_id},
        "info": {
            "queueId": 420,
            "gameCreation": 1_700_000_000_000,
            "gameDuration": 1800,
            "gameVersion": "16.22.1",
            "participants": [participant(player, 100), participant(enemy, 200)],
        },
    }


class MemoIndexBackfillTests(unittest.TestCase):
    """메모가 있는 유저의 저장 경기는 티어와 관계없이 조회 항목이 된다."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.cache = MatchCache(str(Path(self.temp_dir.name) / "cache.db"))

    def _save_discovered(self, match_id: str, source_puuid: str) -> None:
        self.cache.save_match(match_id, _match(match_id, source_puuid, "enemy"))
        self.cache.record_match_discoveries(
            match_ids=[match_id],
            source_puuid=source_puuid,
            source="manual_user",
        )

    def test_memo_player_cached_matches_are_indexed(self) -> None:
        self._save_discovered("KR_1", "memo-low-tier")
        self.cache.upsert_player_memo("memo-low-tier", "부계정")

        added = index_memo_player_matches(self.cache, 420)

        self.assertEqual(added, 2)
        self.assertEqual(self.cache.list_unindexed_memo_match_ids(), [])
        self.assertEqual(self.cache.get_indexed_match_ids(["KR_1"]), {"KR_1"})

    def test_players_without_memo_stay_unindexed(self) -> None:
        self._save_discovered("KR_2", "stranger")

        self.assertEqual(index_memo_player_matches(self.cache, 420), 0)
        self.assertEqual(self.cache.get_indexed_match_ids(["KR_2"]), set())

    def test_single_player_fill_leaves_other_memo_players(self) -> None:
        self._save_discovered("KR_3", "memo-a")
        self._save_discovered("KR_4", "memo-b")
        self.cache.upsert_player_memo("memo-a", "A")
        self.cache.upsert_player_memo("memo-b", "B")

        index_memo_player_matches(self.cache, 420, "memo-a")

        self.assertEqual(self.cache.list_unindexed_memo_match_ids(), ["KR_4"])

    def test_memo_list_counts_only_each_memo_players_games(self) -> None:
        self._save_discovered("KR_5", "memo-c")
        self.cache.upsert_player_memo("memo-c", "C")
        index_memo_player_matches(self.cache, 420)

        items = self.cache.list_player_memos()

        self.assertEqual([item["puuid"] for item in items], ["memo-c"])
        self.assertEqual(items[0]["game_count"], 1)


class StatShardBackfillTests(unittest.TestCase):
    """예전 조회 항목의 능력치 파편은 시작 시 한 번 채워 재사용한다."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.cache = MatchCache(str(Path(self.temp_dir.name) / "cache.db"))
        match = _match("KR_OLD", "player", "enemy")
        for participant in match["info"]["participants"]:
            participant["perks"] = {
                "statPerks": {"offense": 5008, "flex": 5010, "defense": 5011}
            }
        self.cache.save_match("KR_OLD", match)
        self.cache.index_match(match)
        with self.cache._connect() as conn:
            conn.execute(
                """
                UPDATE matchup_index
                SET player_stat_offense = NULL,
                    player_stat_flex = NULL,
                    player_stat_defense = NULL
                """
            )
            conn.commit()

    def _player_row(self) -> dict[str, Any]:
        return self.cache.search_matchup_index(
            player_champion_key=None,
            enemy_champion_key=None,
            lane="MIDDLE",
            start_ts=None,
            end_ts=None,
            patch_prefix=None,
            player_puuids=["player"],
        )[0]

    def test_backfill_fills_shards_from_the_saved_match(self) -> None:
        self.assertEqual(self.cache.backfill_matchup_stat_shards(), 2)

        row = self._player_row()
        self.assertEqual(
            (row["my_stat_offense"], row["my_stat_flex"], row["my_stat_defense"]),
            (5008, 5010, 5011),
        )

    def test_second_backfill_has_nothing_left_to_fill(self) -> None:
        self.cache.backfill_matchup_stat_shards()

        self.assertEqual(self.cache.backfill_matchup_stat_shards(), 0)


class PersonTargetRequestTests(unittest.TestCase):
    """사람 칩으로 고른 계정은 Riot ID 해석 없이 조회 대상이 된다."""

    def _request(self, memos: dict[str, str], **fields: Any) -> Any:
        cache = Mock()
        cache.get_player_memos.return_value = memos
        body = DbSearchBody(
            myChampionKorean="전체",
            enemyChampionKorean="전체",
            laneLabel="미드",
            **fields,
        )
        return api_main._db_request(body, cache=cache)

    def test_one_person_groups_all_accounts_under_memo(self) -> None:
        request = self._request(
            {"a": "하얗고예쁨", "b": "하얗고예쁨"},
            targetPuuids=["a", "b"],
        )

        self.assertEqual(request.target_puuids, ("a", "b"))
        self.assertEqual(request.target_label, "하얗고예쁨 · 계정 2개")

    def test_several_people_are_counted_by_person(self) -> None:
        request = self._request(
            {"a": "베베핑크", "b": "베베핑크", "c": "페이커"},
            targetPuuids=["a", "b", "c"],
        )

        self.assertEqual(request.target_label, "관심 유저 2명")

    def test_no_selection_keeps_all_memo_players(self) -> None:
        cache = Mock()
        cache.list_player_memos.return_value = [{"puuid": "a"}, {"puuid": "b"}]
        body = DbSearchBody(
            myChampionKorean="전체",
            enemyChampionKorean="전체",
            laneLabel="미드",
        )

        request = api_main._db_request(body, cache=cache)

        self.assertEqual(request.target_puuids, ("a", "b"))
        self.assertEqual(request.target_label, "관심 유저 2명")


if __name__ == "__main__":
    unittest.main()
