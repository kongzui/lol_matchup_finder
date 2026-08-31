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
        client.get_summoner_by_puuid.assert_not_called()
        client.get_league_entries_by_summoner_id.assert_not_called()
        cache.save_ranked_profile.assert_not_called()


if __name__ == "__main__":
    unittest.main()
