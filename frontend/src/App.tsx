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
  Server,
  StickyNote,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import type { Dispatch, ReactNode, SetStateAction } from "react";
import { api, searchCsvUrl } from "./api";
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
  return new Date().toISOString().slice(0, 10);
}

function daysAgoText(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString().slice(0, 10);
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

function autoRefreshSummary(meta: MetaOptions): string {
  const refresh = meta.autoRefresh;
  if (refresh.status === "running" && refresh.targetCount) {
    return `자동갱신 중 · ${refresh.targetCount}명`;
  }
  return refresh.message;
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
  return compacted.sort((a, b) =>
    a.localeCompare(b, "ko-KR", { sensitivity: "base" }),
  );
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
  return compacted.sort((a, b) => a.localeCompare(b, "ko-KR"));
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

function useJobPoller(
  jobId: string | null,
  onDone: (status: JobStatus) => Promise<void>,
) {
  const [job, setJob] = useState<JobStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!jobId) {
      return;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const status = await api.getJob(jobId);
        if (cancelled) {
          return;
        }
        setJob(status);
        if (status.status === "succeeded") {
          await onDone(status);
          return;
        }
        if (status.status === "failed") {
          setError(status.error || "작업에 실패했습니다.");
          return;
        }
        window.setTimeout(poll, 900);
      } catch (exc) {
        if (!cancelled) {
          setError(
            exc instanceof Error ? exc.message : "job 조회에 실패했습니다.",
          );
        }
      }
    };
    poll();
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  return { job, error, setError };
}

