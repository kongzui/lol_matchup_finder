import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Copy,
  Database,
  Download,
  ExternalLink,
  History,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Search,
  StickyNote,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import type { Dispatch, ReactNode, SetStateAction } from "react";
import { api, ApiError } from "./api";
import {
  useJobCenter,
  type InterruptedJob,
  type JobConnectionState,
} from "./useJobCenter";
import type {
  DbSearchBody,
  DbSearchPayload,
  DetailPlayer,
  JobStatus,
  MatchDetail,
  MatchRow,
  MemoTarget,
  MetaOptions,
  MultiSearchBody,
  MultiSearchPayload,
  PlayerMemo,
  RuneEntry,
  RunePage,
  SearchBody,
  SearchPayload,
  TeamSummary,
} from "./types";

type Workspace = "search" | "multi" | "db" | "memo" | "history";
type DetailTab = "summary" | "build";

type PlayerMemoState = {
  items: PlayerMemo[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
};

let initialPlayerMemosRequest: ReturnType<typeof api.listPlayerMemos> | null =
  null;
let initialMetaRequest: ReturnType<typeof api.startup> | null = null;

function loadInitialMeta() {
  if (!initialMetaRequest) {
    initialMetaRequest = api.startup();
  }
  return initialMetaRequest;
}

function loadInitialPlayerMemos() {
  if (!initialPlayerMemosRequest) {
    initialPlayerMemosRequest = api.listPlayerMemos();
  }
  return initialPlayerMemosRequest;
}

const PAGE_SIZE = 50;
const RIOT_ID_HISTORY_KEY = "lol-matchup-finder:riot-id-history";
const LEGACY_MULTI_SEARCH_HISTORY_KEY =
  "lol-matchup-finder:multi-search-history";
const RIOT_ID_HISTORY_LIMIT = 20;
const ENEMY_CHAMPION_HISTORY_KEY = "lol-matchup-finder:enemy-champion-history";
const ENEMY_CHAMPION_HISTORY_LIMIT = 20;
const MY_CHAMPION_HISTORY_KEY = "lol-matchup-finder:my-champion-history";
const MY_CHAMPION_HISTORY_LIMIT = 6;
const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;
const LANE_ORDER: Record<string, number> = {
  TOP: 0,
  JUNGLE: 1,
  MIDDLE: 2,
  BOTTOM: 3,
  UTILITY: 4,
};

function todayText(): string {
  return localDateText(new Date());
}

function daysAgoText(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return localDateText(date);
}

function localDateText(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function matchDate(row: MatchRow): Date | null {
  if (row.game_creation) {
    return new Date(row.game_creation);
  }
  if (row.game_date) {
    const parsed = new Date(`${row.game_date}T00:00:00`);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }
  return null;
}

function startOfLocalDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function relativeMatchDate(row: MatchRow): string {
  const date = matchDate(row);
  if (!date) {
    return "-";
  }

  const elapsedMs = new Date().getTime() - date.getTime();
  if (elapsedMs < HOUR_MS) {
    return "1시간 미만";
  }
  if (elapsedMs < DAY_MS) {
    return `${Math.floor(elapsedMs / HOUR_MS)}시간 전`;
  }

  const today = startOfLocalDay(new Date());
  const target = startOfLocalDay(date);
  const daysAgo = Math.max(
    Math.floor((today.getTime() - target.getTime()) / DAY_MS),
    0,
  );

  if (daysAgo === 0) {
    return "오늘";
  }
  if (daysAgo < 7) {
    return `${daysAgo}일 전`;
  }
  if (daysAgo < 30) {
    return `${Math.floor(daysAgo / 7)}주일 전`;
  }
  return `${Math.floor(daysAgo / 30)}개월 전`;
}

function exactMatchDate(row: MatchRow): string {
  const date = matchDate(row);
  if (!date) {
    return "";
  }
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  }).format(date);
}

function dbLastMatchText(timestamp?: number | null): string {
  if (!timestamp) {
    return "없음";
  }
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  }).format(new Date(timestamp));
}

function playerMatchCollectionSummary(meta: MetaOptions): string {
  const refresh = meta.autoRefresh;
  if (refresh.status === "running" && refresh.plannedTargetCount) {
    return `${refresh.plannedTargetCount}명 매치 수집 중`;
  }
  return refresh.message;
}

function playerMatchCollectionScope(meta: MetaOptions): string {
  const lastCollected = meta.autoRefresh.lastCollectedAt
    ? memoDate(meta.autoRefresh.lastCollectedAt)
    : "기록 없음";
  const planned = meta.autoRefresh.plannedTargetCount
    ? ` · 이번 대상 ${meta.autoRefresh.plannedTargetCount}명`
    : "";
  return `설정 ${meta.autoRefresh.targetCount}명${planned} · 가장 최근 완료 ${lastCollected}`;
}

function matchPatchVersion(row: MatchRow): string | null {
  const version = row.game_version?.trim();
  if (!version) {
    return null;
  }
  const parts = version.split(".");
  if (parts.length >= 2) {
    return `${parts[0]}.${parts[1]}`;
  }
  return version;
}

function championIcon(version: string, key?: string): string {
  return key
    ? `https://ddragon.leagueoflegends.com/cdn/${version}/img/champion/${key}.png`
    : "";
}

function itemIcon(version: string, itemId: number): string {
  return `https://ddragon.leagueoflegends.com/cdn/${version}/img/item/${itemId}.png`;
}

function opggUrl(gameName?: string, tagLine?: string): string {
  if (!gameName || !tagLine) {
    return "";
  }
  return `https://op.gg/ko/lol/summoners/kr/${encodeURIComponent(
    gameName,
  )}-${encodeURIComponent(tagLine)}`;
}

function splitRiotId(value?: string): { name: string; tag: string } {
  const [name = "", tag = ""] = (value || "").split("#", 2);
  return { name, tag };
}

function normalizeRiotIdInput(value: string): string {
  return splitRiotIdInput(value).join("\n");
}

function splitRiotIdInput(value: string): string[] {
  return value
    .split(/\r?\n|,/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function normalizeSingleRiotIdInput(value: string): string {
  return splitRiotIdInput(value)[0] || "";
}

function appendRiotIdInput(current: string, riotId: string): string {
  const currentIds = splitRiotIdInput(current);
  const nextId = normalizeRiotIdInput(riotId);
  const nextKey = nextId.toLocaleLowerCase();
  if (
    !nextId ||
    currentIds.some((item) => item.toLocaleLowerCase() === nextKey)
  ) {
    return currentIds.join("\n");
  }
  return `${[...currentIds, nextId].join("\n")}\n`;
}

function appendRiotIdInputs(
  current: string,
  riotIds: string[],
  separator: "line" | "comma" = "line",
): string {
  const currentIds = splitRiotIdInput(current);
  const seen = new Set(currentIds.map((item) => item.toLocaleLowerCase()));
  const nextIds = [...currentIds];
  for (const riotId of riotIds) {
    const normalized = normalizeRiotIdInput(riotId);
    const key = normalized.toLocaleLowerCase();
    if (!normalized || seen.has(key)) {
      continue;
    }
    seen.add(key);
    nextIds.push(normalized);
  }
  return nextIds.length
    ? `${nextIds.join(separator === "comma" ? ", " : "\n")}${
        separator === "line" ? "\n" : ""
      }`
    : "";
}

function compactRiotIdHistory(values: string[]): string[] {
  const seen = new Set<string>();
  const compacted: string[] = [];
  for (const value of values) {
    for (const riotId of splitRiotIdInput(value)) {
      const key = riotId.toLocaleLowerCase();
      if (!riotId || seen.has(key)) {
        continue;
      }
      seen.add(key);
      compacted.push(riotId);
      if (compacted.length >= RIOT_ID_HISTORY_LIMIT) {
        return compacted;
      }
    }
  }
  return compacted;
}

function readStoredRiotIdHistory(key: string): string[] {
  try {
    const raw = window.localStorage.getItem(key);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed)
      ? parsed.filter((item) => typeof item === "string")
      : [];
  } catch {
    return [];
  }
}

function loadRiotIdHistory(): string[] {
  return compactRiotIdHistory([
    ...readStoredRiotIdHistory(RIOT_ID_HISTORY_KEY),
    ...readStoredRiotIdHistory(LEGACY_MULTI_SEARCH_HISTORY_KEY),
  ]);
}

function saveRiotIdHistory(history: string[]): void {
  try {
    window.localStorage.setItem(RIOT_ID_HISTORY_KEY, JSON.stringify(history));
  } catch {
    return;
  }
}

function compactEnemyChampionHistory(
  values: string[],
  meta?: MetaOptions,
): string[] {
  const championNames = meta
    ? new Set(meta.champions.map((champion) => champion.koreanName))
    : null;
  const seen = new Set<string>();
  const compacted: string[] = [];
  for (const value of values) {
    const champion = value.trim();
    if (!champion || seen.has(champion)) {
      continue;
    }
    if (championNames && !championNames.has(champion)) {
      continue;
    }
    seen.add(champion);
    compacted.push(champion);
    if (compacted.length >= ENEMY_CHAMPION_HISTORY_LIMIT) {
      break;
    }
  }
  return compacted;
}

function loadEnemyChampionHistory(): string[] {
  try {
    const raw = window.localStorage.getItem(ENEMY_CHAMPION_HISTORY_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed)
      ? compactEnemyChampionHistory(
          parsed.filter((item) => typeof item === "string"),
        )
      : [];
  } catch {
    return [];
  }
}

function saveEnemyChampionHistory(history: string[]): void {
  try {
    window.localStorage.setItem(
      ENEMY_CHAMPION_HISTORY_KEY,
      JSON.stringify(history),
    );
  } catch {
    return;
  }
}

function compactMyChampionHistory(
  values: string[],
  meta?: MetaOptions,
): string[] {
  const championNames = meta
    ? new Set(meta.champions.map((champion) => champion.koreanName))
    : null;
  const seen = new Set<string>();
  const compacted: string[] = [];
  for (const value of values) {
    const champion = value.trim();
    if (!champion || seen.has(champion)) {
      continue;
    }
    if (championNames && !championNames.has(champion)) {
      continue;
    }
    seen.add(champion);
    compacted.push(champion);
    if (compacted.length >= MY_CHAMPION_HISTORY_LIMIT) {
      break;
    }
  }
  return compacted;
}

function loadMyChampionHistory(): string[] {
  try {
    const raw = window.localStorage.getItem(MY_CHAMPION_HISTORY_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed)
      ? compactMyChampionHistory(
          parsed.filter((item) => typeof item === "string"),
        )
      : [];
  } catch {
    return [];
  }
}

function saveMyChampionHistory(history: string[]): void {
  try {
    window.localStorage.setItem(
      MY_CHAMPION_HISTORY_KEY,
      JSON.stringify(history),
    );
  } catch {
    return;
  }
}

function removeStoredHistoryItem(
  item: string,
  setHistory: Dispatch<SetStateAction<string[]>>,
  saveHistory: (history: string[]) => void,
): void {
  setHistory((prev) => {
    const next = prev.filter((historyItem) => historyItem !== item);
    saveHistory(next);
    return next;
  });
}

function riotId(name?: string, tag?: string, fallback?: string): string {
  if (name && tag) {
    return `${name}#${tag}`;
  }
  return fallback || name || "-";
}

function withMemo(nickname: string, memo?: string | null): string {
  return memo ? `${nickname} (${memo})` : nickname;
}

function memoDate(unixSeconds?: number | null): string {
  if (!unixSeconds) {
    return "-";
  }
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
  }).format(new Date(unixSeconds * 1000));
}

function playerMemoName(item: PlayerMemo): string {
  return riotId(
    item.riot_id_game_name || undefined,
    item.riot_id_tag_line || undefined,
    item.summoner_name || item.puuid,
  );
}

function playerCollectionStatus(item: PlayerMemo): string {
  const completedAt = item.last_collected_at
    ? ` · 마지막 완료 ${memoDate(item.last_collected_at)}`
    : "";
  if (item.collection_status === "current") {
    return `경기 데이터 최신${completedAt}`;
  }
  if (item.collection_status === "continuation_needed") {
    return `다음 수집에서 이어받기${completedAt}`;
  }
  if (item.collection_status === "needs_collection") {
    return `경기 수집 필요${completedAt}`;
  }
  return "아직 경기 수집 안 함";
}

function groupPlayerMemos(items: PlayerMemo[]) {
  const groups = new Map<string, PlayerMemo[]>();
  for (const item of items) {
    const key = item.memo.trim() || "메모 없음";
    groups.set(key, [...(groups.get(key) || []), item]);
  }
  return [...groups.entries()]
    .map(([memoText, groupItems]) => ({
      memoText,
      items: groupItems.sort((left, right) =>
        playerMemoName(left).localeCompare(playerMemoName(right), "ko-KR", {
          sensitivity: "base",
        }),
      ),
      gameCount: groupItems.reduce(
        (total, item) => total + (item.game_count || 0),
        0,
      ),
    }))
    .sort((left, right) =>
      left.memoText.localeCompare(right.memoText, "ko-KR", {
        sensitivity: "base",
      }),
    );
}

