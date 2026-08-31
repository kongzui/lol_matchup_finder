"""백그라운드 작업 목록, 중복 방지, F5 복구 정보를 검증한다."""

import threading
import unittest
from unittest.mock import patch

import src.api.main as api_main

from src.api.main import JobRecord, JobStore, _job_status


class JobStoreTests(unittest.TestCase):
    """같은 서버에서 작업을 다시 찾기 위한 저장소 계약을 검증한다."""

    def test_each_store_has_its_own_server_instance(self) -> None:
        first = JobStore()
        second = JobStore()

        self.assertNotEqual(first.server_instance_id, second.server_instance_id)
        self.assertGreater(first.server_started_at, 0)

    def test_same_active_dedupe_key_reuses_job(self) -> None:
        store = JobStore()
        started = threading.Event()
        release = threading.Event()

        def runner(job: JobRecord) -> None:
            started.set()
            release.wait(timeout=2)
            store.update(job.job_id, status="succeeded", result={"ok": True})

        first = store.create("multi-search", runner, dedupe_key="same-scope")
        self.assertTrue(started.wait(timeout=1))
        second = store.create("multi-search", runner, dedupe_key="same-scope")
        release.set()

        self.assertEqual(first.job_id, second.job_id)

    def test_active_and_recent_jobs_are_returned_separately(self) -> None:
        store = JobStore()
        release = threading.Event()
        active_started = threading.Event()
        completed = threading.Event()

        def active_runner(job: JobRecord) -> None:
            active_started.set()
            release.wait(timeout=2)
            store.update(job.job_id, status="succeeded", result={"ok": True})

        def completed_runner(job: JobRecord) -> None:
            store.update(job.job_id, status="partial", result={"saved": 1})
            completed.set()

        active_job = store.create(
            "search",
            active_runner,
            origin="individual-search",
            title="한 명 빠른 검색",
        )
        self.assertTrue(active_started.wait(timeout=1))
        completed_job = store.create("multi-search", completed_runner)
        self.assertTrue(completed.wait(timeout=1))

        active, recent = store.list_jobs(recent_limit=1)
        release.set()

        self.assertEqual([job.job_id for job in active], [active_job.job_id])
        self.assertEqual([job.job_id for job in recent], [completed_job.job_id])
        self.assertEqual(recent[0].status, "partial")

    def test_job_status_exposes_recovery_metadata(self) -> None:
        job = JobRecord(
            job_id="job",
            kind="multi-search",
            status="partial",
            origin="manual-multi",
            title="여러 명 경기 수집",
            target_summary="직접 입력 2명",
            result={"saved": 1},
            started_at=10,
            finished_at=20,
        )

        payload = _job_status(job)

        self.assertEqual(payload["origin"], "manual-multi")
        self.assertEqual(payload["targetSummary"], "직접 입력 2명")
        self.assertTrue(payload["resultAvailable"])
        self.assertEqual(payload["finishedAt"], 20)

    def test_list_endpoint_includes_server_identity(self) -> None:
        store = JobStore()
        with patch.object(api_main, "_jobs", store):
            payload = api_main.list_jobs(recent_limit=20)

        self.assertEqual(payload["serverInstanceId"], store.server_instance_id)
        self.assertEqual(payload["activeJobs"], [])
        self.assertEqual(payload["recentJobs"], [])


if __name__ == "__main__":
    unittest.main()
