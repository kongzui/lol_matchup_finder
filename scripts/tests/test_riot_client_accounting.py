"""Riot HTTP 요청 수 집계와 재시도를 외부 연결 없이 검증한다."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from src.riot_client import (
    MAX_RATE_LIMIT_WAIT_SECONDS,
    MAX_RETRY_SERVER_ERROR,
    RiotApiError,
    RiotApiRateLimited,
    RiotClient,
)


def _limited(retry_after: str) -> SimpleNamespace:
    """호출 제한 응답 대역을 만든다."""
    return SimpleNamespace(
        status_code=429,
        headers={"Retry-After": retry_after},
        text="limited",
    )


def _success() -> SimpleNamespace:
    """정상 계정 응답 대역을 만든다."""
    return SimpleNamespace(
        status_code=200,
        headers={},
        text="ok",
        json=lambda: {"puuid": "player"},
    )


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

    def test_repeated_rate_limits_wait_and_continue_the_same_request(self) -> None:
        sleep = Mock()
        waits: list[float] = []
        http_client = Mock()
        http_client.get.side_effect = [
            _limited("120"),
            _limited("120"),
            _limited("120"),
            _limited("120"),
            _success(),
        ]
        client = RiotClient(
            "test-key",
            sleep_func=sleep,
            rate_limit_wait_cb=waits.append,
        )
        client._client = http_client

        result = client.get_account_by_riot_id("Player", "KR1")

        self.assertEqual(result, {"puuid": "player"})
        self.assertEqual(sleep.call_count, 4)
        self.assertEqual(waits, [120.0, 120.0, 120.0, 120.0])

    def test_wait_longer_than_the_limit_stops_without_sleeping(self) -> None:
        sleep = Mock()
        http_client = Mock()
        http_client.get.side_effect = [_limited(str(MAX_RATE_LIMIT_WAIT_SECONDS + 1))]
        client = RiotClient("test-key", sleep_func=sleep)
        client._client = http_client

        with self.assertRaises(RiotApiRateLimited):
            client.get_account_by_riot_id("Player", "KR1")

        sleep.assert_not_called()

    def test_server_errors_still_stop_after_the_retry_limit(self) -> None:
        sleep = Mock()
        http_client = Mock()
        http_client.get.return_value = SimpleNamespace(
            status_code=503,
            headers={},
            text="unavailable",
        )
        client = RiotClient("test-key", sleep_func=sleep)
        client._client = http_client

        with self.assertRaises(RiotApiError):
            client.get_account_by_riot_id("Player", "KR1")

        self.assertEqual(http_client.get.call_count, MAX_RETRY_SERVER_ERROR + 1)


if __name__ == "__main__":
    unittest.main()
