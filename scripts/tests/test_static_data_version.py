"""게임 정보 저장소가 이미 확인한 버전을 다시 묻지 않는지 검증한다."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import src.api.main as api_main
from src.static_data import StaticDataRepository


class StaticDataVersionTests(unittest.TestCase):
    """챔피언과 룬·주문 저장소는 버전 확인 결과를 공유해야 한다."""

    def test_supplied_version_skips_another_latest_version_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = StaticDataRepository(str(Path(temp_dir) / "cache.db"))
            payloads = [
                '{"data":{"Flash":{"key":"4","id":"SummonerFlash","name":"점멸"}}}',
                "[]",
            ]
            with (
                patch.object(
                    repository,
                    "_fetch_latest_version",
                    side_effect=AssertionError("버전 주소를 다시 호출하면 안 돼요."),
                ) as fetch_version,
                patch.object(repository, "_fetch_text", side_effect=payloads),
            ):
                data = repository.load(
                    check_latest=True,
                    latest_version="99.1.1",
                )

        self.assertEqual(data.version, "99.1.1")
        fetch_version.assert_not_called()

    def test_concurrent_startup_checks_share_one_resource_refresh(self) -> None:
        refresh_started = threading.Event()
        release_refresh = threading.Event()

        def refresh_resources(**_kwargs: object) -> None:
            refresh_started.set()
            release_refresh.wait(timeout=2)
            api_main._data_dragon_checked = True

        with (
            patch.object(api_main, "_data_dragon_checked", False),
            patch.object(
                api_main,
                "_refresh_data_dragon_resources",
                side_effect=refresh_resources,
            ) as refresh,
        ):
            first = threading.Thread(target=api_main._ensure_data_dragon_checked)
            second = threading.Thread(target=api_main._ensure_data_dragon_checked)
            first.start()
            self.assertTrue(refresh_started.wait(timeout=2))
            second.start()
            release_refresh.set()
            first.join(timeout=2)
            second.join(timeout=2)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        refresh.assert_called_once_with(force_refresh=False, check_latest=True)


if __name__ == "__main__":
    unittest.main()