function usePlayerMemoItems(): PlayerMemo[] {
  const [items, setItems] = useState<PlayerMemo[]>([]);

  useEffect(() => {
    let cancelled = false;
    api
      .listPlayerMemos()
      .then((payload) => {
        if (!cancelled) {
          setItems(payload.items);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setItems([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return items;
}

export default function App() {
  const [meta, setMeta] = useState<MetaOptions | null>(null);
  const [active, setActive] = useState<Workspace>("search");
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

  useEffect(() => {
    api
      .getMeta()
      .then(setMeta)
      .catch((exc) =>
        setError(exc instanceof Error ? exc.message : "메타데이터 로딩 실패"),
      );
  }, []);

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

  const autoRefreshJobId =
    meta?.autoRefresh.status === "running" ? meta.autoRefresh.jobId : null;
  const autoRefreshPoller = useJobPoller(autoRefreshJobId, async () => {
    setMeta(await api.getMeta());
  });

  const refreshMeta = async () => {
    setMetaRefreshing(true);
    setMetaRefreshError(null);
    try {
      setMeta(await api.refreshMeta());
    } catch (exc) {
      setMetaRefreshError(
        exc instanceof Error ? exc.message : "Data Dragon 갱신 실패",
      );
    } finally {
      setMetaRefreshing(false);
    }
  };

  if (error) {
    return <FatalError message={error} />;
  }

  if (!meta) {
    return <div className="boot">메타데이터를 불러오는 중...</div>;
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span>LoL</span>
          <strong>Matchup Finder</strong>
        </div>
        <nav className="nav-list" aria-label="작업공간">
          <NavButton
            active={active === "search"}
            icon={<Search size={18} />}
            label="개별유저검색"
            onClick={() => setActive("search")}
          />
          <NavButton
            active={active === "multi"}
            icon={<UploadCloud size={18} />}
            label="멀티서치"
            onClick={() => setActive("multi")}
          />
          <NavButton
            active={active === "db"}
            icon={<Database size={18} />}
            label="DB조회"
            onClick={() => setActive("db")}
          />
          <NavButton
            active={active === "memo"}
            icon={<StickyNote size={18} />}
            label="유저 메모"
            onClick={() => setActive("memo")}
          />
          <NavButton
            active={active === "history"}
            icon={<History size={18} />}
            label="최근검색 관리"
            onClick={() => setActive("history")}
          />
        </nav>
        <div className="server-card">
          <div className="server-title">
            <Server size={16} />
            서버 상태
          </div>
          <span className={meta.apiKeyDetected ? "status ok" : "status danger"}>
            {meta.apiKeyDetected ? "RIOT_API_KEY 감지됨" : "RIOT_API_KEY 없음"}
          </span>
          <p>
            {meta.region} · {meta.platform} · queue {meta.queueId}
          </p>
          <div className="server-db-stats">
            <p>
              <span>DB 매치</span>
              <strong>{meta.dbMatchCount.toLocaleString("ko-KR")}개</strong>
            </p>
            <p>
              <span>마지막 매치</span>
              <strong>{dbLastMatchText(meta.dbLastMatchCreation)}</strong>
            </p>
          </div>
          <div className={`server-auto-refresh ${meta.autoRefresh.status}`}>
            <p>
              <span>자동갱신</span>
              <strong>{autoRefreshSummary(meta)}</strong>
            </p>
            {meta.autoRefresh.days !== null &&
              meta.autoRefresh.matchesPerPlayer !== null && (
                <em>
                  최근 {meta.autoRefresh.days}일 · 1인당{" "}
                  {meta.autoRefresh.matchesPerPlayer}판
                </em>
              )}
            {autoRefreshPoller.job && meta.autoRefresh.status === "running" && (
              <em>
                {Math.round(autoRefreshPoller.job.progress * 100)}% ·{" "}
                {autoRefreshPoller.job.message || "진행 중"}
              </em>
            )}
            {autoRefreshPoller.error && (
              <em className="server-error-text">{autoRefreshPoller.error}</em>
            )}
          </div>
          <div className="data-dragon-row">
            <p>Data Dragon {meta.dataDragonVersion}</p>
            <button
              className="icon-button compact"
              type="button"
              onClick={refreshMeta}
              disabled={metaRefreshing}
              title="Data Dragon 갱신"
              aria-label="Data Dragon 갱신"
            >
              <RefreshCw className={metaRefreshing ? "spin" : ""} size={14} />
            </button>
          </div>
          {metaRefreshError && (
            <p className="server-error-text">{metaRefreshError}</p>
          )}
        </div>
      </aside>

      <main className="workspace">
        {active === "search" && (
          <SearchWorkspace
            meta={meta}
            riotIdHistory={riotIdHistory}
            setRiotIdHistory={setRiotIdHistory}
            enemyChampionHistory={enemyChampionHistory}
            setEnemyChampionHistory={setEnemyChampionHistory}
            myChampionHistory={myChampionHistory}
            setMyChampionHistory={setMyChampionHistory}
          />
        )}
        {active === "multi" && (
          <MultiCollectWorkspace
            meta={meta}
            riotIdHistory={riotIdHistory}
            setRiotIdHistory={setRiotIdHistory}
          />
        )}
        {active === "db" && (
          <DbLookupWorkspace
            meta={meta}
            riotIdHistory={riotIdHistory}
            setRiotIdHistory={setRiotIdHistory}
            enemyChampionHistory={enemyChampionHistory}
            setEnemyChampionHistory={setEnemyChampionHistory}
            myChampionHistory={myChampionHistory}
            setMyChampionHistory={setMyChampionHistory}
          />
        )}
        {active === "memo" && <PlayerMemoWorkspace />}
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

function FatalError({ message }: { message: string }) {
  return (
    <div className="fatal">
      <strong>앱을 시작할 수 없습니다.</strong>
      <p>{message}</p>
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
      onClick={onClick}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}

function SearchWorkspace({
  meta,
  riotIdHistory,
  setRiotIdHistory,
  enemyChampionHistory,
  setEnemyChampionHistory,
  myChampionHistory,
  setMyChampionHistory,
}: {
  meta: MetaOptions;
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
  const [payload, setPayload] = useState<SearchPayload | null>(null);
  const memoItems = usePlayerMemoItems();

  const poller = useJobPoller(jobId, async (status) => {
    const result = await api.getJobResult(status.jobId);
    setPayload(result.payload as SearchPayload);
  });

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
    setPayload(null);
    poller.setError(null);
    const nextBody = { ...body, riotIdRaw: rememberHistory(body.riotIdRaw) };
    rememberMyChampionHistory(nextBody.myChampionKorean);
    rememberEnemyChampionHistory(nextBody.enemyChampionKorean);
    setBody(nextBody);
    const created = await api.createSearchJob(nextBody);
    setJobId(created.jobId);
  };

  return (
    <section className="screen">
      <WorkspaceHeader
        eyebrow="개별유저검색"
        title="Riot ID 기준 매치업 검색"
        description="최근 솔로 랭크에서 내가 고른 챔피언과 상대 라이너 조건이 맞는 경기만 찾습니다."
      />
      <form
        className={`control-panel search-panel ${
          body.periodKind === meta.customPeriodLabel ? "custom-period" : ""
        }`}
        onSubmit={submit}
      >
        <div className="riot-id-field">
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
          <MemoPlayerSelect items={memoItems} onPick={applyMemoItem} />
        </div>
        <div className="my-champion-field">
          <ChampionSelect
            label="내 챔피언"
            value={body.myChampionKorean}
            meta={meta}
            onChange={(value) => setBody({ ...body, myChampionKorean: value })}
          />
          <RecentMyChampionSearches
            history={myChampionHistory}
            onPick={(value) => setBody({ ...body, myChampionKorean: value })}
          />
        </div>
        <div className="enemy-filter-field">
          <div className="enemy-filter-controls">
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
          </div>
          <RecentEnemyChampionSearches
            history={enemyChampionHistory}
            onPick={(value) => setBody({ ...body, enemyChampionKorean: value })}
          />
        </div>
        <SelectField
          label="최대 매치"
          value={String(body.maxMatches)}
          options={["50", "100", "200", "300"]}
          onChange={(value) => setBody({ ...body, maxMatches: Number(value) })}
        />
        {body.periodKind === meta.customPeriodLabel && (
          <div className="search-custom-period-fields">
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
        <button className="primary-action search-submit-action" type="submit">
          <Search size={18} />
          검색 실행
        </button>
        <RecentRiotIdSearches
          className="search-riot-history"
          history={riotIdHistory}
          onPick={applyHistoryItem}
        />
      </form>

      <JobProgress job={poller.job} error={poller.error} />
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
            onSaved={(puuid, memo) =>
              setPayload({
                ...payload,
                memos: { ...payload.memos, [puuid]: memo },
              })
            }
          />
          <div className="toolbar">
            <a className="tool-button" href={searchCsvUrl(jobId || "")}>
              <Download size={16} />
              CSV 다운로드
            </a>
          </div>
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
  riotIdHistory,
  setRiotIdHistory,
}: {
  meta: MetaOptions;
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
  const memoItems = usePlayerMemoItems();
  const memoGroups = useMemo(() => groupPlayerMemos(memoItems), [memoItems]);

  const poller = useJobPoller(jobId, async (status) => {
    const result = await api.getJobResult(status.jobId);
    setPayload(result.payload as MultiSearchPayload);
  });

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
    setPayload(null);
    poller.setError(null);
    const nextBody = { ...body, riotIdsRaw: rememberHistory(body.riotIdsRaw) };
    setBody(nextBody);
    const created = await api.createMultiSearchJob(nextBody);
    setJobId(created.jobId);
  };

  return (
    <section className="screen">
      <WorkspaceHeader
        eyebrow="멀티서치"
        title="여러 Riot ID 매치 수집"
        description="직접 입력한 유저들의 솔로 랭크 match_cache를 저장하고 matchup_index에 반영합니다."
      />
      <form className="control-panel multi" onSubmit={submit}>
        <div className="multi-riot-id-field">
          <label className="field textarea-field">
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
        </div>
        <MemoGroupSelect groups={memoGroups} onPick={applyMemoGroup} />
        <div className="multi-options">
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
        <button className="primary-action multi-submit-action" type="submit">
          <UploadCloud size={18} />
          수집 실행
        </button>
        <RecentRiotIdSearches
          className="multi-riot-history"
          history={riotIdHistory}
          onPick={applyHistoryItem}
        />
      </form>
      <JobProgress job={poller.job} error={poller.error} />
      {payload && (
        <>
          <MultiSummary payload={payload} />
          <MissingMemoPanel
            candidates={payload.resolved_users}
            memos={payload.memos}
            onSaved={(puuid, memo) =>
              setPayload({
                ...payload,
                memos: { ...payload.memos, [puuid]: memo },
              })
            }
          />
        </>
      )}
    </section>
  );
}

function DbLookupWorkspace({
  meta,
  riotIdHistory,
  setRiotIdHistory,
  enemyChampionHistory,
  setEnemyChampionHistory,
  myChampionHistory,
  setMyChampionHistory,
}: {
  meta: MetaOptions;
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
  const [error, setError] = useState<string | null>(null);
  const memoItems = usePlayerMemoItems();
  const memoGroups = useMemo(() => groupPlayerMemos(memoItems), [memoItems]);

  const runSearch = async (nextBody = body) => {
    setError(null);
    try {
      setPayload(await api.dbSearch(nextBody));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "DB조회에 실패했습니다.");
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
    const nextBody = {
      ...body,
      riotIdsRaw: rememberRiotIdHistory(body.riotIdsRaw),
      page: 1,
    };
    rememberMyChampionHistory(nextBody);
    rememberEnemyChampionHistory(nextBody);
    setBody(nextBody);
    runSearch(nextBody);
  };

  const movePage = (page: number) => {
    const nextBody = { ...body, page };
    setBody(nextBody);
    runSearch(nextBody);
  };

  const exportCsv = async () => {
    const blob = await api.exportDbSearch(body);
    downloadBlob(blob, "db_matchup_results.csv");
  };

  const pageCount = payload
    ? Math.max(Math.ceil(payload.total / PAGE_SIZE), 1)
    : 1;

  return (
    <section className="screen">
      <WorkspaceHeader
        eyebrow="DB조회"
        title="matchup_index 빠른 조회"
        description="로컬 DB 인덱스에서 메모 유저 또는 입력 유저의 매치업 통계와 목록을 확인합니다."
      />
      <form
        className={`control-panel db-panel ${
          body.periodKind === meta.customPeriodLabel ? "custom-period" : ""
        }`}
        onSubmit={submit}
      >
        <div className="multi-riot-id-field">
          <label className="field textarea-field">
            <span>조회 유저 목록</span>
            <textarea
              ref={textareaRef}
              value={body.riotIdsRaw}
              onChange={(event) =>
                setBody({ ...body, riotIdsRaw: event.target.value })
              }
              placeholder={"비우면 메모 유저만\nHide on bush#KR1, Keria#KR1"}
            />
          </label>
          <MemoGroupSelect groups={memoGroups} onPick={applyMemoGroup} />
        </div>
        <div className="my-champion-field">
          <ChampionSelect
            label="내 챔피언"
            value={body.myChampionKorean}
            meta={meta}
            onChange={(value) => setBody({ ...body, myChampionKorean: value })}
          />
          <RecentMyChampionSearches
            history={myChampionHistory}
            onPick={(value) => setBody({ ...body, myChampionKorean: value })}
          />
        </div>
        <div className="enemy-filter-field">
          <div className="enemy-filter-controls">
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
          </div>
          <RecentEnemyChampionSearches
            history={enemyChampionHistory}
            onPick={(value) => setBody({ ...body, enemyChampionKorean: value })}
          />
        </div>
        {body.periodKind === meta.customPeriodLabel && (
          <div className="db-custom-period-fields">
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
        <div className="db-submit-area">
          <label className="check-field db-filter-option">
            <input
              type="checkbox"
              checked={body.currentPatchOnly}
              onChange={(event) =>
                setBody({ ...body, currentPatchOnly: event.target.checked })
              }
            />
            <span>이번 패치만</span>
          </label>
          <button className="primary-action db-submit-action" type="submit">
            <Database size={18} />
            DB조회 실행
          </button>
        </div>
        <RecentRiotIdSearches
          className="db-riot-history"
          history={riotIdHistory}
          onPick={applyHistoryItem}
        />
      </form>

      {error && <div className="error-box">{error}</div>}
      {payload && (
        <>
          <DbSummary payload={payload} />
          <div className="toolbar">
            <button className="tool-button" onClick={exportCsv}>
              <Download size={16} />
              CSV 다운로드
            </button>
            <div className="pager">
              <button
                disabled={body.page <= 1}
                onClick={() => movePage(body.page - 1)}
              >
                <ChevronLeft size={16} />
              </button>
              <span>
                {body.page} / {pageCount}
              </span>
              <button
                disabled={body.page >= pageCount}
                onClick={() => movePage(body.page + 1)}
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
          />
        </>
      )}
    </section>
  );
}

function PlayerMemoWorkspace() {
  const [items, setItems] = useState<PlayerMemo[]>([]);
  const [riotIdRaw, setRiotIdRaw] = useState("");
  const [memo, setMemo] = useState("");
  const [filter, setFilter] = useState("");
  const [editing, setEditing] = useState<Record<string, string>>({});
  const [editingPuuid, setEditingPuuid] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const loadMemos = async () => {
    const payload = await api.listPlayerMemos();
    setItems(payload.items);
    setEditing(
      Object.fromEntries(payload.items.map((item) => [item.puuid, item.memo])),
    );
    setEditingPuuid(null);
  };

  useEffect(() => {
    loadMemos().catch((exc) =>
      setError(exc instanceof Error ? exc.message : "메모 목록 조회 실패"),
    );
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const target = await api.resolvePlayerMemoRiotId(riotIdRaw);
      await api.upsertPlayerMemo(target.puuid, memo);
      setRiotIdRaw("");
      setMemo("");
      await loadMemos();
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
      await api.upsertPlayerMemo(item.puuid, editing[item.puuid] || "");
      await loadMemos();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "메모 저장 실패");
    } finally {
      setSaving(false);
    }
  };

  const saveAutoRefresh = async (item: PlayerMemo, enabled: boolean) => {
    setSaving(true);
    setError(null);
    try {
      await api.updatePlayerMemoAutoRefresh(item.puuid, enabled);
      await loadMemos();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "자동갱신 설정 실패");
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
    if (!window.confirm(`${name} 메모를 삭제할까요?`)) {
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await api.deletePlayerMemo(item.puuid);
      await loadMemos();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "메모 삭제 실패");
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
        eyebrow="유저 메모"
        title="관심 유저 관리"
        description="DB조회 기본 대상이 되는 메모 유저를 추가하고 관리합니다."
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
          <Plus size={18} />
          추가
        </button>
      </form>
      {error && <div className="error-box">{error}</div>}
      <section className="memo-panel">
        <div className="memo-panel-head">
          <div>
            <span>등록된 메모</span>
            <strong>{items.length}명</strong>
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
        {filteredItems.length === 0 ? (
          <div className="empty compact">
            <strong>등록된 메모가 없습니다.</strong>
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
                            <span>수정 {memoDate(item.updated_at)}</span>
                          </div>
                          <div className="memo-actions">
                            <label className="memo-auto-refresh">
                              <input
                                type="checkbox"
                                checked={item.auto_refresh_enabled}
                                onChange={(event) =>
                                  saveAutoRefresh(item, event.target.checked)
                                }
                                disabled={saving}
                              />
                              자동갱신
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
                              title="삭제"
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
  onSaved: (puuid: string, memo: string) => void;
}) {
  const missing = candidates.filter((candidate) => !memos[candidate.puuid]);
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
      onSaved(candidate.puuid, memo);
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
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description: string;
}) {
  return (
    <header className="workspace-header">
      <span>{eyebrow}</span>
      <h1>{title}</h1>
      <p>{description}</p>
    </header>
  );
}

function MemoPlayerSelect({
  items,
  onPick,
}: {
  items: PlayerMemo[];
  onPick: (item: PlayerMemo) => void;
}) {
  return (
    <label className="field memo-select-field">
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
        disabled={items.length === 0}
      >
        <option value="">
          {items.length ? "메모에서 선택" : "등록된 메모 없음"}
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
  onPick,
}: {
  groups: ReturnType<typeof groupPlayerMemos>;
  onPick: (items: PlayerMemo[], separator?: "line" | "comma") => void;
}) {
  const allItems = groups.flatMap((group) => group.items);

  return (
    <label className="field memo-group-select-field">
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
        disabled={groups.length === 0}
      >
        <option value="">
          {groups.length ? "메모에서 선택" : "등록된 메모 없음"}
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
        eyebrow="최근검색 관리"
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
          title="내 챔피언 최근검색"
          items={myChampionHistory}
          fixedItems={["전체"]}
          emptyText="저장된 내 챔피언 최근검색이 없습니다."
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
  className = "",
  history,
  onPick,
  onRemove,
}: {
  className?: string;
  history: string[];
  onPick: (riotId: string) => void;
  onRemove?: (riotId: string) => void;
}) {
  if (history.length === 0) {
    return null;
  }
  return (
    <div
      className={`recent-searches ${className}`.trim()}
      aria-label="최근검색"
    >
      <span>
        <History size={14} />
        최근검색
      </span>
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
      <span>
        <History size={14} />
        최근 상대 챔피언
      </span>
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
    <div
      className="recent-searches compact-history"
      aria-label="최근 내 챔피언"
    >
      <span>
        <History size={14} />
        최근 내 챔피언
      </span>
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
    <span className={`recent-chip-wrap ${onRemove ? "" : "plain"}`.trim()}>
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
  if (!job || job.status === "succeeded") {
    return null;
  }
  return (
    <div className="progress-box">
      <div className="progress-head">
        <strong>{job.message || "작업 준비 중..."}</strong>
        <span>{Math.round(job.progress * 100)}%</span>
      </div>
      <div className="progress-track">
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
        ["발견", `${total}`, "경기"],
        [
          "승률",
          total ? `${((wins / total) * 100).toFixed(1)}%` : "-",
          `${wins}승 ${losses}패`,
        ],
        ["스캔", `${payload.scanned_total}`, "매치"],
        [
          "캐시/API",
          `${payload.cache_hits} / ${payload.api_calls}`,
          payload.index_allowed
            ? `DB ${payload.indexed_rows} row`
            : `인덱스 제외 · ${payload.index_tier || "랭크 없음"}`,
        ],
      ]}
    />
  );
}

function MultiSummary({ payload }: { payload: MultiSearchPayload }) {
  return (
    <>
      <SummaryGrid
        title={`${payload.input_count}명 입력 · ${payload.period_label}`}
        meta={`1인당 최대 ${payload.matches_per_player}판`}
        items={[
          ["인덱스", `${payload.indexed_rows}`, "row"],
          [
            "성공/실패",
            `${payload.success_count} / ${payload.failure_count}`,
            "유저",
          ],
          [
            "매치/API",
            `${payload.discovered_matches} / ${payload.api_calls}`,
            `신규 상세 ${payload.new_match_details}`,
          ],
          ["캐시", `${payload.cache_hits}`, "hit"],
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
        ["발견", `${payload.stats.total}`, "경기"],
        [
          "승률",
          payload.stats.winRate === null ? "-" : `${payload.stats.winRate}%`,
          `${payload.stats.wins}승 ${payload.stats.losses}패`,
        ],
        ["대상", payload.target_label, `${payload.results.length} 표시 중`],
        [
          "캐시/API",
          `${payload.cache_hits} / ${payload.api_calls}`,
          "PUUID 조회",
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
}: {
  rows: MatchRow[];
  meta: MetaOptions;
  focusPuuid?: string;
  showPlayerCopy: boolean;
  memos?: Record<string, string>;
  clientPagination?: boolean;
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
        <strong>조건에 맞는 경기를 찾지 못했습니다.</strong>
        <p>기간을 늘리거나 챔피언/라인 선택이 맞는지 확인해 주세요.</p>
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
            <button disabled={page <= 1} onClick={() => setPage(page - 1)}>
              <ChevronLeft size={16} />
            </button>
            <span>
              {page} / {pageCount}
            </span>
            <button
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
          <span title={row.game_version}>패치 {patchVersion}</span>
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
        >
          <ExternalLink size={16} />
        </a>
        <button className="icon-button" onClick={onToggle} title="매치 상세">
          {detailOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>
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

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 900);
  };
  return (
    <button className="icon-button" onClick={copy} title={label}>
      {copied ? "OK" : <Copy size={16} />}
    </button>
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
  const [activeTab, setActiveTab] = useState<DetailTab>("summary");

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError(null);
    setActiveTab("summary");
    api
      .getMatchDetail(matchId, playerPuuid)
      .then((value) => {
        if (!cancelled) {
          setDetail(value);
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

  if (error) {
    return <div className="detail-panel error-box">{error}</div>;
  }
  if (!detail) {
    return <div className="detail-panel">매치 상세를 불러오는 중...</div>;
  }
  return (
    <div className="detail-panel">
      <div className="detail-tabs" role="tablist" aria-label="매치 상세 탭">
        <button
          className={activeTab === "summary" ? "active" : ""}
          onClick={() => setActiveTab("summary")}
          type="button"
        >
          종합
        </button>
        <button
          className={activeTab === "build" ? "active" : ""}
          onClick={() => setActiveTab("build")}
          type="button"
        >
          빌드
        </button>
      </div>
      {activeTab === "summary" ? (
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
      <CompareBar label="Total Kill" left={ally.kills} right={enemy.kills} />
      <CompareBar label="Total Gold" left={ally.gold} right={enemy.gold} />
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
    <div className="rune-page stat-shards">
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