function secondsToMinutes(seconds?: number): string {
  return `${Math.max(Math.floor((seconds || 0) / 60), 0)}분`;
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function dbSearchCriteriaKey(body: DbSearchBody): string {
  const { page: _page, snapshot: _snapshot, ...criteria } = body;
  return JSON.stringify(criteria);
}

function useJobCompletion(
  job: JobStatus | null,
  onDone: (status: JobStatus, isCurrent: () => boolean) => Promise<void>,
  onFailed?: (status: JobStatus, isCurrent: () => boolean) => Promise<void>,
) {
  const [error, setError] = useState<string | null>(null);
  const completionSequence = useRef(0);
  const onDoneRef = useRef(onDone);
  const onFailedRef = useRef(onFailed);
  onDoneRef.current = onDone;
  onFailedRef.current = onFailed;

  useEffect(() => {
    if (!job) {
      return;
    }
    if (job.status === "queued" || job.status === "running") {
      setError(null);
      return;
    }
    setError(null);
    const sequence = completionSequence.current + 1;
    completionSequence.current = sequence;
    let retryTimer: number | null = null;
    let retryCount = 0;
    const isCurrent = () => completionSequence.current === sequence;
    const complete = async () => {
      try {
        if (job.status === "failed" && !job.resultAvailable) {
          if (!isCurrent()) {
            return;
          }
          setError(job.error || "작업에 실패했습니다.");
          if (onFailedRef.current) {
            await onFailedRef.current(job, isCurrent);
          }
        } else {
          await onDoneRef.current(job, isCurrent);
          if (isCurrent()) {
            setError(null);
          }
        }
      } catch (exc) {
        if (isCurrent()) {
          if (exc instanceof ApiError && [404, 410].includes(exc.status)) {
            setError(
              "서버가 다시 시작되어 이 작업 결과는 더 이상 열 수 없어요. 이미 저장을 마친 경기는 내 컴퓨터에 남아 있어요.",
            );
            if (onFailedRef.current) {
              await onFailedRef.current(job, isCurrent);
            }
            return;
          }
          retryCount += 1;
          const retryDelay = Math.min(1_000 * 2 ** (retryCount - 1), 10_000);
          const reason =
            exc instanceof Error ? exc.message : "작업 결과 조회 실패";
          setError(
            `작업은 끝났지만 결과 화면 연결이 잠시 끊겼어요. ${Math.ceil(
              retryDelay / 1_000,
            )}초 뒤 다시 불러와요. (${reason})`,
          );
          retryTimer = window.setTimeout(complete, retryDelay);
        }
      }
    };
    void complete();
    return () => {
      if (completionSequence.current === sequence) {
        completionSequence.current += 1;
      }
      if (retryTimer !== null) {
        window.clearTimeout(retryTimer);
      }
    };
  }, [job?.error, job?.jobId, job?.resultAvailable, job?.status]);

  return { error, setError };
}

export default function App() {
  const jobCenter = useJobCenter();
  const [meta, setMeta] = useState<MetaOptions | null>(null);
  const [startupAttempt, setStartupAttempt] = useState(0);
  const [active, setActive] = useState<Workspace>("db");
  const [error, setError] = useState<string | null>(null);
  const [riotIdHistory, setRiotIdHistory] =
    useState<string[]>(loadRiotIdHistory);
  const [enemyChampionHistory, setEnemyChampionHistory] = useState<string[]>(
    loadEnemyChampionHistory,
  );
  const [myChampionHistory, setMyChampionHistory] = useState<string[]>(
    loadMyChampionHistory,
  );
  const [metaRefreshing, setMetaRefreshing] = useState(false);
  const [metaRefreshError, setMetaRefreshError] = useState<string | null>(null);
  const [sharedRefreshError, setSharedRefreshError] = useState<string | null>(
    null,
  );
  const [collectionStarting, setCollectionStarting] = useState(false);
  const [collectionStartError, setCollectionStartError] = useState<
    string | null
  >(null);
  const [memoItems, setMemoItems] = useState<PlayerMemo[]>([]);
  const [memosLoading, setMemosLoading] = useState(true);
  const [memosError, setMemosError] = useState<string | null>(null);
  const [recoveryRequest, setRecoveryRequest] = useState({
    jobId: null as string | null,
    sequence: 0,
  });
  const memoRequestSequence = useRef(0);
  const metaRequestSequence = useRef(0);
  const sharedRefreshPromise = useRef<Promise<void> | null>(null);
  const terminalJobsSeen = useRef<Set<string>>(new Set());
  const terminalJobsInitialized = useRef(false);
  const interruptedJobsSeen = useRef<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    const requestId = metaRequestSequence.current + 1;
    metaRequestSequence.current = requestId;
    loadInitialMeta()
      .then((payload) => {
        if (!cancelled && metaRequestSequence.current === requestId) {
          setMeta(payload);
        }
      })
      .catch((exc) => {
        if (!cancelled && metaRequestSequence.current === requestId) {
          setError(exc instanceof Error ? exc.message : "메타데이터 로딩 실패");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [startupAttempt]);

  useEffect(() => {
    let cancelled = false;
    const requestId = memoRequestSequence.current + 1;
    memoRequestSequence.current = requestId;
    setMemosLoading(true);
    setMemosError(null);
    loadInitialPlayerMemos()
      .then((payload) => {
        if (!cancelled && memoRequestSequence.current === requestId) {
          setMemoItems(payload.items);
        }
      })
      .catch((exc) => {
        if (!cancelled && memoRequestSequence.current === requestId) {
          setMemosError(
            exc instanceof Error ? exc.message : "메모 목록 조회 실패",
          );
        }
      })
      .finally(() => {
        if (!cancelled && memoRequestSequence.current === requestId) {
          setMemosLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [startupAttempt]);

  const refreshPlayerMemos = async () => {
    const requestId = memoRequestSequence.current + 1;
    memoRequestSequence.current = requestId;
    setMemosLoading(true);
    setMemosError(null);
    try {
      const payload = await api.listPlayerMemos();
      if (memoRequestSequence.current === requestId) {
        setMemoItems(payload.items);
      }
    } catch (exc) {
      if (memoRequestSequence.current === requestId) {
        setMemosError(
          exc instanceof Error ? exc.message : "메모 목록 조회 실패",
        );
      }
    } finally {
      if (memoRequestSequence.current === requestId) {
        setMemosLoading(false);
      }
    }
  };

  const refreshMetaAndMemos = (): Promise<void> => {
    if (sharedRefreshPromise.current) {
      return sharedRefreshPromise.current;
    }

    const requestId = metaRequestSequence.current + 1;
    metaRequestSequence.current = requestId;
    const request = (async () => {
      const [metaResult] = await Promise.allSettled([
        api.getMeta(),
        refreshPlayerMemos(),
      ]);
      if (metaRequestSequence.current !== requestId) {
        return;
      }
      if (metaResult.status === "fulfilled") {
        setMeta(metaResult.value);
        setSharedRefreshError(null);
      } else {
        setSharedRefreshError(
          metaResult.reason instanceof Error
            ? metaResult.reason.message
            : "공용 데이터 현황 갱신 실패",
        );
      }
    })();
    sharedRefreshPromise.current = request;
    void request.finally(() => {
      if (sharedRefreshPromise.current === request) {
        sharedRefreshPromise.current = null;
      }
    });
    return request;
  };

  const playerMemos: PlayerMemoState = {
    items: memoItems,
    loading: memosLoading,
    error: memosError,
    refresh: refreshPlayerMemos,
  };

  useEffect(() => {
    if (!meta || jobCenter.connectionState !== "connected") {
      return;
    }
    if (!terminalJobsInitialized.current) {
      terminalJobsInitialized.current = true;
      terminalJobsSeen.current = new Set(
        jobCenter.recentJobs.map((job) => job.jobId),
      );
      if (jobCenter.recentJobs.length > 0) {
        void refreshMetaAndMemos();
      }
      return;
    }

    const newlyFinished = jobCenter.recentJobs.filter(
      (job) => !terminalJobsSeen.current.has(job.jobId),
    );
    for (const job of newlyFinished) {
      terminalJobsSeen.current.add(job.jobId);
    }
    if (newlyFinished.length > 0) {
      void refreshMetaAndMemos();
    }
  }, [jobCenter.connectionState, jobCenter.recentJobs, meta]);

  useEffect(() => {
    if (!meta) {
      return;
    }
    const newlyInterrupted = jobCenter.interruptedJobs.filter(
      (job) => !interruptedJobsSeen.current.has(job.jobId),
    );
    for (const job of newlyInterrupted) {
      interruptedJobsSeen.current.add(job.jobId);
    }
    if (newlyInterrupted.length > 0) {
      void refreshMetaAndMemos();
    }
  }, [jobCenter.interruptedJobs, meta]);

  useEffect(() => {
    if (meta?.autoRefresh.status === "running") {
      void jobCenter.refresh();
    }
  }, [meta?.autoRefresh.jobId, meta?.autoRefresh.status]);

  useEffect(() => {
    if (!meta?.latestSearchRiotId) {
      return;
    }
    setRiotIdHistory((prev) => {
      if (prev.length > 0) {
        return prev;
      }
      const next = compactRiotIdHistory([meta.latestSearchRiotId || ""]);
      saveRiotIdHistory(next);
      return next;
    });
  }, [meta?.latestSearchRiotId]);

  useEffect(() => {
    if (!meta) {
      return;
    }
    setEnemyChampionHistory((prev) => {
      const next = compactEnemyChampionHistory(prev, meta);
      saveEnemyChampionHistory(next);
      return next;
    });
  }, [meta]);

  useEffect(() => {
    if (!meta) {
      return;
    }
    setMyChampionHistory((prev) => {
      const next = compactMyChampionHistory(prev, meta);
      saveMyChampionHistory(next);
      return next;
    });
  }, [meta]);

  const autoRefreshJob = jobCenter.jobs.find(
    (job) => job.jobId === meta?.autoRefresh.jobId,
  );
  const selectedRecoveryJob = jobCenter.jobs.find(
    (job) => job.jobId === recoveryRequest.jobId,
  );
  const searchRecoveryJob =
    selectedRecoveryJob?.kind === "search"
      ? selectedRecoveryJob
      : jobCenter.activeJobs.find((job) => job.kind === "search");
  const multiRecoveryJob =
    selectedRecoveryJob && isMultiCollectionJob(selectedRecoveryJob)
      ? selectedRecoveryJob
      : jobCenter.activeJobs.find(isMultiCollectionJob);
  const searchRecoveryRequestSequence =
    selectedRecoveryJob?.kind === "search" ? recoveryRequest.sequence : 0;
  const multiRecoveryRequestSequence =
    selectedRecoveryJob && isMultiCollectionJob(selectedRecoveryJob)
      ? recoveryRequest.sequence
      : 0;

  const startPlayerMatchCollection = async () => {
    if (!meta) {
      return;
    }

    const refresh = meta.autoRefresh;
    let confirmed = false;
    if (refresh.requiresConfirmation) {
      confirmed = window.confirm(
        `이번에 필요한 ${refresh.plannedTargetCount}명을 사람마다 최근 최대 ${refresh.days}일, 최대 ${refresh.matchesPerPlayer}판씩 수집할까요?`,
      );
      if (!confirmed) {
        return;
      }
    }

    setCollectionStarting(true);
    setCollectionStartError(null);
    try {
      await api.createPlayerMatchCollectionJob(confirmed);
      await jobCenter.refresh();
      const requestId = metaRequestSequence.current + 1;
      metaRequestSequence.current = requestId;
      const nextMeta = await api.getMeta();
      if (metaRequestSequence.current === requestId) {
        setMeta(nextMeta);
        setSharedRefreshError(null);
      }
    } catch (exc) {
      setCollectionStartError(
        exc instanceof Error ? exc.message : "매치 수집 시작 실패",
      );
    } finally {
      setCollectionStarting(false);
    }
  };

  const refreshMeta = async () => {
    setMetaRefreshing(true);
    setMetaRefreshError(null);
    try {
      const requestId = metaRequestSequence.current + 1;
      metaRequestSequence.current = requestId;
      const nextMeta = await api.refreshMeta();
      if (metaRequestSequence.current === requestId) {
        setMeta(nextMeta);
      }
    } catch (exc) {
      setMetaRefreshError(
        exc instanceof Error ? exc.message : "게임 정보 갱신 실패",
      );
    } finally {
      setMetaRefreshing(false);
    }
  };

  if (error) {
    return (
      <FatalError
        message={error}
        onRetry={() => {
          initialMetaRequest = null;
          initialPlayerMemosRequest = null;
          setError(null);
          setMeta(null);
          setStartupAttempt((current) => current + 1);
        }}
      />
    );
  }

  if (!meta) {
    return (
      <div className="boot">
        게임 정보를 확인하고 관심 유저의 자동 수집 필요 여부를 살펴보는 중...
      </div>
    );
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">
            MF
          </div>
          <div className="brand-copy">
            <span>내 컴퓨터의 경기</span>
            <strong>Matchup Finder</strong>
          </div>
        </div>
        <nav className="nav-list" aria-label="작업공간">
          <div className="nav-section">
            <span className="nav-section-title">검색 도구</span>
            <NavButton
              active={active === "db"}
              icon={<Database size={16} />}
              label="저장된 매치업"
              onClick={() => setActive("db")}
            />
            <NavButton
              active={active === "search"}
              icon={<Search size={16} />}
              label="개별 유저 검색"
              onClick={() => setActive("search")}
            />
            <NavButton
              active={active === "multi"}
              icon={<UploadCloud size={16} />}
              label="새 데이터 가져오기"
              onClick={() => setActive("multi")}
            />
          </div>
          <div className="nav-section">
            <span className="nav-section-title">기록 관리</span>
            <NavButton
              active={active === "memo"}
              icon={<StickyNote size={16} />}
              label="관심 유저"
              onClick={() => setActive("memo")}
            />
            <NavButton
              active={active === "history"}
              icon={<History size={16} />}
              label="최근검색 관리"
              onClick={() => setActive("history")}
            />
          </div>
        </nav>
        <div className="server-card">
          <div className="server-card-head">
            <div className="server-title">
              <span>시스템 상태</span>
              <strong>
                {meta.apiKeyDetected
                  ? "새 데이터 수집 가능"
                  : "저장된 데이터만 조회 가능"}
              </strong>
            </div>
            <span
              className={meta.apiKeyDetected ? "status ok" : "status danger"}
            >
              <i aria-hidden="true" />
              {meta.apiKeyDetected ? "준비됨" : "API 키 없음"}
            </span>
          </div>
          <dl className="server-facts">
            <div>
              <dt>저장된 경기</dt>
              <dd>
                {meta.dbIndexedMatchCount.toLocaleString("ko-KR")}개 조회 가능
                <small>
                  원본 {meta.dbMatchCount.toLocaleString("ko-KR")}개
                </small>
              </dd>
            </div>
            <div>
              <dt>마지막 경기</dt>
              <dd>{dbLastMatchText(meta.dbLastIndexedMatchCreation)}</dd>
            </div>
            <div>
              <dt>서버 환경</dt>
              <dd>
                {meta.region.toUpperCase()} · {meta.platform.toUpperCase()} ·
                솔로 랭크 {meta.queueId}
              </dd>
            </div>
            <div>
              <dt>게임 정보 버전</dt>
              <dd className="server-version">
                {meta.dataDragonVersion}
                <button
                  className="icon-button compact"
                  type="button"
                  onClick={refreshMeta}
                  disabled={metaRefreshing}
                  title="게임 정보 갱신"
                  aria-label="게임 정보 갱신"
                >
                  <RefreshCw
                    className={metaRefreshing ? "spin" : ""}
                    size={13}
                  />
                </button>
              </dd>
            </div>
          </dl>
          <div className={`server-match-collection ${meta.autoRefresh.status}`}>
            <div className="server-collection-head">
              <span>
                <RefreshCw
                  className={
                    collectionStarting || meta.autoRefresh.status === "running"
                      ? "spin"
                      : ""
                  }
                  size={13}
                />
                자동 매치 수집
              </span>
              <em>{meta.autoRefresh.targetCount}명</em>
            </div>
            <strong className="server-collection-state">
              {playerMatchCollectionSummary(meta)}
            </strong>
            <p className="server-collection-scope">
              {playerMatchCollectionScope(meta)}
            </p>
            {meta.autoRefresh.requiresConfirmation && (
              <p className="server-collection-confirmation">
                이번에 필요한 {meta.autoRefresh.plannedTargetCount}명 중 오래
                확인하지 않은 사람이 있어요. 지금 수집을 누르면 사람마다 최근
                최대 {meta.autoRefresh.days}일 · 최대{" "}
                {meta.autoRefresh.matchesPerPlayer}판을 확인해요.
              </p>
            )}
            <button
              className="server-collect-button"
              type="button"
              onClick={startPlayerMatchCollection}
              disabled={
                collectionStarting ||
                meta.autoRefresh.status === "running" ||
                meta.autoRefresh.targetCount === 0 ||
                !meta.apiKeyDetected
              }
              title="설정된 유저의 매치를 지금 수집"
            >
              <RefreshCw size={13} />
              {collectionStarting || meta.autoRefresh.status === "running"
                ? "수집 중"
                : "지금 수집"}
            </button>
            {meta.autoRefresh.status === "running" &&
              meta.autoRefresh.days !== null &&
              meta.autoRefresh.matchesPerPlayer !== null && (
                <em>
                  최근 {meta.autoRefresh.days}일 · 1인당 최대{" "}
                  {meta.autoRefresh.matchesPerPlayer}판
                </em>
              )}
            {autoRefreshJob && meta.autoRefresh.status === "running" && (
              <em>
                {Math.round(autoRefreshJob.progress * 100)}% ·{" "}
                {autoRefreshJob.message || "진행 중"}
              </em>
            )}
            {jobCenter.connectionMessage &&
              meta.autoRefresh.status === "running" && (
                <em className="server-error-text">
                  {jobCenter.connectionMessage}
                </em>
              )}
            {collectionStartError && (
              <em className="server-error-text">{collectionStartError}</em>
            )}
            {meta.autoRefresh.failures.length > 0 && (
              <details className="server-collection-failures">
                <summary>
                  실패한 유저 {meta.autoRefresh.failures.length}명
                </summary>
                <div>
                  {meta.autoRefresh.failures.map((failure) => (
                    <p key={failure.riot_id_raw}>
                      <strong>{failure.riot_id_raw}</strong>
                      <span>{failure.reason}</span>
                    </p>
                  ))}
                </div>
              </details>
            )}
          </div>
          <JobCenterPanel
            activeJobs={jobCenter.activeJobs}
            recentJobs={jobCenter.recentJobs}
            interruptedJobs={jobCenter.interruptedJobs}
            connectionState={jobCenter.connectionState}
            connectionMessage={jobCenter.connectionMessage}
            onOpen={(job) => {
              setRecoveryRequest((current) => ({
                jobId: job.jobId,
                sequence: current.sequence + 1,
              }));
              if (job.kind === "search") {
                setActive("search");
              } else if (isMultiCollectionJob(job)) {
                setActive("multi");
              }
            }}
          />
          {metaRefreshError && (
            <p className="server-error-text">{metaRefreshError}</p>
          )}
          {sharedRefreshError && (
            <p className="server-error-text">
              경기 데이터 현황 갱신 실패: {sharedRefreshError}
            </p>
          )}
        </div>
      </aside>

      <main className="workspace">
        <div hidden={active !== "search"}>
          <SearchWorkspace
            meta={meta}
            playerMemos={playerMemos}
            onCollectionCompleted={refreshMetaAndMemos}
            recoveryJob={searchRecoveryJob || null}
            recoveryRequestSequence={searchRecoveryRequestSequence}
            jobs={jobCenter.jobs}
            interruptedJobs={jobCenter.interruptedJobs}
            jobCenterReady={jobCenter.connectionState === "connected"}
            onJobCreated={jobCenter.refresh}
            riotIdHistory={riotIdHistory}
            setRiotIdHistory={setRiotIdHistory}
            enemyChampionHistory={enemyChampionHistory}
            setEnemyChampionHistory={setEnemyChampionHistory}
            myChampionHistory={myChampionHistory}
            setMyChampionHistory={setMyChampionHistory}
          />
        </div>
        <div hidden={active !== "multi"}>
          <MultiCollectWorkspace
            meta={meta}
            playerMemos={playerMemos}
            onCollectionCompleted={refreshMetaAndMemos}
            recoveryJob={multiRecoveryJob || null}
            recoveryRequestSequence={multiRecoveryRequestSequence}
            jobs={jobCenter.jobs}
            interruptedJobs={jobCenter.interruptedJobs}
            jobCenterReady={jobCenter.connectionState === "connected"}
            onJobCreated={jobCenter.refresh}
            riotIdHistory={riotIdHistory}
            setRiotIdHistory={setRiotIdHistory}
          />
        </div>
        <div hidden={active !== "db"}>
          <DbLookupWorkspace
            meta={meta}
            playerMemos={playerMemos}
            riotIdHistory={riotIdHistory}
            setRiotIdHistory={setRiotIdHistory}
            enemyChampionHistory={enemyChampionHistory}
            setEnemyChampionHistory={setEnemyChampionHistory}
            myChampionHistory={myChampionHistory}
            setMyChampionHistory={setMyChampionHistory}
          />
        </div>
        {active === "memo" && (
          <PlayerMemoWorkspace
            playerMemos={playerMemos}
            onCollectionSettingsChanged={async () => {
              await refreshMetaAndMemos();
            }}
          />
        )}
        {active === "history" && (
          <RecentHistoryWorkspace
            riotIdHistory={riotIdHistory}
            setRiotIdHistory={setRiotIdHistory}
            enemyChampionHistory={enemyChampionHistory}
            setEnemyChampionHistory={setEnemyChampionHistory}
            myChampionHistory={myChampionHistory}
            setMyChampionHistory={setMyChampionHistory}
          />
        )}
      </main>
    </div>
  );
}

function FatalError({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="fatal">
      <strong>앱을 시작할 수 없습니다.</strong>
      <p>{message}</p>
      <button className="primary-action" type="button" onClick={onRetry}>
        시작 준비 다시 시도
      </button>
    </div>
  );
}

function NavButton({
  active,
  icon,
  label,
  onClick,
}: {
  active: boolean;
  icon: ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      className={`nav-button ${active ? "active" : ""}`}
      type="button"
      aria-current={active ? "page" : undefined}
      onClick={onClick}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}

function jobStatusText(status: JobStatus["status"]): string {
  if (status === "queued") return "준비 중";
  if (status === "running") return "진행 중";
  if (status === "succeeded") return "완료";
  if (status === "partial") return "일부 확인 필요";
  return "실패";
}

function isMultiCollectionJob(job: Pick<JobStatus, "kind">): boolean {
  return job.kind === "multi-search" || job.kind === "player-match-collection";
}

function JobCenterPanel({
  activeJobs,
  recentJobs,
  interruptedJobs,
  connectionState,
  connectionMessage,
  onOpen,
}: {
  activeJobs: JobStatus[];
  recentJobs: JobStatus[];
  interruptedJobs: InterruptedJob[];
  connectionState: JobConnectionState;
  connectionMessage: string | null;
  onOpen: (job: JobStatus) => void;
}) {
  const [showAllRecent, setShowAllRecent] = useState(false);
  const visibleRecent = showAllRecent ? recentJobs : recentJobs.slice(0, 3);
  return (
    <section className="job-center">
      <p className="sr-only" role="status" aria-live="polite">
        {recentJobs[0]
          ? `${recentJobs[0].title} ${jobStatusText(recentJobs[0].status)}`
          : ""}
      </p>
      <div className="job-center-head">
        <span>경기 데이터 작업</span>
        <strong>
          {activeJobs.length ? `${activeJobs.length}개 진행 중` : "대기"}
        </strong>
      </div>
      {connectionState === "loading" && <p>작업 기록을 확인하고 있어요.</p>}
      {connectionState === "reconnecting" && (
        <p className="server-error-text">
          {connectionMessage || "서버 작업에 다시 연결하고 있어요."}
        </p>
      )}
      {interruptedJobs.map((job) => (
        <div className="job-center-item interrupted" key={`old-${job.jobId}`}>
          <div>
            <strong>{job.title}</strong>
            <span>{job.targetSummary}</span>
          </div>
          <em>중단됨 · 최종 상태 확인 불가</em>
          <p>서버는 바뀌었지만 이미 저장을 마친 경기는 남아 있어요.</p>
        </div>
      ))}
      {activeJobs.map((job) => (
        <div className="job-center-item running" key={job.jobId}>
          <div>
            <strong>{job.title}</strong>
            <span>{job.targetSummary}</span>
          </div>
          <em
            role="progressbar"
            aria-label={`${job.title} 진행률`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(job.progress * 100)}
          >
            {Math.round(job.progress * 100)}% · {job.message || "진행 중"}
          </em>
          <div className="job-center-bar" aria-hidden="true">
            <div style={{ width: `${Math.round(job.progress * 100)}%` }} />
          </div>
          {(job.kind === "search" || isMultiCollectionJob(job)) && (
            <button type="button" onClick={() => onOpen(job)}>
              이어보기
            </button>
          )}
        </div>
      ))}
      {visibleRecent.map((job) => (
        <div className={`job-center-item ${job.status}`} key={job.jobId}>
          <div>
            <strong>{job.title}</strong>
            <span>{job.targetSummary}</span>
          </div>
          <em>
            {jobStatusText(job.status)} · {job.message}
          </em>
          {job.status === "failed" && job.error && (
            <p className="server-error-text">{job.error}</p>
          )}
          {job.resultAvailable &&
            (job.kind === "search" || isMultiCollectionJob(job)) && (
              <button type="button" onClick={() => onOpen(job)}>
                결과 보기
              </button>
            )}
        </div>
      ))}
      {recentJobs.length > 3 && (
        <button
          className="tool-button"
          type="button"
          aria-expanded={showAllRecent}
          onClick={() => setShowAllRecent((current) => !current)}
        >
          {showAllRecent
            ? "최근 작업 접기"
            : `최근 작업 ${recentJobs.length}개 모두 보기`}
        </button>
      )}
      {connectionState === "connected" &&
        activeJobs.length === 0 &&
        visibleRecent.length === 0 &&
        interruptedJobs.length === 0 && <p>아직 실행한 작업이 없어요.</p>}
    </section>
  );
}

function SearchWorkspace({
  meta,
  playerMemos,
  onCollectionCompleted,
  recoveryJob,
  recoveryRequestSequence,
  jobs,
  interruptedJobs,
  jobCenterReady,
  onJobCreated,
  riotIdHistory,
  setRiotIdHistory,
  enemyChampionHistory,
  setEnemyChampionHistory,
  myChampionHistory,
  setMyChampionHistory,
}: {
  meta: MetaOptions;
  playerMemos: PlayerMemoState;
  onCollectionCompleted: () => Promise<void>;
  recoveryJob: JobStatus | null;
  recoveryRequestSequence: number;
  jobs: JobStatus[];
  interruptedJobs: InterruptedJob[];
  jobCenterReady: boolean;
  onJobCreated: () => Promise<void>;
  riotIdHistory: string[];
  setRiotIdHistory: Dispatch<SetStateAction<string[]>>;
  enemyChampionHistory: string[];
  setEnemyChampionHistory: Dispatch<SetStateAction<string[]>>;
  myChampionHistory: string[];
  setMyChampionHistory: Dispatch<SetStateAction<string[]>>;
}) {
  const riotIdInputRef = useRef<HTMLInputElement | null>(null);
  const [body, setBody] = useState<SearchBody>({
    riotIdRaw: "",
    periodKind: meta.defaultPeriod,
    customStart: daysAgoText(90),
    customEnd: todayText(),
    myChampionKorean: meta.champions.find(
      (champ) => champ.koreanName === "아리",
    )
      ? "아리"
      : meta.champions[0].koreanName,
    enemyChampionKorean: meta.allLabel,
    laneLabel: "미드",
    maxMatches: 100,
  });
  const [jobId, setJobId] = useState<string | null>(null);
  const [resultJobId, setResultJobId] = useState<string | null>(null);
  const [payload, setPayload] = useState<SearchPayload | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const exportRequestSequence = useRef(0);
  const [jobActive, setJobActive] = useState(false);
  const jobActiveRef = useRef(false);
  const pendingRecoveryJobIdRef = useRef<string | null>(null);
  const observedJobIdsRef = useRef<Set<string>>(new Set());

  const activateJob = (nextJobId: string) => {
    jobActiveRef.current = true;
    setJobActive(true);
    exportRequestSequence.current += 1;
    setExporting(false);
    setResultJobId(null);
    setExportError(null);
    setPayload(null);
    setJobId(nextJobId);
  };

  const finishJob = () => {
    const pendingJobId = pendingRecoveryJobIdRef.current;
    pendingRecoveryJobIdRef.current = null;
    if (pendingJobId) {
      activateJob(pendingJobId);
      return;
    }
    jobActiveRef.current = false;
    setJobActive(false);
    setJobId(null);
  };

  const trackedJob = jobs.find((job) => job.jobId === jobId) || null;
  const completion = useJobCompletion(
    trackedJob,
    async (status, isCurrent) => {
      const result = await api.getJobResult(status.jobId);
      if (!isCurrent()) {
        return;
      }
      setResultJobId(status.jobId);
      setPayload(result.payload as SearchPayload);
      await onCollectionCompleted();
      if (isCurrent()) {
        finishJob();
      }
    },
    async (_status, isCurrent) => {
      try {
        await onCollectionCompleted();
      } finally {
        if (isCurrent()) {
          finishJob();
        }
      }
    },
  );

  useEffect(() => {
    const nextJobId = recoveryJob?.jobId;
    if (!nextJobId) {
      return;
    }
    if (nextJobId === jobId) {
      pendingRecoveryJobIdRef.current = null;
      return;
    }
    if (jobActiveRef.current || !jobCenterReady) {
      pendingRecoveryJobIdRef.current = nextJobId;
      return;
    }
    pendingRecoveryJobIdRef.current = null;
    completion.setError(null);
    activateJob(nextJobId);
  }, [recoveryJob?.jobId, recoveryRequestSequence]);

  useEffect(() => {
    const pendingJobId = pendingRecoveryJobIdRef.current;
    if (!jobCenterReady || jobActiveRef.current || !pendingJobId) {
      return;
    }
    pendingRecoveryJobIdRef.current = null;
    completion.setError(null);
    activateJob(pendingJobId);
  }, [jobCenterReady]);

  useEffect(() => {
    if (jobId && trackedJob) {
      observedJobIdsRef.current.add(jobId);
    }
  }, [jobId, trackedJob]);

  useEffect(() => {
    if (!jobId) {
      return;
    }
    const interrupted = interruptedJobs.some((job) => job.jobId === jobId);
    const disappeared =
      observedJobIdsRef.current.has(jobId) && trackedJob === null;
    if (!interrupted && !disappeared) {
      return;
    }
    completion.setError(
      "서버가 다시 시작되어 이 작업의 마지막 상태는 확인할 수 없어요. 이미 저장을 마친 경기는 내 컴퓨터에 남아 있어요.",
    );
    finishJob();
    void onCollectionCompleted();
  }, [interruptedJobs, jobId, trackedJob]);

  const rememberHistory = (riotIdRaw: string) => {
    const normalized = normalizeSingleRiotIdInput(riotIdRaw);
    if (!normalized) {
      return riotIdRaw;
    }
    setRiotIdHistory((prev) => {
      const next = compactRiotIdHistory([normalized, ...prev]);
      saveRiotIdHistory(next);
      return next;
    });
    return normalized;
  };

  const rememberEnemyChampionHistory = (enemyChampion: string) => {
    setEnemyChampionHistory((prev) => {
      const next = compactEnemyChampionHistory([enemyChampion, ...prev], meta);
      saveEnemyChampionHistory(next);
      return next;
    });
  };

  const rememberMyChampionHistory = (myChampion: string) => {
    setMyChampionHistory((prev) => {
      const next = compactMyChampionHistory([myChampion, ...prev], meta);
      saveMyChampionHistory(next);
      return next;
    });
  };

  const applyHistoryItem = (riotId: string) => {
    setBody({ ...body, riotIdRaw: riotId });
    window.requestAnimationFrame(() => {
      riotIdInputRef.current?.focus();
      riotIdInputRef.current?.setSelectionRange(riotId.length, riotId.length);
    });
  };

  const applyMemoItem = (item: PlayerMemo) => {
    const nextValue = playerMemoName(item);
    setBody({ ...body, riotIdRaw: nextValue });
    window.requestAnimationFrame(() => {
      riotIdInputRef.current?.focus();
      riotIdInputRef.current?.setSelectionRange(
        nextValue.length,
        nextValue.length,
      );
    });
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (jobActiveRef.current || !jobCenterReady) {
      return;
    }
    jobActiveRef.current = true;
    setJobActive(true);
    exportRequestSequence.current += 1;
    setExporting(false);
    setResultJobId(null);
    setExportError(null);
    setPayload(null);
    completion.setError(null);
    const nextBody = { ...body, riotIdRaw: rememberHistory(body.riotIdRaw) };
    rememberMyChampionHistory(nextBody.myChampionKorean);
    rememberEnemyChampionHistory(nextBody.enemyChampionKorean);
    setBody(nextBody);
    try {
      const created = await api.createSearchJob(nextBody);
      setJobId(created.jobId);
      await onJobCreated();
    } catch (exc) {
      finishJob();
      completion.setError(
        exc instanceof Error ? exc.message : "검색 작업 시작에 실패했습니다.",
      );
    }
  };

  const exportCsv = async () => {
    if (!resultJobId || exporting) {
      return;
    }
    const exportRequestId = exportRequestSequence.current + 1;
    exportRequestSequence.current = exportRequestId;
    setExporting(true);
    setExportError(null);
    try {
      const result = await api.exportSearch(resultJobId);
      if (exportRequestSequence.current !== exportRequestId) {
        return;
      }
      downloadBlob(result.blob, result.filename);
    } catch (exc) {
      if (exportRequestSequence.current === exportRequestId) {
        setExportError(
          exc instanceof Error ? exc.message : "CSV 다운로드에 실패했습니다.",
        );
      }
    } finally {
      if (exportRequestSequence.current === exportRequestId) {
        setExporting(false);
      }
    }
  };

  return (
    <section className="screen">
      <WorkspaceHeader
        title="개별 유저 매치업 검색"
        description="한 유저의 최근 솔로 랭크를 불러와 챔피언, 상대, 라인 조건에 맞는 경기만 찾아요."
      />
      <form className="filter-panel" onSubmit={submit}>
        <div className="filter-target">
          <label className="field">
            <span>조회 유저</span>
            <input
              ref={riotIdInputRef}
              value={body.riotIdRaw}
              onChange={(event) =>
                setBody({ ...body, riotIdRaw: event.target.value })
              }
              placeholder="Hide on bush#KR1"
            />
          </label>
          <MemoPlayerSelect state={playerMemos} onPick={applyMemoItem} />
        </div>
        <div className="filter-conditions">
          <div className="filter-fields five">
            <ChampionSelect
              label="기준 챔피언"
              value={body.myChampionKorean}
              meta={meta}
              onChange={(value) =>
                setBody({ ...body, myChampionKorean: value })
              }
            />
            <SelectField
              label="상대 챔피언"
              value={body.enemyChampionKorean}
              options={[
                meta.allLabel,
                ...meta.champions.map((champ) => champ.koreanName),
              ]}
              onChange={(value) =>
                setBody({ ...body, enemyChampionKorean: value })
              }
            />
            <SelectField
              label="라인"
              value={body.laneLabel}
              options={meta.laneOptions}
              onChange={(value) => setBody({ ...body, laneLabel: value })}
            />
            <SelectField
              label="기간"
              value={body.periodKind}
              options={meta.periodOptions}
              onChange={(value) => setBody({ ...body, periodKind: value })}
            />
            <SelectField
              label="최대 매치"
              value={String(body.maxMatches)}
              options={["50", "100", "200", "300"]}
              onChange={(value) =>
                setBody({ ...body, maxMatches: Number(value) })
              }
            />
            {body.periodKind === meta.customPeriodLabel && (
              <div className="filter-dates">
                <DateField
                  label="시작일"
                  value={body.customStart || ""}
                  onChange={(value) => setBody({ ...body, customStart: value })}
                />
                <DateField
                  label="종료일"
                  value={body.customEnd || ""}
                  onChange={(value) => setBody({ ...body, customEnd: value })}
                />
              </div>
            )}
          </div>
          <div className="recent-group">
            <RecentMyChampionSearches
              history={myChampionHistory}
              onPick={(value) => setBody({ ...body, myChampionKorean: value })}
            />
            <RecentEnemyChampionSearches
              history={enemyChampionHistory}
              onPick={(value) =>
                setBody({ ...body, enemyChampionKorean: value })
              }
            />
            <RecentRiotIdSearches
              history={riotIdHistory}
              onPick={applyHistoryItem}
            />
          </div>
        </div>
        <div className="filter-foot">
          <button
            className="primary-action"
            type="submit"
            disabled={jobActive || !jobCenterReady}
          >
            <Search size={16} />
            {!jobCenterReady
              ? "기존 작업 확인 중"
              : jobActive
                ? "검색 중"
                : "검색 실행"}
          </button>
        </div>
      </form>

      <JobProgress job={trackedJob} error={completion.error} />
      {payload && (
        <>
          <SearchSummary payload={payload} />
          <MissingMemoPanel
            candidates={[
              {
                puuid: payload.account.puuid,
                riot_id: `${payload.account.game_name}#${payload.account.tag_line}`,
              },
            ]}
            memos={payload.memos}
            onSaved={async (puuid, memo) => {
              setPayload((current) =>
                current
                  ? {
                      ...current,
                      memos: { ...current.memos, [puuid]: memo },
                    }
                  : current,
              );
              await playerMemos.refresh();
            }}
          />
          <div className="toolbar">
            <EnemyRiotIdsCopyButton
              rows={payload.results}
              labelPrefix="검색 결과"
            />
            <button
              className="tool-button"
              type="button"
              onClick={() => void exportCsv()}
              disabled={exporting}
            >
              <Download size={16} />
              {exporting ? "CSV 만드는 중" : "CSV 다운로드"}
            </button>
          </div>
          {exportError && <div className="error-box">{exportError}</div>}
          <MatchResultList
            rows={payload.results}
            meta={meta}
            focusPuuid={payload.account.puuid}
            showPlayerCopy={false}
            memos={payload.memos}
          />
        </>
      )}
    </section>
  );
}

function MultiCollectWorkspace({
  meta,
  playerMemos,
  onCollectionCompleted,
  recoveryJob,
  recoveryRequestSequence,
  jobs,
  interruptedJobs,
  jobCenterReady,
  onJobCreated,
  riotIdHistory,
  setRiotIdHistory,
}: {
  meta: MetaOptions;
  playerMemos: PlayerMemoState;
  onCollectionCompleted: () => Promise<void>;
  recoveryJob: JobStatus | null;
  recoveryRequestSequence: number;
  jobs: JobStatus[];
  interruptedJobs: InterruptedJob[];
  jobCenterReady: boolean;
  onJobCreated: () => Promise<void>;
  riotIdHistory: string[];
  setRiotIdHistory: Dispatch<SetStateAction<string[]>>;
}) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [body, setBody] = useState<MultiSearchBody>({
    riotIdsRaw: "",
    days: meta.defaultMultiDays,
    matchesPerPlayer: meta.defaultMultiMatchesPerPlayer,
  });
  const [jobId, setJobId] = useState<string | null>(null);
  const [payload, setPayload] = useState<MultiSearchPayload | null>(null);
  const [jobActive, setJobActive] = useState(false);
  const jobActiveRef = useRef(false);
  const pendingRecoveryJobIdRef = useRef<string | null>(null);
  const observedJobIdsRef = useRef<Set<string>>(new Set());

  const activateJob = (nextJobId: string) => {
    jobActiveRef.current = true;
    setJobActive(true);
    setPayload(null);
    setJobId(nextJobId);
  };

  const finishJob = () => {
    const pendingJobId = pendingRecoveryJobIdRef.current;
    pendingRecoveryJobIdRef.current = null;
    if (pendingJobId) {
      activateJob(pendingJobId);
      return;
    }
    jobActiveRef.current = false;
    setJobActive(false);
    setJobId(null);
  };
  const memoGroups = useMemo(
    () => groupPlayerMemos(playerMemos.items),
    [playerMemos.items],
  );

  const trackedJob = jobs.find((job) => job.jobId === jobId) || null;
  const completion = useJobCompletion(
    trackedJob,
    async (status, isCurrent) => {
      const result = await api.getJobResult(status.jobId);
      if (!isCurrent()) {
        return;
      }
      setPayload(result.payload as MultiSearchPayload);
      await onCollectionCompleted();
      if (isCurrent()) {
        finishJob();
      }
    },
    async (_status, isCurrent) => {
      try {
        await onCollectionCompleted();
      } finally {
        if (isCurrent()) {
          finishJob();
        }
      }
    },
  );

  useEffect(() => {
    const nextJobId = recoveryJob?.jobId;
    if (!nextJobId) {
      return;
    }
    if (nextJobId === jobId) {
      pendingRecoveryJobIdRef.current = null;
      return;
    }
    if (jobActiveRef.current || !jobCenterReady) {
      pendingRecoveryJobIdRef.current = nextJobId;
      return;
    }
    pendingRecoveryJobIdRef.current = null;
    completion.setError(null);
    activateJob(nextJobId);
  }, [recoveryJob?.jobId, recoveryRequestSequence]);

  useEffect(() => {
    const pendingJobId = pendingRecoveryJobIdRef.current;
    if (!jobCenterReady || jobActiveRef.current || !pendingJobId) {
      return;
    }
    pendingRecoveryJobIdRef.current = null;
    completion.setError(null);
    activateJob(pendingJobId);
  }, [jobCenterReady]);

  useEffect(() => {
    if (jobId && trackedJob) {
      observedJobIdsRef.current.add(jobId);
    }
  }, [jobId, trackedJob]);

  useEffect(() => {
    if (!jobId) {
      return;
    }
    const interrupted = interruptedJobs.some((job) => job.jobId === jobId);
    const disappeared =
      observedJobIdsRef.current.has(jobId) && trackedJob === null;
    if (!interrupted && !disappeared) {
      return;
    }
    completion.setError(
      "서버가 다시 시작되어 이 작업의 마지막 상태는 확인할 수 없어요. 이미 저장을 마친 경기는 내 컴퓨터에 남아 있어요.",
    );
    finishJob();
    void onCollectionCompleted();
  }, [interruptedJobs, jobId, trackedJob]);

  const rememberHistory = (riotIdsRaw: string) => {
    const normalized = normalizeRiotIdInput(riotIdsRaw);
    if (!normalized) {
      return riotIdsRaw;
    }
    setRiotIdHistory((prev) => {
      const next = compactRiotIdHistory([normalized, ...prev]);
      saveRiotIdHistory(next);
      return next;
    });
    return normalized;
  };

  const applyHistoryItem = (riotId: string) => {
    const nextValue = appendRiotIdInput(body.riotIdsRaw, riotId);
    setBody({ ...body, riotIdsRaw: nextValue });
    window.requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(
        nextValue.length,
        nextValue.length,
      );
    });
  };

  const applyMemoGroup = (
    items: PlayerMemo[],
    separator: "line" | "comma" = "line",
  ) => {
    const nextValue = appendRiotIdInputs(
      body.riotIdsRaw,
      items.map(playerMemoName),
      separator,
    );
    setBody({ ...body, riotIdsRaw: nextValue });
    window.requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(
        nextValue.length,
        nextValue.length,
      );
    });
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (jobActiveRef.current || !jobCenterReady) {
      return;
    }
    jobActiveRef.current = true;
    setJobActive(true);
    setPayload(null);
    completion.setError(null);
    const nextBody = { ...body, riotIdsRaw: rememberHistory(body.riotIdsRaw) };
    setBody(nextBody);
    try {
      const created = await api.createMultiSearchJob(nextBody);
      setJobId(created.jobId);
      await onJobCreated();
    } catch (exc) {
      finishJob();
      completion.setError(
        exc instanceof Error ? exc.message : "수집 작업 시작에 실패했습니다.",
      );
    }
  };

  return (
    <section className="screen">
      <WorkspaceHeader
        title="여러 유저의 경기 가져오기"
        description="Riot ID를 입력해 분석에 사용할 최근 솔로 랭크 경기를 내 컴퓨터에 모아요."
      />
      <form className="filter-panel" onSubmit={submit}>
        <div className="filter-target">
          <label className="field">
            <span>조회 유저 목록</span>
            <textarea
              ref={textareaRef}
              value={body.riotIdsRaw}
              onChange={(event) =>
                setBody({ ...body, riotIdsRaw: event.target.value })
              }
              placeholder={"Aoo#chi\nHide on bush#KR1, Keria#KR1"}
            />
          </label>
          <MemoGroupSelect
            groups={memoGroups}
            state={playerMemos}
            onPick={applyMemoGroup}
          />
        </div>
        <div className="filter-conditions">
          <div className="filter-fields two">
            <SelectField
              label="수집 기간"
              value={String(body.days)}
              options={meta.multiPeriodOptions.map(String)}
              format={(value) => `최근 ${value}일`}
              onChange={(value) => setBody({ ...body, days: Number(value) })}
            />
            <SelectField
              label="1인당 최대 매치"
              value={String(body.matchesPerPlayer)}
              options={meta.multiMatchesPerPlayerOptions.map(String)}
              format={(value) => `${value}판`}
              onChange={(value) =>
                setBody({ ...body, matchesPerPlayer: Number(value) })
              }
            />
          </div>
          <div className="recent-group">
            <RecentRiotIdSearches
              history={riotIdHistory}
              onPick={applyHistoryItem}
            />
          </div>
        </div>
        <div className="filter-foot">
          <button
            className="primary-action"
            type="submit"
            disabled={jobActive || !jobCenterReady}
          >
            <UploadCloud size={16} />
            {!jobCenterReady
              ? "기존 작업 확인 중"
              : jobActive
                ? "수집 중"
                : "수집 실행"}
          </button>
        </div>
      </form>
      <JobProgress job={trackedJob} error={completion.error} />
      {payload && (
        <>
          <MultiSummary payload={payload} />
          <MissingMemoPanel
            candidates={payload.resolved_users}
            memos={payload.memos}
            onSaved={async (puuid, memo) => {
              setPayload((current) =>
                current
                  ? {
                      ...current,
                      memos: { ...current.memos, [puuid]: memo },
                    }
                  : current,
              );
              await playerMemos.refresh();
            }}
          />
        </>
      )}
    </section>
  );
}

function DbLookupWorkspace({
  meta,
  playerMemos,
  riotIdHistory,
  setRiotIdHistory,
  enemyChampionHistory,
  setEnemyChampionHistory,
  myChampionHistory,
  setMyChampionHistory,
}: {
  meta: MetaOptions;
  playerMemos: PlayerMemoState;
  riotIdHistory: string[];
  setRiotIdHistory: Dispatch<SetStateAction<string[]>>;
  enemyChampionHistory: string[];
  setEnemyChampionHistory: Dispatch<SetStateAction<string[]>>;
  myChampionHistory: string[];
  setMyChampionHistory: Dispatch<SetStateAction<string[]>>;
}) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [body, setBody] = useState<DbSearchBody>({
    riotIdsRaw: "",
    myChampionKorean: meta.champions.find(
      (champ) => champ.koreanName === "아리",
    )
      ? "아리"
      : meta.champions[0].koreanName,
    enemyChampionKorean: meta.allLabel,
    laneLabel: "미드",
    periodKind: meta.defaultDbPeriod,
    customStart: daysAgoText(90),
    customEnd: todayText(),
    currentPatchOnly: false,
    page: 1,
    pageSize: PAGE_SIZE,
  });
  const [payload, setPayload] = useState<DbSearchPayload | null>(null);
  const [appliedBody, setAppliedBody] = useState<DbSearchBody | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [copyingAll, setCopyingAll] = useState(false);
  const [copyAllMessage, setCopyAllMessage] = useState<string | null>(null);
  const requestSequence = useRef(0);
  const copyRequestSequence = useRef(0);
  const memoGroups = useMemo(
    () => groupPlayerMemos(playerMemos.items),
    [playerMemos.items],
  );

  const runSearch = async (nextBody: DbSearchBody) => {
    const requestId = requestSequence.current + 1;
    requestSequence.current = requestId;
    copyRequestSequence.current += 1;
    setError(null);
    setCopyAllMessage(null);
    setCopyingAll(false);
    setSearching(true);
    try {
      const nextPayload = await api.dbSearch(nextBody);
      if (requestSequence.current !== requestId) {
        return;
      }
      setPayload(nextPayload);
      setAppliedBody({ ...nextBody, snapshot: nextPayload.snapshot });
    } catch (exc) {
      if (requestSequence.current === requestId) {
        setError(
          exc instanceof Error
            ? exc.message
            : "저장된 경기 조회에 실패했습니다.",
        );
      }
    } finally {
      if (requestSequence.current === requestId) {
        setSearching(false);
      }
    }
  };

  const rememberEnemyChampionHistory = (nextBody: DbSearchBody) => {
    setEnemyChampionHistory((prev) => {
      const next = compactEnemyChampionHistory(
        [nextBody.enemyChampionKorean, ...prev],
        meta,
      );
      saveEnemyChampionHistory(next);
      return next;
    });
  };

  const rememberMyChampionHistory = (nextBody: DbSearchBody) => {
    setMyChampionHistory((prev) => {
      const next = compactMyChampionHistory(
        [nextBody.myChampionKorean, ...prev],
        meta,
      );
      saveMyChampionHistory(next);
      return next;
    });
  };

  const rememberRiotIdHistory = (riotIdsRaw: string) => {
    const normalized = normalizeRiotIdInput(riotIdsRaw);
    if (!normalized) {
      return riotIdsRaw;
    }
    setRiotIdHistory((prev) => {
      const next = compactRiotIdHistory([normalized, ...prev]);
      saveRiotIdHistory(next);
      return next;
    });
    return normalized;
  };

  const applyHistoryItem = (riotId: string) => {
    const nextValue = appendRiotIdInput(body.riotIdsRaw, riotId);
    setBody({ ...body, riotIdsRaw: nextValue });
    window.requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(
        nextValue.length,
        nextValue.length,
      );
    });
  };

  const applyMemoGroup = (
    items: PlayerMemo[],
    separator: "line" | "comma" = "line",
  ) => {
    const nextValue = appendRiotIdInputs(
      body.riotIdsRaw,
      items.map(playerMemoName),
      separator,
    );
    setBody({ ...body, riotIdsRaw: nextValue });
    window.requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(
        nextValue.length,
        nextValue.length,
      );
    });
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (searching) {
      return;
    }
    const nextBody = {
      ...body,
      riotIdsRaw: rememberRiotIdHistory(body.riotIdsRaw),
      page: 1,
    };
    rememberMyChampionHistory(nextBody);
    rememberEnemyChampionHistory(nextBody);
    setBody(nextBody);
    void runSearch(nextBody);
  };

  const movePage = (page: number) => {
    if (!appliedBody || searching) {
      return;
    }
    void runSearch({ ...appliedBody, page });
  };

  const exportCsv = async () => {
    if (!appliedBody || exporting) {
      return;
    }
    setExporting(true);
    setError(null);
    try {
      const blob = await api.exportDbSearch(appliedBody);
      downloadBlob(blob, "db_matchup_results.csv");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "CSV 다운로드 실패");
    } finally {
      setExporting(false);
    }
  };

  const copyAllEnemyRiotIds = async () => {
    if (!appliedBody || copyingAll) {
      return;
    }
    const copyRequestId = copyRequestSequence.current + 1;
    copyRequestSequence.current = copyRequestId;
    setCopyingAll(true);
    setCopyAllMessage(null);
    try {
      const response = await api.getDbEnemyRiotIds(appliedBody);
      if (copyRequestSequence.current !== copyRequestId) {
        return;
      }
      if (response.items.length === 0) {
        setCopyAllMessage("복사할 상대 Riot ID가 없어요.");
        return;
      }
      await navigator.clipboard.writeText(response.items.join("\n"));
      if (copyRequestSequence.current !== copyRequestId) {
        return;
      }
      setCopyAllMessage(
        `전체 결과에서 ${response.items.length}명을 복사했어요.`,
      );
    } catch (exc) {
      if (copyRequestSequence.current === copyRequestId) {
        setCopyAllMessage(
          exc instanceof Error ? exc.message : "상대 Riot ID 전체 복사 실패",
        );
      }
    } finally {
      if (copyRequestSequence.current === copyRequestId) {
        setCopyingAll(false);
      }
    }
  };

  const pageCount = payload
    ? Math.max(Math.ceil(payload.total / payload.pageSize), 1)
    : 1;
  const hasUnappliedChanges =
    appliedBody !== null &&
    dbSearchCriteriaKey(body) !== dbSearchCriteriaKey(appliedBody);
  const hasNewStoredData =
    payload !== null && meta.dbIndexMaxId > payload.snapshot.maxIndexId;

  return (
    <section className="screen">
      <WorkspaceHeader
        title="저장된 매치업 조회"
        description="이미 수집된 경기에서 메모 유저 또는 입력한 유저의 매치업 통계와 목록을 빠르게 확인해요."
      />
      <form className="filter-panel" onSubmit={submit}>
        <div className="filter-target">
          <label className="field">
            <span>조회 유저 목록</span>
            <textarea
              ref={textareaRef}
              value={body.riotIdsRaw}
              onChange={(event) =>
                setBody({ ...body, riotIdsRaw: event.target.value })
              }
              placeholder={"비우면 관심 유저만\nHide on bush#KR1, Keria#KR1"}
            />
          </label>
          <MemoGroupSelect
            groups={memoGroups}
            state={playerMemos}
            onPick={applyMemoGroup}
          />
        </div>
        <div className="filter-conditions">
          <div className="filter-fields">
            <ChampionSelect
              label="기준 챔피언"
              value={body.myChampionKorean}
              meta={meta}
              onChange={(value) =>
                setBody({ ...body, myChampionKorean: value })
              }
            />
            <SelectField
              label="상대 챔피언"
              value={body.enemyChampionKorean}
              options={[
                meta.allLabel,
                ...meta.champions.map((champ) => champ.koreanName),
              ]}
              onChange={(value) =>
                setBody({ ...body, enemyChampionKorean: value })
              }
            />
            <SelectField
              label="라인"
              value={body.laneLabel}
              options={meta.laneOptions}
              onChange={(value) => setBody({ ...body, laneLabel: value })}
            />
            <SelectField
              label="기간"
              value={body.periodKind}
              options={meta.dbPeriodOptions}
              onChange={(value) => setBody({ ...body, periodKind: value })}
            />
            {body.periodKind === meta.customPeriodLabel && (
              <div className="filter-dates">
                <DateField
                  label="시작일"
                  value={body.customStart || ""}
                  onChange={(value) => setBody({ ...body, customStart: value })}
                />
                <DateField
                  label="종료일"
                  value={body.customEnd || ""}
                  onChange={(value) => setBody({ ...body, customEnd: value })}
                />
              </div>
            )}
          </div>
          <div className="recent-group">
            <RecentMyChampionSearches
              history={myChampionHistory}
              onPick={(value) => setBody({ ...body, myChampionKorean: value })}
            />
            <RecentEnemyChampionSearches
              history={enemyChampionHistory}
              onPick={(value) =>
                setBody({ ...body, enemyChampionKorean: value })
              }
            />
            <RecentRiotIdSearches
              history={riotIdHistory}
              onPick={applyHistoryItem}
            />
          </div>
        </div>
        <div className="filter-foot">
          <label className="check-field">
            <input
              type="checkbox"
              checked={body.currentPatchOnly}
              onChange={(event) =>
                setBody({ ...body, currentPatchOnly: event.target.checked })
              }
            />
            <span>이번 패치만</span>
          </label>
          <button className="primary-action" type="submit" disabled={searching}>
            <Database size={16} />
            {searching ? "조회 중" : "저장된 경기 조회"}
          </button>
        </div>
      </form>

      {error && <div className="error-box">{error}</div>}
      {!payload && !searching && meta.dbIndexedMatchCount === 0 && (
        <div className="empty">
          <strong>아직 조회할 수 있는 저장 경기가 없어요.</strong>
          <p>
            개별 유저 검색이나 새 데이터 가져오기에서 경기를 먼저 모아 주세요.
          </p>
        </div>
      )}
      {hasUnappliedChanges && (
        <div className="db-filter-notice" role="status">
          조건을 바꿨어요. 아래 결과와 CSV는 마지막으로 조회한 조건을 그대로
          사용해요. 새 조건을 보려면 저장된 경기 조회를 다시 눌러 주세요.
        </div>
      )}
      {hasNewStoredData && appliedBody && (
        <div className="db-filter-notice" role="status">
          <span>
            새 경기 데이터가 들어왔어요. 지금 결과는 이전 조회 시점이에요.
          </span>
          <button
            className="tool-button"
            type="button"
            disabled={searching}
            onClick={() =>
              void runSearch({
                ...appliedBody,
                page: 1,
                snapshot: undefined,
              })
            }
          >
            <RefreshCw size={16} />
            같은 조건 다시 조회
          </button>
        </div>
      )}
      {payload && (
        <>
          <DbSummary payload={payload} />
          <div className="toolbar">
            <button
              className="tool-button"
              type="button"
              onClick={copyAllEnemyRiotIds}
              disabled={copyingAll || searching}
            >
              <Copy size={16} />
              {copyingAll ? "전체 결과 확인 중" : "전체 결과 상대 Riot ID 복사"}
            </button>
            {copyAllMessage && <span role="status">{copyAllMessage}</span>}
            <button
              className="tool-button"
              type="button"
              onClick={exportCsv}
              disabled={exporting || searching}
            >
              <Download size={16} />
              {exporting ? "CSV 준비 중" : "CSV 다운로드"}
            </button>
            <div className="pager">
              <button
                type="button"
                aria-label="이전 페이지"
                disabled={payload.page <= 1 || searching}
                onClick={() => movePage(payload.page - 1)}
              >
                <ChevronLeft size={16} />
              </button>
              <span>
                {payload.page} / {pageCount}
              </span>
              <button
                type="button"
                aria-label="다음 페이지"
                disabled={payload.page >= pageCount || searching}
                onClick={() => movePage(payload.page + 1)}
              >
                <ChevronRight size={16} />
              </button>
            </div>
          </div>
          <MatchResultList
            rows={payload.results}
            meta={meta}
            showPlayerCopy
            clientPagination={false}
            memos={payload.memos}
            emptyTitle={
              payload.snapshot.targetPuuids.length === 0
                ? "조회할 관심 유저가 없어요."
                : "이 조건에 맞는 저장 경기가 없어요."
            }
            emptyDescription={
              payload.snapshot.targetPuuids.length === 0
                ? "관심 유저를 등록하거나 조회 유저 목록에 Riot ID를 입력해 주세요."
                : "새 데이터를 가져오거나 기간·챔피언·라인 조건을 확인해 주세요."
            }
          />
        </>
      )}
    </section>
  );
}

