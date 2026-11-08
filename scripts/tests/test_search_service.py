"""개별 검색 서비스의 캐시 재사용 규칙을 검증한다."""

import unittest
from unittest.mock import Mock

from src.search_service import fetch_ranked_profile


class RankedProfileCacheTests(unittest.TestCase):
    """랭크가 없는 정상 응답도 유효한 캐시로 사용하는지 검증한다."""

    def test_unranked_profile_is_reused_without_riot_api_call(self) -> None:
        cached_profile = {
            "tier": None,
            "rank": None,
            "league_points": None,
            "wins": None,
            "losses": None,
            "fetched_at": 123,
        }
        cache = Mock()
        cache.get_ranked_profile.return_value = cached_profile
        client = Mock()

        profile, cache_hits, api_calls = fetch_ranked_profile(
            client=client,
            cache=cache,
            puuid="unranked-player",
            queue_id=420,
        )

        self.assertEqual(profile, cached_profile)
        self.assertEqual(cache_hits, 1)
        self.assertEqual(api_calls, 0)
        client.get_league_entries_by_puuid.assert_not_called()
        cache.save_ranked_profile.assert_not_called()

    def test_unranked_player_is_settled_with_one_riot_api_call(self) -> None:
        saved_profile = {"tier": None, "fetched_at": 123}
        cache = Mock()
        cache.get_ranked_profile.side_effect = [None, None, saved_profile]
        client = Mock()
        client.get_league_entries_by_puuid.return_value = []

        profile, cache_hits, api_calls = fetch_ranked_profile(
            client=client,
            cache=cache,
            puuid="unranked-player",
            queue_id=420,
        )

        self.assertEqual(profile, saved_profile)
        self.assertEqual(cache_hits, 0)
        self.assertEqual(api_calls, 1)
        client.get_league_entries_by_puuid.assert_called_once_with("unranked-player")
        self.assertEqual(len(client.method_calls), 1)
        cache.save_ranked_profile.assert_called_once()


if __name__ == "__main__":
    unittest.main()
