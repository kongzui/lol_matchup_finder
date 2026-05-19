"""SQLite 기반 캐시.

- account_cache: Riot ID → PUUID
- match_cache: matchId → 매치 상세 raw JSON
- match_timeline_cache: matchId → 매치 타임라인 raw JSON
- search_history: 검색 조건 기록 (디버깅/재검색용)
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any

from .utils import format_riot_id, unix_to_kst_date_str


# Riot ID는 변경 가능하므로 account_cache는 영구 보관하지 않는다.
ACCOUNT_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
SUMMONER_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
RANKED_PROFILE_CACHE_TTL_SECONDS = 24 * 60 * 60


class MatchCache:
    """SQLite에 PUUID/매치 상세를 캐시한다."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._ensure_parent_dir()
        self._init_schema()

    def _ensure_parent_dir(self) -> None:
        parent = os.path.dirname(os.path.abspath(self._db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS account_cache (
                    riot_id TEXT PRIMARY KEY,
                    puuid TEXT NOT NULL,
                    game_name TEXT,
                    tag_line TEXT,
                    fetched_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS match_cache (
                    match_id TEXT PRIMARY KEY,
                    queue_id INTEGER,
                    game_creation INTEGER,
                    raw_json TEXT NOT NULL,
                    fetched_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS match_timeline_cache (
                    match_id TEXT PRIMARY KEY,
                    raw_json TEXT NOT NULL,
                    fetched_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS summoner_cache (
                    summoner_id TEXT PRIMARY KEY,
                    puuid TEXT NOT NULL,
                    fetched_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS search_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_riot_id TEXT NOT NULL,
                    target_puuid TEXT NOT NULL,
                    queue_id INTEGER NOT NULL,
                    my_champion TEXT NOT NULL,
                    enemy_champion TEXT NOT NULL,
                    lane TEXT NOT NULL,
                    start_time INTEGER NOT NULL,
                    end_time INTEGER NOT NULL,
                    result_count INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS player_registry (
                    puuid TEXT PRIMARY KEY,
                    riot_id_game_name TEXT,
                    riot_id_tag_line TEXT,
                    summoner_name TEXT,
                    first_seen_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL
                );

                DROP TABLE IF EXISTS collection_seed;
                DROP TABLE IF EXISTS challenger_snapshot_runs;
                DROP TABLE IF EXISTS challenger_player_snapshots;
                DROP TABLE IF EXISTS challenger_players_current;

                CREATE TABLE IF NOT EXISTS ranked_profile_cache (
                    puuid TEXT NOT NULL,
                    queue_id INTEGER NOT NULL,
                    tier TEXT,
                    rank TEXT,
                    league_points INTEGER,
                    wins INTEGER,
                    losses INTEGER,
                    fetched_at INTEGER NOT NULL,
                    PRIMARY KEY (puuid, queue_id)
                );

                CREATE TABLE IF NOT EXISTS match_discovery (
                    match_id TEXT NOT NULL,
                    source_puuid TEXT NOT NULL,
                    source TEXT NOT NULL,
                    discovered_at INTEGER NOT NULL,
                    PRIMARY KEY (match_id, source_puuid, source)
                );

                DELETE FROM match_discovery
                WHERE source = 'challenger';

                CREATE TABLE IF NOT EXISTS manual_collection_user (
                    puuid TEXT PRIMARY KEY,
                    riot_id_game_name TEXT,
                    riot_id_tag_line TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    last_collected_at INTEGER,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS matchup_index (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    match_id TEXT NOT NULL,
                    queue_id INTEGER,
                    game_creation INTEGER,
                    game_version TEXT,
                    lane TEXT,
                    player_puuid TEXT NOT NULL,
                    player_riot_id TEXT,
                    player_game_name TEXT,
                    player_tag_line TEXT,
                    player_champion_key TEXT,
                    enemy_puuid TEXT,
                    enemy_riot_id TEXT,
                    enemy_game_name TEXT,
                    enemy_tag_line TEXT,
                    enemy_champion_key TEXT,
                    win INTEGER NOT NULL,
                    kills INTEGER NOT NULL DEFAULT 0,
                    deaths INTEGER NOT NULL DEFAULT 0,
                    assists INTEGER NOT NULL DEFAULT 0,
                    cs INTEGER NOT NULL DEFAULT 0,
                    gold_earned INTEGER NOT NULL DEFAULT 0,
                    damage_to_champions INTEGER NOT NULL DEFAULT 0,
                    game_duration INTEGER NOT NULL DEFAULT 0,
                    player_champion_level INTEGER NOT NULL DEFAULT 0,
                    player_summoner1_id INTEGER,
                    player_summoner2_id INTEGER,
                    player_items_json TEXT NOT NULL DEFAULT '[]',
                    player_primary_tree_id INTEGER,
                    player_primary_runes_json TEXT NOT NULL DEFAULT '[]',
                    player_secondary_tree_id INTEGER,
                    player_secondary_runes_json TEXT NOT NULL DEFAULT '[]',
                    indexed_at INTEGER NOT NULL,
                    UNIQUE (match_id, player_puuid)
                );

                CREATE INDEX IF NOT EXISTS idx_matchup_index_search
                    ON matchup_index (
                        lane,
                        player_champion_key,
                        enemy_champion_key,
                        game_creation
                    );

                CREATE INDEX IF NOT EXISTS idx_matchup_index_match
                    ON matchup_index (match_id);

                CREATE TABLE IF NOT EXISTS player_champion_stats (
                    puuid TEXT NOT NULL,
                    champion_key TEXT NOT NULL,
                    games INTEGER NOT NULL DEFAULT 0,
                    wins INTEGER NOT NULL DEFAULT 0,
                    last_played_at INTEGER,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (puuid, champion_key)
                );
                """
            )
            conn.commit()

    @property
    def db_path(self) -> str:
        return self._db_path

    # --- account_cache ---
    def get_account(self, riot_id_key: str) -> dict[str, Any] | None:
        """riot_id_key는 'gameName#tagLine'을 lower-case 정규화한 값이어야 한다."""
        now = int(time.time())
        with self._connect() as conn:
            row = conn.execute(
                "SELECT puuid, game_name, tag_line, fetched_at FROM account_cache "
                "WHERE riot_id = ?",
                (riot_id_key,),
            ).fetchone()

        if row is None:
            return None

        if now - row["fetched_at"] > ACCOUNT_CACHE_TTL_SECONDS:
            return None

        return {
            "puuid": row["puuid"],
            "game_name": row["game_name"],
            "tag_line": row["tag_line"],
            "fetched_at": row["fetched_at"],
        }

    def save_account(
        self,
        riot_id_key: str,
        puuid: str,
        game_name: str | None,
        tag_line: str | None,
    ) -> None:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO account_cache (riot_id, puuid, game_name, tag_line, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(riot_id) DO UPDATE SET
                    puuid = excluded.puuid,
                    game_name = excluded.game_name,
                    tag_line = excluded.tag_line,
                    fetched_at = excluded.fetched_at
                """,
                (riot_id_key, puuid, game_name, tag_line, now),
            )
            conn.commit()

    # --- match_cache ---
    def get_match(self, match_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT raw_json FROM match_cache WHERE match_id = ?",
                (match_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["raw_json"])
        except json.JSONDecodeError:
            return None

    def save_match(self, match_id: str, match: dict[str, Any]) -> None:
        info = match.get("info", {}) if isinstance(match, dict) else {}
        queue_id = info.get("queueId")
        game_creation = info.get("gameCreation")
        raw_json = json.dumps(match, ensure_ascii=False)
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO match_cache
                    (match_id, queue_id, game_creation, raw_json, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(match_id) DO UPDATE SET
                    queue_id = excluded.queue_id,
                    game_creation = excluded.game_creation,
                    raw_json = excluded.raw_json,
                    fetched_at = excluded.fetched_at
                """,
                (match_id, queue_id, game_creation, raw_json, now),
            )
            conn.commit()

    # --- match_timeline_cache ---
    def get_match_timeline(self, match_id: str) -> dict[str, Any] | None:
        """matchId로 저장된 타임라인 JSON을 가져온다."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT raw_json FROM match_timeline_cache WHERE match_id = ?",
                (match_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["raw_json"])
        except json.JSONDecodeError:
            return None

    def save_match_timeline(self, match_id: str, timeline: dict[str, Any]) -> None:
        """matchId별 타임라인 JSON을 캐시한다."""
        raw_json = json.dumps(timeline, ensure_ascii=False)
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO match_timeline_cache (match_id, raw_json, fetched_at)
                VALUES (?, ?, ?)
                ON CONFLICT(match_id) DO UPDATE SET
                    raw_json = excluded.raw_json,
                    fetched_at = excluded.fetched_at
                """,
                (match_id, raw_json, now),
            )
            conn.commit()

    # --- summoner_cache ---
    def get_summoner_puuid(self, summoner_id: str) -> str | None:
        """encryptedSummonerId로 캐시된 PUUID를 가져온다."""
        now = int(time.time())
        with self._connect() as conn:
            row = conn.execute(
                "SELECT puuid, fetched_at FROM summoner_cache WHERE summoner_id = ?",
                (summoner_id,),
            ).fetchone()

        if row is None:
            return None
        if now - row["fetched_at"] > SUMMONER_CACHE_TTL_SECONDS:
            return None
        return row["puuid"]

    def save_summoner_puuid(self, summoner_id: str, puuid: str) -> None:
        """encryptedSummonerId와 PUUID 매핑을 저장한다."""
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO summoner_cache (summoner_id, puuid, fetched_at)
                VALUES (?, ?, ?)
                ON CONFLICT(summoner_id) DO UPDATE SET
                    puuid = excluded.puuid,
                    fetched_at = excluded.fetched_at
                """,
                (summoner_id, puuid, now),
            )
            conn.commit()

    # --- search_history ---
    def record_search(
        self,
        target_riot_id: str,
        target_puuid: str,
        queue_id: int,
        my_champion: str,
        enemy_champion: str,
        lane: str,
        start_time: int,
        end_time: int,
        result_count: int,
    ) -> None:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO search_history (
                    target_riot_id, target_puuid, queue_id,
                    my_champion, enemy_champion, lane,
                    start_time, end_time, result_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_riot_id,
                    target_puuid,
                    queue_id,
                    my_champion,
                    enemy_champion,
                    lane,
                    start_time,
                    end_time,
                    result_count,
                    now,
                ),
            )
            conn.commit()

    def get_latest_search_riot_id(self) -> str | None:
        """가장 최근 검색한 Riot ID를 반환한다."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT target_riot_id
                FROM search_history
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return row["target_riot_id"]

    # --- ranked_profile_cache ---
    def get_ranked_profile(self, puuid: str, queue_id: int) -> dict[str, Any] | None:
        """캐시된 랭크 프로필을 가져온다."""
        now = int(time.time())
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT tier, rank, league_points, wins, losses, fetched_at
                FROM ranked_profile_cache
                WHERE puuid = ? AND queue_id = ?
                """,
                (puuid, queue_id),
            ).fetchone()

        if row is None:
            return None
        if now - row["fetched_at"] > RANKED_PROFILE_CACHE_TTL_SECONDS:
            return None
        return dict(row)

    def save_ranked_profile(
        self,
        *,
        puuid: str,
        queue_id: int,
        tier: str | None,
        rank: str | None,
        league_points: int | None,
        wins: int | None,
        losses: int | None,
    ) -> None:
        """랭크 프로필을 저장한다. 랭크 정보가 없어도 조회 결과로 캐시한다."""
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ranked_profile_cache (
                    puuid, queue_id, tier, rank, league_points, wins, losses, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(puuid, queue_id) DO UPDATE SET
                    tier = excluded.tier,
                    rank = excluded.rank,
                    league_points = excluded.league_points,
                    wins = excluded.wins,
                    losses = excluded.losses,
                    fetched_at = excluded.fetched_at
                """,
                (puuid, queue_id, tier, rank, league_points, wins, losses, now),
            )
            conn.commit()

    # --- match_discovery / manual_collection_user ---
    def record_match_discovery(
        self,
        *,
        match_id: str,
        source_puuid: str,
        source: str,
    ) -> None:
        """matchId를 어떤 흐름에서 발견했는지 저장한다."""
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO match_discovery (
                    match_id, source_puuid, source, discovered_at
                ) VALUES (?, ?, ?, ?)
                """,
                (match_id, source_puuid, source, now),
            )
            conn.commit()

    def save_manual_collection_user(
        self,
        *,
        puuid: str,
        game_name: str | None,
        tag_line: str | None,
    ) -> None:
        """멀티서치 수동 수집 대상 유저를 저장한다."""
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO manual_collection_user (
                    puuid, riot_id_game_name, riot_id_tag_line,
                    is_active, created_at, updated_at
                ) VALUES (?, ?, ?, 1, ?, ?)
                ON CONFLICT(puuid) DO UPDATE SET
                    riot_id_game_name = excluded.riot_id_game_name,
                    riot_id_tag_line = excluded.riot_id_tag_line,
                    is_active = 1,
                    updated_at = excluded.updated_at
                """,
                (puuid, game_name, tag_line, now, now),
            )
            conn.commit()

    def get_manual_collection_users(self) -> list[dict[str, Any]]:
        """활성 멀티서치 수동 수집 대상 유저 목록을 반환한다."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    puuid,
                    riot_id_game_name,
                    riot_id_tag_line,
                    last_collected_at
                FROM manual_collection_user
                WHERE is_active = 1
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_manual_collection_user_collected(
        self,
        *,
        puuid: str,
        collected_at: int | None = None,
    ) -> None:
        """멀티서치 수동 수집 대상의 마지막 수집 시각을 갱신한다."""
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE manual_collection_user
                SET last_collected_at = ?, updated_at = ?
                WHERE puuid = ?
                """,
                (collected_at or now, now, puuid),
            )
            conn.commit()

    # --- matchup_index ---
    def index_match(self, match: dict[str, Any], allowed_queue_id: int = 420) -> int:
        """매치 상세 JSON을 검색용 matchup_index row로 변환해 저장한다."""
        if not isinstance(match, dict):
            return 0

        info = match.get("info") or {}
        if info.get("queueId") != allowed_queue_id:
            return 0

        rows = _build_matchup_index_rows(match)
        if not rows:
            return 0

        now = int(time.time())
        with self._connect() as conn:
            for participant in info.get("participants") or []:
                _upsert_player_registry(conn, participant, now)

            for row in rows:
                conn.execute(
                    """
                    INSERT INTO matchup_index (
                        match_id, queue_id, game_creation, game_version, lane,
                        player_puuid, player_riot_id, player_game_name,
                        player_tag_line, player_champion_key,
                        enemy_puuid, enemy_riot_id, enemy_game_name,
                        enemy_tag_line, enemy_champion_key,
                        win, kills, deaths, assists, cs, gold_earned,
                        damage_to_champions, game_duration, player_champion_level,
                        player_summoner1_id, player_summoner2_id,
                        player_items_json, player_primary_tree_id,
                        player_primary_runes_json, player_secondary_tree_id,
                        player_secondary_runes_json, indexed_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    ON CONFLICT(match_id, player_puuid) DO UPDATE SET
                        queue_id = excluded.queue_id,
                        game_creation = excluded.game_creation,
                        game_version = excluded.game_version,
                        lane = excluded.lane,
                        player_riot_id = excluded.player_riot_id,
                        player_game_name = excluded.player_game_name,
                        player_tag_line = excluded.player_tag_line,
                        player_champion_key = excluded.player_champion_key,
                        enemy_puuid = excluded.enemy_puuid,
                        enemy_riot_id = excluded.enemy_riot_id,
                        enemy_game_name = excluded.enemy_game_name,
                        enemy_tag_line = excluded.enemy_tag_line,
                        enemy_champion_key = excluded.enemy_champion_key,
                        win = excluded.win,
                        kills = excluded.kills,
                        deaths = excluded.deaths,
                        assists = excluded.assists,
                        cs = excluded.cs,
                        gold_earned = excluded.gold_earned,
                        damage_to_champions = excluded.damage_to_champions,
                        game_duration = excluded.game_duration,
                        player_champion_level = excluded.player_champion_level,
                        player_summoner1_id = excluded.player_summoner1_id,
                        player_summoner2_id = excluded.player_summoner2_id,
                        player_items_json = excluded.player_items_json,
                        player_primary_tree_id = excluded.player_primary_tree_id,
                        player_primary_runes_json = excluded.player_primary_runes_json,
                        player_secondary_tree_id = excluded.player_secondary_tree_id,
                        player_secondary_runes_json = excluded.player_secondary_runes_json,
                        indexed_at = excluded.indexed_at
                    """,
                    (
                        row["match_id"],
                        row["queue_id"],
                        row["game_creation"],
                        row["game_version"],
                        row["lane"],
                        row["player_puuid"],
                        row["player_riot_id"],
                        row["player_game_name"],
                        row["player_tag_line"],
                        row["player_champion_key"],
                        row["enemy_puuid"],
                        row["enemy_riot_id"],
                        row["enemy_game_name"],
                        row["enemy_tag_line"],
                        row["enemy_champion_key"],
                        int(row["win"]),
                        row["kills"],
                        row["deaths"],
                        row["assists"],
                        row["cs"],
                        row["gold_earned"],
                        row["damage_to_champions"],
                        row["game_duration"],
                        row["player_champion_level"],
                        row["player_summoner1_id"],
                        row["player_summoner2_id"],
                        json.dumps(row["player_items"], ensure_ascii=False),
                        row["player_primary_tree_id"],
                        json.dumps(row["player_primary_runes"], ensure_ascii=False),
                        row["player_secondary_tree_id"],
                        json.dumps(row["player_secondary_runes"], ensure_ascii=False),
                        now,
                    ),
                )
            _rebuild_player_champion_stats(
                conn,
                {row["player_puuid"] for row in rows},
                now,
            )

            conn.commit()

        return len(rows)

    def search_matchup_index(
        self,
        *,
        player_champion_key: str,
        enemy_champion_key: str | None,
        lane: str,
        start_ts: int | None,
        end_ts: int | None,
        patch_prefix: str | None,
    ) -> list[dict[str, Any]]:
        """matchup_index만 사용해 DB조회 결과를 가져온다."""
        clauses = [
            "mi.player_champion_key = ?",
            "mi.lane = ?",
        ]
        params: list[Any] = [player_champion_key, lane]

        if enemy_champion_key:
            clauses.append("mi.enemy_champion_key = ?")
            params.append(enemy_champion_key)
        if start_ts is not None:
            clauses.append("mi.game_creation >= ?")
            params.append(start_ts * 1000)
        if end_ts is not None:
            clauses.append("mi.game_creation < ?")
            params.append(end_ts * 1000)
        if patch_prefix:
            clauses.append("mi.game_version LIKE ?")
            params.append(f"{patch_prefix}.%")

        where_sql = " AND ".join(clauses)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT mi.*
                FROM matchup_index mi
                WHERE {where_sql}
                ORDER BY mi.game_creation DESC
                """,
                tuple(params),
            ).fetchall()

        return [_indexed_row_to_result(dict(row)) for row in rows]

    def backfill_matchup_index_from_discoveries(
        self,
        *,
        min_tier: str,
        allowed_queue_id: int = 420,
    ) -> int:
        """출처와 캐시된 티어를 확인할 수 있는 match_cache만 보수적으로 백필한다."""
        tier_order = {
            "IRON": 0,
            "BRONZE": 1,
            "SILVER": 2,
            "GOLD": 3,
            "PLATINUM": 4,
            "EMERALD": 5,
            "DIAMOND": 6,
            "MASTER": 7,
            "GRANDMASTER": 8,
            "CHALLENGER": 9,
        }
        min_value = tier_order.get(min_tier.upper(), tier_order["DIAMOND"])

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT mc.raw_json
                FROM match_cache mc
                JOIN match_discovery md ON md.match_id = mc.match_id
                LEFT JOIN ranked_profile_cache rpc
                    ON rpc.puuid = md.source_puuid
                   AND rpc.queue_id = ?
                WHERE md.source = 'manual_multi'
                   OR (
                        md.source = 'manual_user'
                    AND rpc.tier IS NOT NULL
                   )
                """,
                (allowed_queue_id,),
            ).fetchall()

        indexed = 0
        for row in rows:
            try:
                match = json.loads(row["raw_json"])
            except json.JSONDecodeError:
                continue

            discoveries = self._get_match_discoveries(
                ((match.get("metadata") or {}).get("matchId")) or ""
            )
            can_index = any(
                discovery["source"] == "manual_multi" for discovery in discoveries
            )
            if not can_index:
                for discovery in discoveries:
                    if discovery["source"] != "manual_user":
                        continue
                    profile = self.get_ranked_profile(
                        discovery["source_puuid"],
                        allowed_queue_id,
                    )
                    tier = str((profile or {}).get("tier") or "").upper()
                    if tier_order.get(tier, -1) >= min_value:
                        can_index = True
                        break

            if can_index:
                indexed += self.index_match(match, allowed_queue_id=allowed_queue_id)

        return indexed

    def _get_match_discoveries(self, match_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT match_id, source_puuid, source, discovered_at
                FROM match_discovery
                WHERE match_id = ?
                """,
                (match_id,),
            ).fetchall()
        return [dict(row) for row in rows]


def _selection_ids(style: dict[str, Any]) -> list[int]:
    return [
        int(sel.get("perk"))
        for sel in (style.get("selections") or [])
        if sel.get("perk") is not None
    ]


def _rune_fields(participant: dict[str, Any]) -> dict[str, Any]:
    perks = participant.get("perks") or {}
    styles = perks.get("styles") or []
    primary = styles[0] if len(styles) > 0 else {}
    secondary = styles[1] if len(styles) > 1 else {}
    return {
        "primary_tree_id": primary.get("style"),
        "primary_runes": _selection_ids(primary),
        "secondary_tree_id": secondary.get("style"),
        "secondary_runes": _selection_ids(secondary),
    }


def _build_matchup_index_rows(match: dict[str, Any]) -> list[dict[str, Any]]:
    info = match.get("info") or {}
    metadata = match.get("metadata") or {}
    participants = info.get("participants") or []
    match_id = metadata.get("matchId")
    if not match_id:
        return []

    rows: list[dict[str, Any]] = []
    for player in participants:
        lane = player.get("teamPosition")
        if not lane:
            continue

        enemy = next(
            (
                p
                for p in participants
                if p.get("teamId") != player.get("teamId")
                and p.get("teamPosition") == lane
            ),
            None,
        )
        if enemy is None:
            continue

        player_game_name = player.get("riotIdGameName")
        player_tag_line = player.get("riotIdTagline")
        enemy_game_name = enemy.get("riotIdGameName")
        enemy_tag_line = enemy.get("riotIdTagline")
        player_riot_id = format_riot_id(player_game_name, player_tag_line)
        enemy_riot_id = format_riot_id(enemy_game_name, enemy_tag_line)
        runes = _rune_fields(player)

        rows.append(
            {
                "match_id": match_id,
                "queue_id": info.get("queueId"),
                "game_creation": info.get("gameCreation"),
                "game_version": info.get("gameVersion"),
                "lane": lane,
                "player_puuid": player.get("puuid"),
                "player_riot_id": player_riot_id
                or player.get("summonerName")
                or "Unknown",
                "player_game_name": player_game_name,
                "player_tag_line": player_tag_line,
                "player_champion_key": player.get("championName"),
                "enemy_puuid": enemy.get("puuid"),
                "enemy_riot_id": enemy_riot_id
                or enemy.get("summonerName")
                or "Unknown",
                "enemy_game_name": enemy_game_name,
                "enemy_tag_line": enemy_tag_line,
                "enemy_champion_key": enemy.get("championName"),
                "win": bool(player.get("win")),
                "kills": int(player.get("kills") or 0),
                "deaths": int(player.get("deaths") or 0),
                "assists": int(player.get("assists") or 0),
                "cs": int(
                    (player.get("totalMinionsKilled") or 0)
                    + (player.get("neutralMinionsKilled") or 0)
                ),
                "gold_earned": int(player.get("goldEarned") or 0),
                "damage_to_champions": int(
                    player.get("totalDamageDealtToChampions") or 0
                ),
                "game_duration": int(info.get("gameDuration") or 0),
                "player_champion_level": int(player.get("champLevel") or 0),
                "player_summoner1_id": player.get("summoner1Id"),
                "player_summoner2_id": player.get("summoner2Id"),
                "player_items": [int(player.get(f"item{i}") or 0) for i in range(7)],
                "player_primary_tree_id": runes["primary_tree_id"],
                "player_primary_runes": runes["primary_runes"],
                "player_secondary_tree_id": runes["secondary_tree_id"],
                "player_secondary_runes": runes["secondary_runes"],
            }
        )

    return [
        row
        for row in rows
        if row["player_puuid"]
        and row["player_champion_key"]
        and row["enemy_puuid"]
        and row["enemy_champion_key"]
    ]


def _upsert_player_registry(
    conn: sqlite3.Connection,
    participant: dict[str, Any],
    now: int,
) -> None:
    puuid = participant.get("puuid")
    if not puuid:
        return
    conn.execute(
        """
        INSERT INTO player_registry (
            puuid, riot_id_game_name, riot_id_tag_line,
            summoner_name, first_seen_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(puuid) DO UPDATE SET
            riot_id_game_name = COALESCE(excluded.riot_id_game_name, riot_id_game_name),
            riot_id_tag_line = COALESCE(excluded.riot_id_tag_line, riot_id_tag_line),
            summoner_name = COALESCE(excluded.summoner_name, summoner_name),
            last_seen_at = excluded.last_seen_at
        """,
        (
            puuid,
            participant.get("riotIdGameName"),
            participant.get("riotIdTagline"),
            participant.get("summonerName"),
            now,
            now,
        ),
    )


def _rebuild_player_champion_stats(
    conn: sqlite3.Connection,
    puuids: set[str],
    now: int,
) -> None:
    if not puuids:
        return

    placeholders = ",".join("?" for _ in puuids)
    conn.execute(
        f"""
        DELETE FROM player_champion_stats
        WHERE puuid IN ({placeholders})
        """,
        tuple(puuids),
    )
    conn.execute(
        f"""
        INSERT INTO player_champion_stats (
            puuid, champion_key, games, wins, last_played_at, updated_at
        )
        SELECT
            player_puuid,
            player_champion_key,
            COUNT(*),
            SUM(win),
            MAX(game_creation),
            ?
        FROM matchup_index
        WHERE player_puuid IN ({placeholders})
        GROUP BY player_puuid, player_champion_key
        """,
        (now, *tuple(puuids)),
    )


def _loads_json_list(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [int(item) for item in value if item is not None]


def _indexed_row_to_result(row: dict[str, Any]) -> dict[str, Any]:
    game_creation = row.get("game_creation")
    return {
        "match_id": row.get("match_id"),
        "game_creation": game_creation,
        "game_date": unix_to_kst_date_str(game_creation) if game_creation else "",
        "queue_id": row.get("queue_id"),
        "my_champion_key": row.get("player_champion_key"),
        "enemy_champion_key": row.get("enemy_champion_key"),
        "enemy_riot_id": row.get("enemy_riot_id"),
        "enemy_game_name": row.get("enemy_game_name"),
        "enemy_tag_line": row.get("enemy_tag_line"),
        "player_riot_id": row.get("player_riot_id"),
        "player_game_name": row.get("player_game_name"),
        "player_tag_line": row.get("player_tag_line"),
        "player_puuid": row.get("player_puuid"),
        "player_rank": row.get("player_rank"),
        "player_league_points": row.get("player_league_points"),
        "win": bool(row.get("win")),
        "kills": int(row.get("kills") or 0),
        "deaths": int(row.get("deaths") or 0),
        "assists": int(row.get("assists") or 0),
        "cs": int(row.get("cs") or 0),
        "gold_earned": int(row.get("gold_earned") or 0),
        "damage_to_champions": int(row.get("damage_to_champions") or 0),
        "game_duration": int(row.get("game_duration") or 0),
        "game_version": row.get("game_version"),
        "my_champion_level": int(row.get("player_champion_level") or 0),
        "my_summoner1_id": row.get("player_summoner1_id"),
        "my_summoner2_id": row.get("player_summoner2_id"),
        "my_items": _loads_json_list(row.get("player_items_json")),
        "my_primary_tree_id": row.get("player_primary_tree_id"),
        "my_primary_runes": _loads_json_list(row.get("player_primary_runes_json")),
        "my_secondary_tree_id": row.get("player_secondary_tree_id"),
        "my_secondary_runes": _loads_json_list(row.get("player_secondary_runes_json")),
    }


def normalize_riot_id_key(game_name: str, tag_line: str) -> str:
    """캐시 키로 쓸 정규화된 Riot ID 문자열을 만든다."""
    return f"{game_name.strip().lower()}#{tag_line.strip().lower()}"