function PlayerMemoWorkspace({
  playerMemos,
  onCollectionSettingsChanged,
}: {
  playerMemos: PlayerMemoState;
  onCollectionSettingsChanged: () => Promise<void>;
}) {
  const items = playerMemos.items;
  const [riotIdRaw, setRiotIdRaw] = useState("");
  const [memo, setMemo] = useState("");
  const [filter, setFilter] = useState("");
  const [editing, setEditing] = useState<Record<string, string>>({});
  const [editingPuuid, setEditingPuuid] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const target = await api.resolvePlayerMemoRiotId(riotIdRaw);
      await api.upsertPlayerMemo(target.puuid, memo);
      setRiotIdRaw("");
      setMemo("");
      await playerMemos.refresh();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "메모 저장 실패");
    } finally {
      setSaving(false);
    }
  };

  const saveExisting = async (item: PlayerMemo) => {
    setSaving(true);
    setError(null);
    try {
      const nextMemo = editing[item.puuid] || "";
      await api.upsertPlayerMemo(item.puuid, nextMemo);
      setEditing({ ...editing, [item.puuid]: nextMemo });
      setEditingPuuid(null);
      await playerMemos.refresh();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "메모 저장 실패");
    } finally {
      setSaving(false);
    }
  };

  const saveStartupCollection = async (item: PlayerMemo, enabled: boolean) => {
    setSaving(true);
    setError(null);
    try {
      await api.updatePlayerMemoAutoRefresh(item.puuid, enabled);
      await onCollectionSettingsChanged();
    } catch (exc) {
      setError(
        exc instanceof Error ? exc.message : "앱 시작 시 수집 설정 실패",
      );
    } finally {
      setSaving(false);
    }
  };

  const startEditing = (item: PlayerMemo) => {
    setEditing({ ...editing, [item.puuid]: editing[item.puuid] ?? item.memo });
    setEditingPuuid(item.puuid);
  };

  const cancelEditing = (item: PlayerMemo) => {
    setEditing({ ...editing, [item.puuid]: item.memo });
    setEditingPuuid(null);
  };

  const deleteMemo = async (item: PlayerMemo) => {
    const name = riotId(
      item.riot_id_game_name || undefined,
      item.riot_id_tag_line || undefined,
      item.puuid,
    );
    if (
      !window.confirm(
        `${name}을 관심 유저에서 삭제할까요?\n\n` +
          "저장된 경기 데이터는 남지만, 저장된 매치업의 기본 조회 대상과 " +
          "앱 시작 자동 수집 대상에서는 빠져요.",
      )
    ) {
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await api.deletePlayerMemo(item.puuid);
      await onCollectionSettingsChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "관심 유저 삭제 실패");
    } finally {
      setSaving(false);
    }
  };

  const filteredItems = items.filter((item) => {
    const query = filter.trim().toLocaleLowerCase();
    if (!query) {
      return true;
    }
    const name = playerMemoName(item).toLocaleLowerCase();
    return (
      name.includes(query) || item.memo.toLocaleLowerCase().includes(query)
    );
  });
  const memoGroups = groupPlayerMemos(filteredItems);

  return (
    <section className="screen">
      <WorkspaceHeader
        title="관심 유저 관리"
        description="저장된 매치업의 기본 대상과 앱 시작 수집 대상을 관리해요. 메모는 비워도 괜찮아요."
      />
      <form className="memo-form" onSubmit={submit}>
        <label className="field">
          <span>Riot ID</span>
          <input
            value={riotIdRaw}
            onChange={(event) => setRiotIdRaw(event.target.value)}
            placeholder="Hide on bush#KR1"
          />
        </label>
        <label className="field">
          <span>메모</span>
          <input
            value={memo}
            onChange={(event) => setMemo(event.target.value)}
            placeholder="Faker"
          />
        </label>
        <button className="primary-action" type="submit" disabled={saving}>
          <Plus size={16} />
          추가
        </button>
      </form>
      {error && <div className="error-box">{error}</div>}
      {playerMemos.error && (
        <div className="error-box memo-load-error">
          <span>메모 목록을 불러오지 못했어요. {playerMemos.error}</span>
          <button
            className="tool-button"
            type="button"
            onClick={() => void playerMemos.refresh()}
            disabled={playerMemos.loading}
          >
            <RefreshCw size={16} />
            다시 불러오기
          </button>
        </div>
      )}
      <section className="memo-panel">
        <div className="memo-panel-head">
          <div>
            <span>등록된 관심 유저</span>
            <strong>
              {playerMemos.loading && items.length === 0
                ? "불러오는 중"
                : `${items.length}명`}
            </strong>
          </div>
          <label className="field">
            <span>검색</span>
            <input
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="닉네임 또는 메모"
            />
          </label>
        </div>
        {playerMemos.loading && items.length === 0 ? (
          <div className="empty compact">
            <strong>메모 목록을 불러오는 중이에요.</strong>
          </div>
        ) : playerMemos.error &&
          items.length === 0 ? null : filteredItems.length === 0 ? (
          <div className="empty compact">
            <strong>
              {items.length === 0
                ? "등록된 관심 유저가 없습니다."
                : "검색 조건에 맞는 관심 유저가 없습니다."}
            </strong>
          </div>
        ) : (
          <div className="memo-list">
            {memoGroups.map((group) => {
              return (
                <div className="memo-group" key={group.memoText}>
                  <div className="memo-group-label">
                    <strong>{group.memoText}</strong>
                    <span>
                      닉네임 {group.items.length}개 · 전체 게임{" "}
                      {group.gameCount.toLocaleString()}개
                    </span>
                  </div>
                  <div className="memo-group-players">
                    {group.items.map((item) => {
                      const isEditing = editingPuuid === item.puuid;
                      const name = playerMemoName(item);
                      return (
                        <div
                          className={`memo-row ${isEditing ? "editing" : ""}`}
                          key={item.puuid}
                        >
                          {isEditing && (
                            <div className="memo-edit-cell">
                              <input
                                value={editing[item.puuid] ?? item.memo}
                                onChange={(event) =>
                                  setEditing({
                                    ...editing,
                                    [item.puuid]: event.target.value,
                                  })
                                }
                              />
                            </div>
                          )}
                          <div className="memo-player">
                            <strong>{name}</strong>
                            <span>{playerCollectionStatus(item)}</span>
                            <small>메모 수정 {memoDate(item.updated_at)}</small>
                          </div>
                          <div className="memo-actions">
                            <label className="memo-auto-refresh">
                              <input
                                type="checkbox"
                                checked={item.auto_refresh_enabled}
                                onChange={(event) =>
                                  saveStartupCollection(
                                    item,
                                    event.target.checked,
                                  )
                                }
                                disabled={saving}
                              />
                              앱 시작 시 수집
                            </label>
                            {isEditing ? (
                              <>
                                <button
                                  className="icon-button"
                                  type="button"
                                  title="저장"
                                  onClick={() => saveExisting(item)}
                                  disabled={saving}
                                >
                                  <Save size={16} />
                                </button>
                                <button
                                  className="icon-button"
                                  type="button"
                                  title="취소"
                                  onClick={() => cancelEditing(item)}
                                  disabled={saving}
                                >
                                  <X size={16} />
                                </button>
                              </>
                            ) : (
                              <button
                                className="icon-button"
                                type="button"
                                title="수정"
                                onClick={() => startEditing(item)}
                                disabled={saving}
                              >
                                <Pencil size={16} />
                              </button>
                            )}
                            <button
                              className="icon-button danger"
                              type="button"
                              title="관심 유저 삭제"
                              onClick={() => deleteMemo(item)}
                              disabled={saving}
                            >
                              <Trash2 size={16} />
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </section>
  );
}

function MissingMemoPanel({
  candidates,
  memos,
  onSaved,
}: {
  candidates: MemoTarget[];
  memos: Record<string, string>;
  onSaved: (puuid: string, memo: string) => Promise<void> | void;
}) {
  const missing = candidates.filter(
    (candidate) =>
      !Object.prototype.hasOwnProperty.call(memos, candidate.puuid),
  );
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [savingPuuid, setSavingPuuid] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (missing.length === 0) {
    return null;
  }

  const save = async (candidate: MemoTarget) => {
    const memo = (drafts[candidate.puuid] || "").trim();
    if (!memo) {
      setError("메모를 입력해주세요.");
      return;
    }
    setSavingPuuid(candidate.puuid);
    setError(null);
    try {
      await api.upsertPlayerMemo(candidate.puuid, memo);
      await onSaved(candidate.puuid, memo);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "메모 저장 실패");
    } finally {
      setSavingPuuid(null);
    }
  };

  return (
    <section className="memo-suggestion">
      <div className="memo-suggestion-head">
        <span>처음 검색하는 유저입니다.</span>
        <strong>메모에 추가할까요?</strong>
      </div>
      {error && <p className="memo-suggestion-error">{error}</p>}
      <div className="memo-suggestion-list">
        {missing.map((candidate) => (
          <div className="memo-suggestion-row" key={candidate.puuid}>
            <strong>{candidate.riot_id}</strong>
            <input
              value={drafts[candidate.puuid] || ""}
              onChange={(event) =>
                setDrafts({
                  ...drafts,
                  [candidate.puuid]: event.target.value,
                })
              }
              placeholder="메모"
            />
            <button
              className="tool-button"
              type="button"
              disabled={savingPuuid === candidate.puuid}
              onClick={() => save(candidate)}
            >
              <Plus size={16} />
              추가
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}

function WorkspaceHeader({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <header className="workspace-header">
      <h1>{title}</h1>
      <p>{description}</p>
    </header>
  );
}

function MemoPlayerSelect({
  state,
  onPick,
}: {
  state: PlayerMemoState;
  onPick: (item: PlayerMemo) => void;
}) {
  const { items, loading, error } = state;
  return (
    <label className="field">
      <span>메모된 유저</span>
      <select
        value=""
        onChange={(event) => {
          const item = items.find(
            (candidate) => candidate.puuid === event.target.value,
          );
          if (item) {
            onPick(item);
          }
        }}
        disabled={loading || items.length === 0}
        title={error || undefined}
      >
        <option value="">
          {loading
            ? "메모 불러오는 중"
            : error
              ? "메모 불러오기 실패"
              : items.length
                ? "메모에서 선택"
                : "등록된 메모 없음"}
        </option>
        {items.map((item) => (
          <option value={item.puuid} key={item.puuid}>
            {item.memo} · {playerMemoName(item)}
          </option>
        ))}
      </select>
    </label>
  );
}

function MemoGroupSelect({
  groups,
  state,
  onPick,
}: {
  groups: ReturnType<typeof groupPlayerMemos>;
  state: PlayerMemoState;
  onPick: (items: PlayerMemo[], separator?: "line" | "comma") => void;
}) {
  const allItems = groups.flatMap((group) => group.items);

  return (
    <label className="field">
      <span>메모 선택</span>
      <select
        value=""
        onChange={(event) => {
          if (event.target.value === "all") {
            onPick(allItems, "comma");
            return;
          }
          const groupIndex = Number(event.target.value);
          const group = groups[groupIndex];
          if (group) {
            onPick(group.items);
          }
        }}
        disabled={state.loading || groups.length === 0}
        title={state.error || undefined}
      >
        <option value="">
          {state.loading
            ? "메모 불러오는 중"
            : state.error
              ? "메모 불러오기 실패"
              : groups.length
                ? "메모에서 선택"
                : "등록된 메모 없음"}
        </option>
        {groups.length > 0 && <option value="all">전체</option>}
        {groups.map((group, index) => (
          <option value={index} key={group.memoText}>
            {group.memoText}
          </option>
        ))}
      </select>
    </label>
  );
}

function RecentHistoryWorkspace({
  riotIdHistory,
  setRiotIdHistory,
  enemyChampionHistory,
  setEnemyChampionHistory,
  myChampionHistory,
  setMyChampionHistory,
}: {
  riotIdHistory: string[];
  setRiotIdHistory: Dispatch<SetStateAction<string[]>>;
  enemyChampionHistory: string[];
  setEnemyChampionHistory: Dispatch<SetStateAction<string[]>>;
  myChampionHistory: string[];
  setMyChampionHistory: Dispatch<SetStateAction<string[]>>;
}) {
  return (
    <section className="screen">
      <WorkspaceHeader
        title="최근검색 목록 정리"
        description="검색 화면에서는 최근검색을 고르는 데만 쓰고, 삭제는 여기서만 처리합니다."
      />
      <div className="history-manager-grid">
        <HistoryManagerSection
          title="유저 최근검색"
          items={riotIdHistory}
          emptyText="저장된 유저 최근검색이 없습니다."
          onRemove={(value) =>
            removeStoredHistoryItem(value, setRiotIdHistory, saveRiotIdHistory)
          }
        />
        <HistoryManagerSection
          title="기준 챔피언 최근검색"
          items={myChampionHistory}
          fixedItems={["전체"]}
          emptyText="저장된 기준 챔피언 최근검색이 없습니다."
          onRemove={(value) =>
            removeStoredHistoryItem(
              value,
              setMyChampionHistory,
              saveMyChampionHistory,
            )
          }
        />
        <HistoryManagerSection
          title="상대 챔피언 최근검색"
          items={enemyChampionHistory}
          fixedItems={["전체"]}
          emptyText="저장된 상대 챔피언 최근검색이 없습니다."
          onRemove={(value) =>
            removeStoredHistoryItem(
              value,
              setEnemyChampionHistory,
              saveEnemyChampionHistory,
            )
          }
        />
      </div>
    </section>
  );
}

function HistoryManagerSection({
  title,
  items,
  fixedItems = [],
  emptyText,
  onRemove,
}: {
  title: string;
  items: string[];
  fixedItems?: string[];
  emptyText: string;
  onRemove: (item: string) => void;
}) {
  const visibleItems = [
    ...fixedItems,
    ...items.filter((item) => !fixedItems.includes(item)),
  ];

  return (
    <section className="history-manager-section">
      <div className="history-manager-title">
        <History size={15} />
        <h2>{title}</h2>
      </div>
      {visibleItems.length ? (
        <div className="history-manager-list">
          {visibleItems.map((item) => (
            <div className="history-manager-row" key={item}>
              <span title={item}>{item}</span>
              {!fixedItems.includes(item) && (
                <button
                  className="icon-button danger"
                  type="button"
                  onClick={() => onRemove(item)}
                  title={`${item} 삭제`}
                  aria-label={`${item} 기록 삭제`}
                >
                  <Trash2 size={16} />
                </button>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div className="empty compact">{emptyText}</div>
      )}
    </section>
  );
}

function RecentRiotIdSearches({
  history,
  onPick,
  onRemove,
}: {
  history: string[];
  onPick: (riotId: string) => void;
  onRemove?: (riotId: string) => void;
}) {
  if (history.length === 0) {
    return null;
  }
  return (
    <div className="recent-searches" aria-label="최근검색">
      <span>최근검색</span>
      <div>
        {history.map((item) => (
          <RecentHistoryChip
            key={item}
            item={item}
            onPick={onPick}
            onRemove={onRemove}
          />
        ))}
      </div>
    </div>
  );
}

function RecentEnemyChampionSearches({
  history,
  onPick,
  onRemove,
}: {
  history: string[];
  onPick: (champion: string) => void;
  onRemove?: (champion: string) => void;
}) {
  const items = ["전체", ...history.filter((item) => item !== "전체")];
  return (
    <div className="recent-searches" aria-label="최근 상대 챔피언">
      <span>최근 상대 챔피언</span>
      <div>
        {items.map((item) => (
          <RecentHistoryChip
            key={item}
            item={item}
            onPick={onPick}
            onRemove={item === "전체" ? undefined : onRemove}
          />
        ))}
      </div>
    </div>
  );
}

function RecentMyChampionSearches({
  history,
  onPick,
  onRemove,
}: {
  history: string[];
  onPick: (champion: string) => void;
  onRemove?: (champion: string) => void;
}) {
  const items = ["전체", ...history.filter((item) => item !== "전체")];
  return (
    <div className="recent-searches" aria-label="최근 기준 챔피언">
      <span>최근 기준 챔피언</span>
      <div>
        {items.map((item) => (
          <RecentHistoryChip
            key={item}
            item={item}
            onPick={onPick}
            onRemove={item === "전체" ? undefined : onRemove}
          />
        ))}
      </div>
    </div>
  );
}

function RecentHistoryChip({
  item,
  onPick,
  onRemove,
}: {
  item: string;
  onPick: (item: string) => void;
  onRemove?: (item: string) => void;
}) {
  return (
    <span className="recent-chip-wrap">
      <button
        className="recent-chip"
        type="button"
        onClick={() => onPick(item)}
        title={item}
      >
        {item}
      </button>
      {onRemove && (
        <button
          className="recent-remove"
          type="button"
          onClick={() => onRemove(item)}
          title={`${item} 삭제`}
          aria-label={`${item} 기록 삭제`}
        >
          ×
        </button>
      )}
    </span>
  );
}

function ChampionSelect({
  label,
  value,
  meta,
  onChange,
}: {
  label: string;
  value: string;
  meta: MetaOptions;
  onChange: (value: string) => void;
}) {
  return (
    <SelectField
      label={label}
      value={value}
      options={[
        meta.allLabel,
        ...meta.champions.map((champion) => champion.koreanName),
      ]}
      onChange={onChange}
    />
  );
}

function SelectField({
  label,
  value,
  options,
  format,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  format?: (value: string) => string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option} value={option}>
            {format ? format(option) : option}
          </option>
        ))}
      </select>
    </label>
  );
}

function DateField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="date"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function JobProgress({
  job,
  error,
}: {
  job: JobStatus | null;
  error: string | null;
}) {
  if (error) {
    return <div className="error-box">{error}</div>;
  }
  if (
    !job ||
    job.status === "succeeded" ||
    job.status === "partial" ||
    job.status === "failed"
  ) {
    return null;
  }
  return (
    <div className="progress-box">
      <div className="progress-head">
        <strong>{job.message || "작업 준비 중..."}</strong>
        <span>{Math.round(job.progress * 100)}%</span>
      </div>
      <div
        className="progress-track"
        role="progressbar"
        aria-label="현재 작업 진행률"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(job.progress * 100)}
      >
        <div style={{ width: `${Math.round(job.progress * 100)}%` }} />
      </div>
    </div>
  );
}

function SearchSummary({ payload }: { payload: SearchPayload }) {
  const wins = payload.results.filter((row) => row.win).length;
  const total = payload.results.length;
  const losses = total - wins;
  return (
    <SummaryGrid
      title={`${payload.account.game_name}#${payload.account.tag_line}`}
      meta={`${payload.lane_label} · ${payload.period_kind}`}
      items={[
        ["조건에 맞는 경기", `${total}`, "경기"],
        [
          "승률",
          total ? `${((wins / total) * 100).toFixed(1)}%` : "-",
          `${wins}승 ${losses}패`,
        ],
        ["확인한 경기", `${payload.scanned_total}`, "경기"],
        [
          "저장본 사용 / 외부 요청",
          `${payload.cache_hits} / ${payload.api_calls}`,
          payload.index_allowed
            ? `새로 저장한 조회 항목 ${payload.indexed_rows}개`
            : `저장 대상 아님 · ${payload.index_tier || "랭크 없음"}`,
        ],
      ]}
    />
  );
}

function MultiSummary({ payload }: { payload: MultiSearchPayload }) {
  return (
    <>
      <SummaryGrid
        title={`${payload.resolved_users.length}명 확인 · ${payload.period_label}`}
        meta={`${payload.input_count}개 입력 · 1인당 최대 ${payload.matches_per_player}판`}
        items={[
          ["새로 저장한 조회 항목", `${payload.indexed_rows}`, "개"],
          [
            "완료 / 확인 필요",
            `${payload.success_count} / ${payload.failure_count}`,
            "유저",
          ],
          [
            "찾은 경기 / 외부 요청",
            `${payload.discovered_matches} / ${payload.api_calls}`,
            `신규 상세 ${payload.new_match_details}`,
          ],
          ["저장본 사용", `${payload.cache_hits}`, "건"],
        ]}
      />
      {payload.failures.length > 0 && (
        <div className="failure-list">
          <h2>수집 실패 목록</h2>
          {payload.failures.map((failure) => (
            <p key={`${failure.riot_id_raw}-${failure.reason}`}>
              <strong>{failure.riot_id_raw}</strong>
              <span>{failure.reason}</span>
            </p>
          ))}
        </div>
      )}
    </>
  );
}

function DbSummary({ payload }: { payload: DbSearchPayload }) {
  return (
    <SummaryGrid
      title={`${payload.my_champion_key || "전체"} vs ${payload.enemy_champion_key || "전체"}`}
      meta={`${payload.lane_label} · ${payload.period_kind} · ${
        payload.patch_prefix || "전체 패치"
      }`}
      items={[
        ["조건에 맞는 경기", `${payload.stats.total}`, "경기"],
        [
          "승률",
          payload.stats.winRate === null ? "-" : `${payload.stats.winRate}%`,
          `${payload.stats.wins}승 ${payload.stats.losses}패`,
        ],
        ["대상", payload.target_label, `${payload.results.length} 표시 중`],
        [
          "로컬 유저 확인",
          `${payload.cache_hits}`,
          payload.api_calls === 0
            ? "외부 요청 없음"
            : `외부 요청 ${payload.api_calls}`,
        ],
      ]}
    />
  );
}

function SummaryGrid({
  title,
  meta,
  items,
}: {
  title: string;
  meta: string;
  items: Array<[string, string, string]>;
}) {
  return (
    <section className="summary">
      <div className="summary-main">
        <span>결과 요약</span>
        <strong>{title}</strong>
        <p>{meta}</p>
      </div>
      <div className="kpis">
        {items.map(([label, value, caption]) => (
          <div className="kpi" key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
            <em>{caption}</em>
          </div>
        ))}
      </div>
    </section>
  );
}

function MatchResultList({
  rows,
  meta,
  focusPuuid,
  showPlayerCopy,
  memos = {},
  clientPagination = true,
  emptyTitle = "조건에 맞는 경기를 찾지 못했습니다.",
  emptyDescription = "기간을 늘리거나 챔피언/라인 선택이 맞는지 확인해 주세요.",
}: {
  rows: MatchRow[];
  meta: MetaOptions;
  focusPuuid?: string;
  showPlayerCopy: boolean;
  memos?: Record<string, string>;
  clientPagination?: boolean;
  emptyTitle?: string;
  emptyDescription?: string;
}) {
  const [openIds, setOpenIds] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(1);
  const pageCount = clientPagination
    ? Math.max(Math.ceil(rows.length / PAGE_SIZE), 1)
    : 1;
  const visibleRows = useMemo(
    () =>
      clientPagination
        ? rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
        : rows,
    [clientPagination, page, rows],
  );

  useEffect(() => {
    setPage(1);
  }, [rows]);

  if (rows.length === 0) {
    return (
      <div className="empty">
        <strong>{emptyTitle}</strong>
        <p>{emptyDescription}</p>
      </div>
    );
  }

  const toggle = (id: string) => {
    setOpenIds((current) => {
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  return (
    <section className="result-list">
      <div className="list-head">
        <h2>결과 목록</h2>
        {clientPagination && (
          <div className="pager compact">
            <button
              type="button"
              aria-label="이전 페이지"
              disabled={page <= 1}
              onClick={() => setPage(page - 1)}
            >
              <ChevronLeft size={16} />
            </button>
            <span>
              {page} / {pageCount}
            </span>
            <button
              type="button"
              aria-label="다음 페이지"
              disabled={page >= pageCount}
              onClick={() => setPage(page + 1)}
            >
              <ChevronRight size={16} />
            </button>
          </div>
        )}
      </div>
      {visibleRows.map((row, index) => {
        const id = `${row.match_id || index}-${row.player_puuid || focusPuuid || index}`;
        const playerPuuid = row.player_puuid || focusPuuid;
        return (
          <article className="result-card" key={id}>
            <MatchResultCard
              row={row}
              meta={meta}
              showPlayerCopy={showPlayerCopy}
              focusPuuid={focusPuuid}
              memos={memos}
              detailOpen={openIds.has(id)}
              onToggle={() => toggle(id)}
            />
            {openIds.has(id) && row.match_id && playerPuuid && (
              <MatchDetailPanel
                matchId={row.match_id}
                playerPuuid={playerPuuid}
              />
            )}
          </article>
        );
      })}
    </section>
  );
}

function MatchResultCard({
  row,
  meta,
  showPlayerCopy,
  focusPuuid,
  memos,
  detailOpen,
  onToggle,
}: {
  row: MatchRow;
  meta: MetaOptions;
  showPlayerCopy: boolean;
  focusPuuid?: string;
  memos: Record<string, string>;
  detailOpen: boolean;
  onToggle: () => void;
}) {
  const enemy = splitRiotId(row.enemy_riot_id);
  const enemyName = row.enemy_game_name || enemy.name;
  const enemyTag = row.enemy_tag_line || enemy.tag;
  const player = splitRiotId(row.player_riot_id);
  const playerId = riotId(
    row.player_game_name || player.name,
    row.player_tag_line || player.tag,
  );
  const enemyId = riotId(enemyName, enemyTag, row.enemy_riot_id);
  const playerPuuid = row.player_puuid || focusPuuid;
  const playerMemo = playerPuuid ? memos[playerPuuid] : undefined;
  const enemyMemo = row.enemy_puuid ? memos[row.enemy_puuid] : undefined;
  const enemyLink = opggUrl(enemyName, enemyTag);
  const resultClass = row.win ? "win" : "loss";
  const matchDateLabel = relativeMatchDate(row);
  const matchDateTitle = exactMatchDate(row);
  const patchVersion = matchPatchVersion(row);

  return (
    <div className={`card-grid ${resultClass}`}>
      <div className="match-meta">
        <strong>{row.win ? "승리" : "패배"}</strong>
        <span title={matchDateTitle}>{matchDateLabel}</span>
        {patchVersion && (
          <span className="match-patch" title={row.game_version}>
            패치 {patchVersion}
          </span>
        )}
        <em>{secondsToMinutes(row.game_duration)}</em>
      </div>
      <div className="player-summary">
        {showPlayerCopy && (
          <span className="player-id">{withMemo(playerId, playerMemo)}</span>
        )}
        <div className="champ-line">
          <ChampionLoadout row={row} meta={meta} />
          <div>
            <strong>
              {row.kills} / <b>{row.deaths}</b> / {row.assists}
            </strong>
            <p>
              CS {row.cs} ·{" "}
              {Number(row.damage_to_champions || 0).toLocaleString()} 피해
            </p>
          </div>
        </div>
      </div>
      <div className="match-build">
        <div className="items">
          {(row.my_items || [])
            .slice(0, 7)
            .map((itemId, index) =>
              itemId ? (
                <img
                  key={`${itemId}-${index}`}
                  src={itemIcon(meta.staticDataVersion, itemId)}
                  alt=""
                />
              ) : (
                <span key={`empty-${index}`} />
              ),
            )}
        </div>
        <MatchQuickBuild row={row} />
      </div>
      <div className="enemy-summary">
        <img
          src={championIcon(meta.dataDragonVersion, row.enemy_champion_key)}
          alt=""
        />
        <div>
          <span>상대 라이너</span>
          <strong>{withMemo(enemyName || "-", enemyMemo)}</strong>
          <em>
            #{enemyTag || "-"} · {row.enemy_champion_key}
          </em>
        </div>
      </div>
      <div className="actions">
        {showPlayerCopy && (
          <CopyButton value={playerId} label="플레이어 Riot ID 복사" />
        )}
        <CopyButton value={enemyId} label="상대 Riot ID 복사" />
        <a
          className={`icon-button ${enemyLink ? "" : "disabled"}`}
          href={enemyLink || undefined}
          target="_blank"
          rel="noreferrer"
          title="OP.GG 열기"
          aria-label="OP.GG 열기"
        >
          <ExternalLink size={16} />
        </a>
        <button
          className="icon-button"
          type="button"
          onClick={onToggle}
          title="매치 상세 보기 · 저장되지 않은 타임라인이나 랭크 정보는 처음 한 번 새로 가져올 수 있어요."
          aria-label="매치 상세 보기"
        >
          {detailOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>
      </div>
    </div>
  );
}

function MatchQuickBuild({ row }: { row: MatchRow }) {
  const primaryRuneIcons = (row.myPrimaryRuneIconUrls || []).filter(Boolean);
  const secondaryRuneIcons = (row.mySecondaryRuneIconUrls || []).filter(
    Boolean,
  );
  const statShardIcons = (row.myStatShardIconUrls || []).map((url, index) => ({
    url,
    title: row.myStatShardNames?.[index] || "능력치 파편",
  }));

  return (
    <div className="quick-build">
      <div className="quick-runes">
        <div className="quick-rune-pages">
          <div className="quick-rune-group">
            {primaryRuneIcons.map((url, index) => (
              <img
                key={`${url}-${index}`}
                src={url || ""}
                alt=""
                title="주 룬"
              />
            ))}
          </div>
          <div className="quick-rune-group">
            {secondaryRuneIcons.map((url, index) => (
              <img
                key={`${url}-${index}`}
                src={url || ""}
                alt=""
                title="부 룬"
              />
            ))}
          </div>
        </div>
        <span className="quick-rune-divider" />
        <div className="quick-shard-row">
          {statShardIcons.map(({ url, title }, index) =>
            url ? (
              <img
                className="stat-shard"
                key={`${url}-${index}`}
                src={url}
                alt=""
                title={title}
              />
            ) : (
              <span className="stat-shard" key={`stat-empty-${index}`} />
            ),
          )}
        </div>
      </div>
    </div>
  );
}

function ChampionLoadout({ row, meta }: { row: MatchRow; meta: MetaOptions }) {
  const primaryRuneIcon = row.myPrimaryRuneIconUrls?.find(Boolean);
  const secondaryRuneIcon =
    row.mySecondaryTreeIconUrl || row.mySecondaryRuneIconUrls?.find(Boolean);
  const miniIcons = [
    row.mySummoner1IconUrl,
    row.mySummoner2IconUrl,
    primaryRuneIcon,
    secondaryRuneIcon,
  ];

  return (
    <div className="champion-loadout">
      <div className="champion">
        <img
          src={championIcon(meta.dataDragonVersion, row.my_champion_key)}
          alt=""
        />
        <span>Lv {row.my_champion_level || 0}</span>
      </div>
      <div className="mini-loadout">
        {miniIcons.map((url, index) =>
          url ? (
            <img key={`${url}-${index}`} src={url} alt="" />
          ) : (
            <span key={index} />
          ),
        )}
      </div>
    </div>
  );
}

function CopyButton({
  value,
  label,
  children,
  className = "icon-button",
}: {
  value: string;
  label: string;
  children?: ReactNode;
  className?: string;
}) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (timer.current !== null) {
        window.clearTimeout(timer.current);
      }
    },
    [],
  );
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setState("copied");
    } catch {
      setState("failed");
    }
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
    }
    timer.current = window.setTimeout(() => setState("idle"), 1_500);
  };
  const stateLabel =
    state === "copied" ? "복사됨" : state === "failed" ? "복사 실패" : null;
  return (
    <button
      className={className}
      type="button"
      onClick={copy}
      title={stateLabel || label}
      aria-label={stateLabel || label}
    >
      {stateLabel
        ? children
          ? stateLabel
          : state === "copied"
            ? "✓"
            : "!"
        : children || <Copy size={16} />}
    </button>
  );
}

function EnemyRiotIdsCopyButton({
  rows,
  labelPrefix,
}: {
  rows: MatchRow[];
  labelPrefix: string;
}) {
  const enemyIds = useMemo(() => {
    const seen = new Set<string>();
    const values: string[] = [];
    for (const row of rows) {
      const split = splitRiotId(row.enemy_riot_id);
      const value = riotId(
        row.enemy_game_name || split.name,
        row.enemy_tag_line || split.tag,
        row.enemy_riot_id,
      );
      const key = value.toLocaleLowerCase();
      if (value === "-" || seen.has(key)) {
        continue;
      }
      seen.add(key);
      values.push(value);
    }
    return values;
  }, [rows]);

  if (enemyIds.length === 0) {
    return null;
  }
  const label = `${labelPrefix} 상대 Riot ID ${enemyIds.length}명 복사`;
  return (
    <CopyButton
      value={enemyIds.join("\n")}
      label={label}
      className="tool-button"
    >
      <Copy size={16} />
      {label}
    </CopyButton>
  );
}

function MatchDetailPanel({
  matchId,
  playerPuuid,
}: {
  matchId: string;
  playerPuuid: string;
}) {
  const [detail, setDetail] = useState<MatchDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<DetailTab>("build");
  const [loadedDetailKey, setLoadedDetailKey] = useState<string | null>(null);
  const [loadingPart, setLoadingPart] = useState<DetailTab | null>(null);
  const [partErrors, setPartErrors] = useState<
    Record<DetailTab, string | null>
  >({ build: null, summary: null });
  const [partApiCalls, setPartApiCalls] = useState<Record<DetailTab, number>>({
    build: 0,
    summary: 0,
  });
  const loadedParts = useRef<Set<string>>(new Set());
  const detailKey = `${matchId}:${playerPuuid}`;

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError(null);
    setActiveTab("build");
    setLoadedDetailKey(null);
    setLoadingPart(null);
    setPartErrors({ build: null, summary: null });
    setPartApiCalls({ build: 0, summary: 0 });
    loadedParts.current.clear();
    api
      .getMatchDetail(matchId, playerPuuid)
      .then((value) => {
        if (!cancelled) {
          setDetail(value);
          setLoadedDetailKey(detailKey);
        }
      })
      .catch((exc) => {
        if (!cancelled) {
          setError(exc instanceof Error ? exc.message : "상세 조회 실패");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [matchId, playerPuuid]);

  useEffect(() => {
    if (!detail || loadedDetailKey !== detailKey) {
      return;
    }
    const partKey = `${detailKey}:${activeTab}`;
    if (loadedParts.current.has(partKey)) {
      return;
    }

    let cancelled = false;
    let completed = false;
    loadedParts.current.add(partKey);
    setLoadingPart(activeTab);
    setPartErrors((current) => ({ ...current, [activeTab]: null }));
    const request =
      activeTab === "build"
        ? api.getMatchBuildDetail(matchId, playerPuuid)
        : api.getMatchRankDetail(matchId, playerPuuid);
    request
      .then((value) => {
        if (cancelled) {
          return;
        }
        completed = true;
        setPartApiCalls((current) => ({
          ...current,
          [activeTab]: value.detailLoad.externalApiCalls,
        }));
        setDetail((current) => {
          if (!current) {
            return current;
          }
          if (activeTab === "build" && "buildTimeline" in value) {
            return {
              ...current,
              buildTimeline: value.buildTimeline,
              timelineError: value.timelineError,
              detailLoad: {
                ...current.detailLoad,
                timelineSource: value.detailLoad.timelineSource,
                externalApiCalls:
                  current.detailLoad.externalApiCalls +
                  value.detailLoad.externalApiCalls,
              },
            };
          }
          if (activeTab === "summary" && "me" in value) {
            return {
              ...current,
              me: value.me,
              enemyLaner: value.enemyLaner,
              othersAlly: value.othersAlly,
              othersEnemy: value.othersEnemy,
              detailLoad: {
                ...current.detailLoad,
                rankCacheHits: value.detailLoad.rankCacheHits,
                rankError: value.detailLoad.rankError,
                externalApiCalls:
                  current.detailLoad.externalApiCalls +
                  value.detailLoad.externalApiCalls,
              },
            };
          }
          return current;
        });
      })
      .catch((exc) => {
        if (!cancelled) {
          loadedParts.current.delete(partKey);
          setPartErrors((current) => ({
            ...current,
            [activeTab]:
              exc instanceof Error ? exc.message : "상세 정보 확인 실패",
          }));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingPart((current) => (current === activeTab ? null : current));
        }
      });
    return () => {
      cancelled = true;
      if (!completed) {
        loadedParts.current.delete(partKey);
      }
    };
  }, [activeTab, detailKey, loadedDetailKey]);

  if (error) {
    return <div className="detail-panel error-box">{error}</div>;
  }
  if (!detail) {
    return (
      <div className="detail-panel loading">
        내 컴퓨터에 저장된 기본 경기 정보를 불러오는 중이에요.
      </div>
    );
  }
  const activePartError = partErrors[activeTab];
  const sourceMessage =
    loadingPart === activeTab
      ? activeTab === "build"
        ? "빌드에 필요한 타임라인을 확인하고 있어요."
        : "종합 표에 필요한 참가자 랭크를 확인하고 있어요."
      : partApiCalls[activeTab] > 0
        ? `이 탭의 새 정보 요청 ${partApiCalls[activeTab]}번을 마치고 내 컴퓨터에 저장했어요.`
        : "필요한 상세 정보는 내 컴퓨터에 저장된 자료를 사용했어요.";
  return (
    <div className="detail-panel">
      <div className="detail-head">
        <div className="detail-tabs" role="tablist" aria-label="매치 상세 탭">
          <button
            className={activeTab === "summary" ? "active" : ""}
            onClick={() => setActiveTab("summary")}
            type="button"
            role="tab"
            aria-selected={activeTab === "summary"}
          >
            종합
          </button>
          <button
            className={activeTab === "build" ? "active" : ""}
            onClick={() => setActiveTab("build")}
            type="button"
            role="tab"
            aria-selected={activeTab === "build"}
          >
            빌드
          </button>
        </div>
        <p className="detail-load-source" role="status">
          {sourceMessage}
        </p>
      </div>
      {(activePartError ||
        (activeTab === "summary" && detail.detailLoad.rankError)) && (
        <div className="error-box">
          {activePartError || detail.detailLoad.rankError}
        </div>
      )}
      {loadingPart === activeTab ? (
        <p className="timeline-notice">필요한 정보만 확인하고 있어요.</p>
      ) : activeTab === "summary" ? (
        <MatchSummaryTab detail={detail} />
      ) : (
        <BuildTab detail={detail} />
      )}
    </div>
  );
}

function MatchSummaryTab({ detail }: { detail: MatchDetail }) {
  const allyPlayers = sortTeam([detail.me, ...(detail.othersAlly || [])]);
  const enemyPlayers = sortTeam(
    [detail.enemyLaner, ...(detail.othersEnemy || [])].filter(
      Boolean,
    ) as DetailPlayer[],
  );
  const allPlayers = [...allyPlayers, ...enemyPlayers];
  const maxDamage = Math.max(
    ...allPlayers.map((player) => player.damage || 0),
    1,
  );
  const allyTotals = teamTotals(allyPlayers);
  const enemyTotals = teamTotals(enemyPlayers);

  return (
    <div className="match-summary-tab">
      <div className="team-tables">
        <TeamTable
          title={detail.me.win ? "승리 (블루팀)" : "패배 (블루팀)"}
          players={allyPlayers}
          maxDamage={maxDamage}
        />
        <TeamCompare ally={allyTotals} enemy={enemyTotals} />
        <TeamTable
          title={detail.enemyLaner?.win ? "승리 (레드팀)" : "패배 (레드팀)"}
          players={enemyPlayers}
          maxDamage={maxDamage}
        />
      </div>
    </div>
  );
}

function sortTeam(players: DetailPlayer[]): DetailPlayer[] {
  return players
    .map((player, index) => ({ player, index }))
    .sort((left, right) => {
      const leftOrder = LANE_ORDER[left.player.team_position || ""] ?? 99;
      const rightOrder = LANE_ORDER[right.player.team_position || ""] ?? 99;
      return leftOrder - rightOrder || left.index - right.index;
    })
    .map(({ player }) => player);
}

function teamTotals(players: DetailPlayer[]) {
  return {
    kills: players.reduce((total, player) => total + (player.kills || 0), 0),
    gold: players.reduce((total, player) => total + (player.gold || 0), 0),
  };
}

function participantName(player: DetailPlayer): string {
  const name = player.riot_id_game_name || player.summoner_name || "-";
  return player.riot_id_tag_line ? `${name}#${player.riot_id_tag_line}` : name;
}

function kdaRatio(player: DetailPlayer): string {
  if (!player.deaths) {
    return "Perfect";
  }
  return ((player.kills + player.assists) / player.deaths).toFixed(2);
}

function TeamTable({
  title,
  players,
  maxDamage,
}: {
  title: string;
  players: DetailPlayer[];
  maxDamage: number;
}) {
  const resultClass = players[0]?.win ? "win" : "loss";
  return (
    <section className={`team-table ${resultClass}`}>
      <div className="team-table-title">{title}</div>
      <div className="team-table-head">
        <span>챔피언</span>
        <span>플레이어</span>
        <span>KDA</span>
        <span>피해량</span>
        <span>CS</span>
        <span>골드</span>
        <span>아이템</span>
      </div>
      {players.map((player) => (
        <div
          className="team-table-row"
          key={player.puuid || participantName(player)}
        >
          <div className="team-champion">
            <div className="level-champion">
              {player.championIconUrl && (
                <img src={player.championIconUrl} alt="" />
              )}
              <span>{player.champion_level || 0}</span>
            </div>
            <div className="summoner-spells">
              {player.summoner1IconUrl && (
                <img src={player.summoner1IconUrl} alt="" />
              )}
              {player.summoner2IconUrl && (
                <img src={player.summoner2IconUrl} alt="" />
              )}
            </div>
            <DetailRuneLoadout player={player} />
          </div>
          <div className="team-player-name">
            <strong>{participantName(player)}</strong>
            <span>{player.rankLabel || player.championNameKo}</span>
          </div>
          <div className="team-kda">
            <strong>
              {player.kills}/{player.deaths}/{player.assists}
            </strong>
            <span>{kdaRatio(player)}:1</span>
          </div>
          <div className="team-damage">
            <span>{Number(player.damage || 0).toLocaleString()}</span>
            <div className="damage-bar">
              <div
                style={{
                  width: `${((player.damage || 0) / maxDamage) * 100}%`,
                }}
              />
            </div>
          </div>
          <div className="team-small-stat">{player.cs || 0}</div>
          <div className="team-small-stat">
            {Number(player.gold || 0).toLocaleString()}
          </div>
          <ItemStrip urls={player.itemIconUrls || []} />
        </div>
      ))}
    </section>
  );
}

function DetailRuneLoadout({ player }: { player: DetailPlayer }) {
  const runeIcons = [
    {
      url: player.primaryRuneIconUrls?.find(Boolean),
      title: player.primaryRuneNames?.find(Boolean) || player.primaryTreeName,
    },
    {
      url:
        player.secondaryTreeIconUrl ||
        player.secondaryRuneIconUrls?.find(Boolean),
      title:
        player.secondaryTreeName || player.secondaryRuneNames?.find(Boolean),
    },
  ];

  return (
    <div className="detail-runes">
      {runeIcons.map((rune, index) =>
        rune.url ? (
          <img
            key={`${rune.url}-${index}`}
            src={rune.url}
            alt=""
            title={rune.title}
          />
        ) : (
          <span key={`empty-rune-${index}`} />
        ),
      )}
    </div>
  );
}

function ItemStrip({ urls = [] }: { urls?: Array<string | null> }) {
  return (
    <div className="item-strip">
      {[...urls, null, null, null, null, null, null, null]
        .slice(0, 7)
        .map((url, index) =>
          url ? (
            <img key={`${url}-${index}`} src={url} alt="" />
          ) : (
            <span key={`empty-${index}`} />
          ),
        )}
    </div>
  );
}

function TeamCompare({
  ally,
  enemy,
}: {
  ally: { kills: number; gold: number };
  enemy: { kills: number; gold: number };
}) {
  return (
    <div className="team-compare">
      <CompareBar label="총 킬" left={ally.kills} right={enemy.kills} />
      <CompareBar label="총 골드" left={ally.gold} right={enemy.gold} />
    </div>
  );
}

function CompareBar({
  label,
  left,
  right,
}: {
  label: string;
  left: number;
  right: number;
}) {
  const total = Math.max(left + right, 1);
  return (
    <div className="compare-row">
      <span>{Number(left).toLocaleString()}</span>
      <div className="compare-track">
        <div
          className="compare-left"
          style={{ width: `${(left / total) * 100}%` }}
        />
        <strong>{label}</strong>
        <div
          className="compare-right"
          style={{ width: `${(right / total) * 100}%` }}
        />
      </div>
      <span>{Number(right).toLocaleString()}</span>
    </div>
  );
}

function BuildTab({ detail }: { detail: MatchDetail }) {
  const buildTimeline = detail.buildTimeline;
  const me = detail.me;
  const itemGroups = groupItemEvents(buildTimeline?.item_events || []);
  return (
    <div className="build-tab">
      <section className="build-section">
        <h3>아이템 빌드</h3>
        {itemGroups.length ? (
          <div className="build-sequence item-build-sequence">
            {itemGroups.map((group, index) => (
              <div
                className="item-build-group"
                key={`${group.minute}-${index}`}
              >
                {index > 0 && <span className="build-arrow">›</span>}
                <div className="item-build-stack">
                  <div className="item-build-icons">
                    {group.events.map((event, eventIndex) => (
                      <span
                        key={`${event.item_id}-${event.timestamp}-${eventIndex}`}
                      >
                        {event.icon_url ? (
                          <img src={event.icon_url} alt="" />
                        ) : (
                          event.item_id
                        )}
                      </span>
                    ))}
                  </div>
                  <em>{group.minute}분</em>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <TimelineNotice error={detail.timelineError} />
        )}
      </section>

      <section className="build-section">
        <h3>스킬 빌드</h3>
        {buildTimeline?.skill_events.length ? (
          <>
            <div className="skill-icons">
              {uniqueSkillEvents(detail).map((event) => (
                <div
                  className="skill-icon-card"
                  key={event.skillSlot || event.skill_slot || event.label}
                >
                  {event.iconUrl && (
                    <img src={event.iconUrl} alt={event.spellName} />
                  )}
                  <strong>{event.label}</strong>
                </div>
              ))}
            </div>
            <div className="skill-order">
              {buildTimeline.skill_events.map((event) => (
                <span
                  className={event.label === "R" ? "ultimate" : ""}
                  key={`${event.level}-${event.timestamp}`}
                >
                  {event.label}
                </span>
              ))}
            </div>
          </>
        ) : (
          <TimelineNotice error={detail.timelineError} />
        )}
      </section>

      <section className="build-section">
        <h3>룬</h3>
        <div className="rune-pages">
          <RunePageBlock
            page={me.primaryRunePage}
            fallbackIconUrls={me.primaryRuneIconUrls || []}
            fallbackTitle={me.primaryTreeName || "주 룬"}
          />
          <RunePageBlock
            page={me.secondaryRunePage}
            compact
            fallbackIconUrls={me.secondaryRuneIconUrls || []}
            fallbackTitle={me.secondaryTreeName || "보조 룬"}
          />
          <StatShardBlock
            rows={me.statShardPage || []}
            fallbackIconUrls={me.statShardIconUrls || []}
          />
        </div>
      </section>
    </div>
  );
}

function groupItemEvents(
  events: NonNullable<MatchDetail["buildTimeline"]>["item_events"],
) {
  const groups: Array<{
    minute: number;
    events: NonNullable<MatchDetail["buildTimeline"]>["item_events"];
  }> = [];
  for (const event of events) {
    const last = groups[groups.length - 1];
    if (last && last.minute === event.minute) {
      last.events.push(event);
    } else {
      groups.push({ minute: event.minute, events: [event] });
    }
  }
  return groups;
}

function TimelineNotice({ error }: { error: string | null }) {
  return (
    <p className="timeline-notice">
      {error || "빌드 타임라인을 불러오지 못했습니다."}
    </p>
  );
}

function uniqueSkillEvents(detail: MatchDetail) {
  const bySlot = new Map<
    number,
    NonNullable<MatchDetail["buildTimeline"]>["skill_events"][number]
  >();
  for (const event of detail.buildTimeline?.skill_events || []) {
    const slot = event.skillSlot || event.skill_slot || 0;
    if (slot && !bySlot.has(slot)) {
      bySlot.set(slot, event);
    }
  }
  return [...bySlot.entries()]
    .sort(([left], [right]) => left - right)
    .map(([, event]) => event);
}

function RunePageBlock({
  page,
  compact = false,
  fallbackIconUrls = [],
  fallbackTitle,
}: {
  page?: RunePage | null;
  compact?: boolean;
  fallbackIconUrls?: Array<string | null>;
  fallbackTitle: string;
}) {
  if (!page || page.slots.length === 0) {
    return (
      <div className={`rune-page ${compact ? "compact" : ""}`}>
        <div className="rune-page-title">
          <span>{fallbackTitle}</span>
        </div>
        <div className="rune-slot selected-only">
          {fallbackIconUrls.filter(Boolean).map((url, index) => (
            <span className="rune-icon selected" key={`${url}-${index}`}>
              <img src={url || ""} alt="" />
            </span>
          ))}
        </div>
      </div>
    );
  }
  return (
    <div className={`rune-page ${compact ? "compact" : ""}`}>
      <div className="rune-page-title">
        {page.treeIconUrl && <img src={page.treeIconUrl} alt="" />}
        <span>{page.treeName}</span>
      </div>
      {page.slots.map((slot, index) => (
        <div className="rune-slot" key={`${page.treeId}-${index}`}>
          {slot.map((rune) => (
            <RuneIcon rune={rune} key={rune.id} />
          ))}
        </div>
      ))}
    </div>
  );
}

function StatShardBlock({
  rows,
  fallbackIconUrls = [],
}: {
  rows: RuneEntry[][];
  fallbackIconUrls?: Array<string | null>;
}) {
  return (
    <div className="rune-page">
      <div className="rune-page-title">
        <span>능력치 파편</span>
      </div>
      {rows.length
        ? rows.map((row, index) => (
            <div className="rune-slot" key={`stat-${index}`}>
              {row.map((rune) => (
                <RuneIcon rune={rune} key={`${index}-${rune.id}`} />
              ))}
            </div>
          ))
        : fallbackIconUrls.filter(Boolean).map((url, index) => (
            <div
              className="rune-slot selected-only"
              key={`stat-fallback-${index}`}
            >
              <span className="rune-icon selected">
                <img src={url || ""} alt="" />
              </span>
            </div>
          ))}
    </div>
  );
}

function RuneIcon({ rune }: { rune: RuneEntry }) {
  return (
    <span
      className={`rune-icon ${rune.selected ? "selected" : ""}`}
      title={rune.name}
    >
      {rune.iconUrl && <img src={rune.iconUrl} alt="" />}
    </span>
  );
}
