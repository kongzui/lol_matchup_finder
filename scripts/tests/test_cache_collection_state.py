"""관심 유저와 저장 경기 현황의 SQLite 계약을 검증한다."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.cache import MatchCache


class CacheCollectionStateTests(unittest.TestCase):
    """삭제와 수집 상태를 서로 다른 정보로 보관해야 한다."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "cache.db"
        self.cache = MatchCache(str(self.db_path))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_empty_memo_keeps_the_interested_player(self) -> None:
        self.cache.save_player_identity(
            puuid="player",
            game_name="Player",
            tag_line="KR1",
        )
        self.cache.upsert_player_memo("player", "   ")

        items = self.cache.list_player_memos()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["puuid"], "player")
        self.assertEqual(items[0]["memo"], "")

    def test_collection_cursor_keeps_offset_and_clears_on_completion(self) -> None:
        self.cache.save_manual_collection_user(
            puuid="player",
            game_name="Player",
            tag_line="KR1",
        )
        self.cache.save_manual_collection_cursor(
            puuid="player",
            start_at=100,
            end_at=900,
            target_at=900,
            offset=200,
            queue_id=420,
            region="asia",
            matches_per_player=100,
        )

        self.assertEqual(
            self.cache.get_manual_collection_cursor("player"),
            {
                "start_at": 100,
                "end_at": 900,
                "target_at": 900,
                "offset": 200,
                "queue_id": 420,
                "region": "asia",
                "matches_per_player": 100,
            },
        )

        self.cache.mark_manual_collection_user_collected(
            puuid="player",
            collected_at=900,
        )
        self.assertIsNone(self.cache.get_manual_collection_cursor("player"))

    def test_raw_and_queryable_match_counts_are_separate(self) -> None:
        self.cache.save_match(
            "KR_RAW",
            {
                "metadata": {"matchId": "KR_RAW"},
                "info": {"queueId": 420, "gameCreation": 1_000_000},
            },
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO matchup_index (
                    match_id, game_creation, player_puuid, win, indexed_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                ("KR_INDEXED", 2_000_000, "player", 1, 2_000),
            )
            conn.commit()

        stats = self.cache.get_match_cache_stats()

        self.assertEqual(stats["match_count"], 1)
        self.assertEqual(stats["indexed_match_count"], 1)
        self.assertEqual(stats["indexed_row_count"], 1)
        self.assertEqual(stats["last_indexed_match_creation"], 2_000_000)

    def test_matchup_page_does_not_load_every_matching_row(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO matchup_index (
                    match_id, game_creation, lane, player_puuid, win, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (f"KR_{index}", index * 1_000, "MIDDLE", "player", index % 2, 1)
                    for index in range(1, 5)
                ],
            )
            conn.commit()

        kwargs = {
            "player_champion_key": None,
            "enemy_champion_key": None,
            "lane": "MIDDLE",
            "start_ts": None,
            "end_ts": None,
            "patch_prefix": None,
        }
        rows = self.cache.search_matchup_index(
            **kwargs,
            limit=2,
            offset=1,
        )
        stats = self.cache.get_matchup_index_stats(**kwargs)

        self.assertEqual([row["match_id"] for row in rows], ["KR_3", "KR_2"])
        self.assertEqual(stats, {"total": 4, "wins": 2, "losses": 2})

    def test_matchup_pages_keep_the_same_snapshot_when_new_rows_arrive(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO matchup_index (
                    match_id, game_creation, lane, player_puuid, win, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (f"KR_{index}", index * 1_000, "MIDDLE", "player", 1, 1)
                    for index in range(1, 5)
                ],
            )
            conn.commit()

        snapshot_max_id = self.cache.get_matchup_index_max_id()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO matchup_index (
                    match_id, game_creation, lane, player_puuid, win, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("KR_NEW", 99_000, "MIDDLE", "player", 1, 2),
            )
            conn.commit()

        rows = self.cache.search_matchup_index(
            player_champion_key=None,
            enemy_champion_key=None,
            lane="MIDDLE",
            start_ts=None,
            end_ts=None,
            patch_prefix=None,
            max_index_id=snapshot_max_id,
            limit=2,
            offset=2,
        )

        self.assertEqual([row["match_id"] for row in rows], ["KR_2", "KR_1"])

    def test_full_copy_reads_only_distinct_enemy_riot_ids(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO matchup_index (
                    match_id, game_creation, lane, player_puuid,
                    enemy_riot_id, enemy_game_name, enemy_tag_line,
                    win, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "KR_1",
                        1_000,
                        "MIDDLE",
                        "player",
                        "Enemy#KR1",
                        "Enemy",
                        "KR1",
                        1,
                        1,
                    ),
                    (
                        "KR_2",
                        3_000,
                        "MIDDLE",
                        "player",
                        "enemy#kr1",
                        "enemy",
                        "kr1",
                        1,
                        1,
                    ),
                    (
                        "KR_3",
                        2_000,
                        "MIDDLE",
                        "player",
                        None,
                        "Second",
                        "KR1",
                        1,
                        1,
                    ),
                    (
                        "KR_4",
                        9_000,
                        "TOP",
                        "player",
                        "WrongLane#KR1",
                        "WrongLane",
                        "KR1",
                        1,
                        1,
                    ),
                    (
                        "KR_5",
                        4_000,
                        "MIDDLE",
                        "player",
                        "Straße#EUW",
                        "Straße",
                        "EUW",
                        1,
                        1,
                    ),
                    (
                        "KR_6",
                        5_000,
                        "MIDDLE",
                        "player",
                        "STRASSE#euw",
                        "STRASSE",
                        "euw",
                        1,
                        1,
                    ),
                ],
            )
            conn.commit()

        values = self.cache.search_matchup_enemy_riot_ids(
            player_champion_key=None,
            enemy_champion_key=None,
            lane="MIDDLE",
            start_ts=None,
            end_ts=None,
            patch_prefix=None,
        )

        self.assertEqual(len(values), 3)
        self.assertEqual(values[0], "STRASSE#euw")
        self.assertEqual(values[1], "enemy#kr1")
        self.assertEqual(values[2], "Second#KR1")


if __name__ == "__main__":
    unittest.main()
