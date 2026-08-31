import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "./api";
import type { JobListPayload, JobStatus } from "./types";

const JOB_RECOVERY_KEY = "lol-matchup-finder:job-recovery:v1";

type StoredActiveJob = Pick<
  JobStatus,
  "jobId" | "kind" | "title" | "targetSummary"
>;

type StoredJobRecovery = {
  serverInstanceId: string;
  activeJobs: StoredActiveJob[];
};

export type InterruptedJob = StoredActiveJob & {
  status: "interrupted";
};

export type JobConnectionState = "loading" | "connected" | "reconnecting";

let initialJobListRequest: ReturnType<typeof api.listJobs> | null = null;

function loadInitialJobList() {
  if (!initialJobListRequest) {
    initialJobListRequest = api.listJobs();
  }
  return initialJobListRequest;
}

function readStoredRecovery(): StoredJobRecovery | null {
  try {
    const raw = window.localStorage.getItem(JOB_RECOVERY_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<StoredJobRecovery>;
    if (
      typeof parsed.serverInstanceId !== "string" ||
      !Array.isArray(parsed.activeJobs)
    ) {
      return null;
    }
    return parsed as StoredJobRecovery;
  } catch {
    return null;
  }
}

function saveRecovery(payload: JobListPayload): void {
  const value: StoredJobRecovery = {
    serverInstanceId: payload.serverInstanceId,
    activeJobs: payload.activeJobs.map((job) => ({
      jobId: job.jobId,
      kind: job.kind,
      title: job.title,
      targetSummary: job.targetSummary,
    })),
  };
  try {
    window.localStorage.setItem(JOB_RECOVERY_KEY, JSON.stringify(value));
  } catch {
    return;
  }
}

export function useJobCenter() {
  const [payload, setPayload] = useState<JobListPayload | null>(null);
  const [connectionState, setConnectionState] =
    useState<JobConnectionState>("loading");
  const [connectionMessage, setConnectionMessage] = useState<string | null>(
    null,
  );
  const [interruptedJobs, setInterruptedJobs] = useState<InterruptedJob[]>([]);
  const timerRef = useRef<number | null>(null);
  const retryCountRef = useRef(0);
  const inFlightRef = useRef<Promise<void> | null>(null);
  const mountedRef = useRef(false);

  const applyPayload = useCallback((nextPayload: JobListPayload) => {
    const previous = readStoredRecovery();
    if (
      previous &&
      previous.serverInstanceId !== nextPayload.serverInstanceId &&
      previous.activeJobs.length > 0
    ) {
      setInterruptedJobs((current) => {
        const knownIds = new Set(current.map((job) => job.jobId));
        return [
          ...current,
          ...previous.activeJobs
            .filter((job) => !knownIds.has(job.jobId))
            .map((job) => ({ ...job, status: "interrupted" as const })),
        ];
      });
    }
    saveRecovery(nextPayload);
    setPayload(nextPayload);
    setConnectionState("connected");
    setConnectionMessage(null);
    retryCountRef.current = 0;
  }, []);

  const runPoll = useCallback(
    async (initial = false): Promise<void> => {
      if (inFlightRef.current) {
        return inFlightRef.current;
      }
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }

      const request = (async () => {
        try {
          const nextPayload = await (initial
            ? loadInitialJobList()
            : api.listJobs());
          if (!mountedRef.current) {
            return;
          }
          applyPayload(nextPayload);
          timerRef.current = window.setTimeout(
            () => void runPoll(),
            nextPayload.activeJobs.length > 0 ? 900 : 5_000,
          );
        } catch (exc) {
          if (!mountedRef.current) {
            return;
          }
          retryCountRef.current += 1;
          const retryDelay = Math.min(
            1_000 * 2 ** (retryCountRef.current - 1),
            10_000,
          );
          const reason =
            exc instanceof Error ? exc.message : "작업 목록 조회 실패";
          setConnectionState("reconnecting");
          setConnectionMessage(
            exc instanceof ApiError && exc.status === 404
              ? "서버 연결을 다시 확인하고 있어요."
              : `서버 작업은 계속될 수 있어요. ${Math.ceil(
                  retryDelay / 1_000,
                )}초 뒤 다시 연결해요. (${reason})`,
          );
          timerRef.current = window.setTimeout(
            () => void runPoll(),
            retryDelay,
          );
        }
      })();
      inFlightRef.current = request;
      try {
        await request;
      } finally {
        if (inFlightRef.current === request) {
          inFlightRef.current = null;
        }
      }
    },
    [applyPayload],
  );

  useEffect(() => {
    mountedRef.current = true;
    void runPoll(true);
    return () => {
      mountedRef.current = false;
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
    };
  }, [runPoll]);

  const jobs = useMemo(
    () => [...(payload?.activeJobs || []), ...(payload?.recentJobs || [])],
    [payload],
  );
  const refresh = useCallback(() => runPoll(false), [runPoll]);

  return {
    activeJobs: payload?.activeJobs || [],
    recentJobs: payload?.recentJobs || [],
    jobs,
    interruptedJobs,
    connectionState,
    connectionMessage,
    serverInstanceId: payload?.serverInstanceId || null,
    refresh,
  };
}
