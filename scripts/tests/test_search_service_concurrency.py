"""서로 다른 작업이 같은 매치 상세를 중복 호출하지 않는지 검증한다."""

from __future__ import annotations

import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from src.search_service import (
    ensure_match_indexed,
    fetch_match_detail,
    fetch_match_timeline,
    fetch_ranked_profile,
)
from src.riot_client import RiotApiNotFound


class _ThreadSafeCache:
    """동시 호출 테스트에 필요한 최소 메모리 캐시다."""

    def __init__(self) -> None:
        self._matches: dict[str, dict[str, str]] = {}
        self._lock = threading.Lock()

    def get_match(self, match_id: str) -> dict[str, str] | None:
        with self._lock:
            return self._matches.get(match_id)

    def save_match(self, match_id: str, match: dict[str, str]) -> None:
        with self._lock:
            self._matches[match_id] = match


class _SlowClient:
    """두 작업이 겹칠 시간을 주는 Riot 클라이언트 대역이다."""

    def __init__(self) -> None:
        self.call_count = 0
        self._lock = threading.Lock()

    def get_match_by_id(self, match_id: str) -> dict[str, str]:
        with self._lock:
            self.call_count += 1
        time.sleep(0.03)
        return {"match_id": match_id}


class MatchDetailSingleFlightTests(unittest.TestCase):
    """같은 Match ID의 저장 중 호출은 한 번만 외부로 나가야 한다."""

    def test_concurrent_fetches_share_the_saved_match(self) -> None:
        cache = _ThreadSafeCache()
        client = _SlowClient()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda _index: fetch_match_detail(
                        client,  # type: ignore[arg-type]
                        cache,  # type: ignore[arg-type]
                        "KR_SHARED",
                    ),
                    range(2),
                )
            )

        self.assertEqual(client.call_count, 1)
        self.assertEqual(results, [{"match_id": "KR_SHARED"}] * 2)

    def test_concurrent_missing_match_checks_share_the_404_result(self) -> None:
        cache = _ThreadSafeCache()

        class MissingClient(_SlowClient):
            def get_match_by_id(self, _match_id: str) -> dict[str, str]:
                with self._lock:
                    self.call_count += 1
                time.sleep(0.03)
                raise RiotApiNotFound("없는 경기")

        client = MissingClient()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda _index: fetch_match_detail(
                        client,  # type: ignore[arg-type]
                        cache,  # type: ignore[arg-type]
                        "KR_SHARED_MISSING",
                    ),
                    range(2),
                )
            )

        self.assertEqual(client.call_count, 1)
        self.assertEqual(results, [None, None])

    def test_concurrent_timeline_fetches_share_the_saved_timeline(self) -> None:
        class TimelineCache:
            def __init__(self) -> None:
                self.value: dict[str, str] | None = None
                self.lock = threading.Lock()

            def get_match_timeline(self, _match_id: str) -> dict[str, str] | None:
                with self.lock:
                    return self.value

            def save_match_timeline(
                self, _match_id: str, timeline: dict[str, str]
            ) -> None:
                with self.lock:
                    self.value = timeline

        class TimelineClient:
            def __init__(self) -> None:
                self.call_count = 0
                self.lock = threading.Lock()

            def get_match_timeline_by_id(self, match_id: str) -> dict[str, str]:
                with self.lock:
                    self.call_count += 1
                time.sleep(0.03)
                return {"match_id": match_id}

        cache = TimelineCache()
        client = TimelineClient()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda _index: fetch_match_timeline(
                        client,  # type: ignore[arg-type]
                        cache,  # type: ignore[arg-type]
                        "KR_TIMELINE",
                    ),
                    range(2),
                )
            )

        self.assertEqual(client.call_count, 1)
        self.assertEqual(results, [{"match_id": "KR_TIMELINE"}] * 2)

    def test_concurrent_missing_timeline_checks_share_the_404_result(self) -> None:
        class TimelineCache:
            def get_match_timeline(self, _match_id: str) -> None:
                return None

        class MissingTimelineClient:
            def __init__(self) -> None:
                self.call_count = 0
                self.lock = threading.Lock()

            def get_match_timeline_by_id(self, _match_id: str) -> None:
                with self.lock:
                    self.call_count += 1
                time.sleep(0.03)
                raise RiotApiNotFound("없는 타임라인")

        cache = TimelineCache()
        client = MissingTimelineClient()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda _index: fetch_match_timeline(
                        client,  # type: ignore[arg-type]
                        cache,  # type: ignore[arg-type]
                        "KR_TIMELINE_MISSING",
                    ),
                    range(2),
                )
            )

        self.assertEqual(client.call_count, 1)
        self.assertEqual(results, [None, None])

    def test_concurrent_rank_fetches_share_the_saved_profile(self) -> None:
        class RankCache:
            def __init__(self) -> None:
                self.value: dict[str, object] | None = None
                self.lock = threading.Lock()

            def get_ranked_profile(
                self, _puuid: str, _queue_id: int
            ) -> dict[str, object] | None:
                with self.lock:
                    return self.value

            def save_ranked_profile(self, **values: object) -> None:
                with self.lock:
                    self.value = dict(values)

        class RankClient:
            def __init__(self) -> None:
                self.call_count = 0
                self.lock = threading.Lock()

            def get_league_entries_by_puuid(
                self, _puuid: str
            ) -> list[dict[str, object]]:
                with self.lock:
                    self.call_count += 1
                time.sleep(0.03)
                return [
                    {
                        "queueType": "RANKED_SOLO_5x5",
                        "tier": "GOLD",
                        "rank": "I",
                    }
                ]

        cache = RankCache()
        client = RankClient()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda _index: fetch_ranked_profile(
                        client,  # type: ignore[arg-type]
                        cache,  # type: ignore[arg-type]
                        "shared-puuid",
                        420,
                    ),
                    range(2),
                )
            )

        self.assertEqual(client.call_count, 1)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result[0] is not None for result in results))

    def test_concurrent_indexing_builds_the_match_once(self) -> None:
        class IndexCache:
            def __init__(self) -> None:
                self.indexed = False
                self.call_count = 0
                self.lock = threading.Lock()

            def get_indexed_match_ids(self, _match_ids: list[str]) -> set[str]:
                with self.lock:
                    return {"KR_INDEX"} if self.indexed else set()

            def index_match(self, _match: object, **_kwargs: object) -> int:
                with self.lock:
                    self.call_count += 1
                time.sleep(0.03)
                with self.lock:
                    self.indexed = True
                return 10

        cache = IndexCache()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda _index: ensure_match_indexed(
                        cache,  # type: ignore[arg-type]
                        {"metadata": {"matchId": "KR_INDEX"}},
                        match_id="KR_INDEX",
                        queue_id=420,
                    ),
                    range(2),
                )
            )

        self.assertEqual(cache.call_count, 1)
        self.assertEqual(sorted(results), [(0, True), (10, True)])


if __name__ == "__main__":
    unittest.main()
