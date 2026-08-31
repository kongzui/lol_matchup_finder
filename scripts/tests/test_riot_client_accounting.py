"""Riot HTTP 요청 수 집계와 재시도를 외부 연결 없이 검증한다."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from src.riot_client import RiotClient


class RiotClientAccountingTests(unittest.TestCase):
    """화면의 API 횟수는 실제 HTTP 시도 수를 사용해야 한다."""

    def test_retry_is_counted_as_an_additional_http_request(self) -> None:
        sleep = Mock()
        http_client = Mock()
        http_client.get.side_effect = [
            SimpleNamespace(
                status_code=429,
                headers={"Retry-After": "1"},
                text="limited",
            ),
            SimpleNamespace(
                status_code=200,
                headers={},
                text="ok",
                json=lambda: {"puuid": "player"},
            ),
        ]
        client = RiotClient("test-key", sleep_func=sleep)
        client._client = http_client

        result = client.get_account_by_riot_id("Player", "KR1")

        self.assertEqual(result, {"puuid": "player"})
        self.assertEqual(client.request_count, 2)
        self.assertEqual(http_client.get.call_count, 2)
        sleep.assert_called_once_with(1.0)

    def test_retry_after_is_capped_to_avoid_an_unbounded_global_wait(self) -> None:
        self.assertEqual(RiotClient._parse_retry_after("9999"), 120.0)


if __name__ == "__main__":
    unittest.main()
