import type {
  DbSearchBody,
  DbSearchPayload,
  JobListPayload,
  JobPayload,
  MatchBuildDetail,
  MatchDetail,
  MatchRankDetail,
  MetaOptions,
  MultiSearchBody,
  PlayerMemo,
  ResolvePlayerMemoPayload,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function requestJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: string };
      message = body.detail || message;
    } catch {
      // JSON이 아닌 오류 응답은 기본 메시지를 사용한다.
    }
    throw new ApiError(message, response.status);
  }
  return (await response.json()) as T;
}

const jobResultRequests = new Map<string, Promise<JobPayload>>();
const matchDetailRequests = new Map<string, Promise<unknown>>();

function requestJobResult(jobId: string): Promise<JobPayload> {
  const existing = jobResultRequests.get(jobId);
  if (existing) {
    return existing;
  }
  const request = requestJson<JobPayload>(`/api/jobs/${jobId}/result`).catch(
    (error) => {
      jobResultRequests.delete(jobId);
      throw error;
    },
  );
  jobResultRequests.set(jobId, request);
  return request;
}

function requestMatchDetailPart<T>(url: string): Promise<T> {
  const existing = matchDetailRequests.get(url) as Promise<T> | undefined;
  if (existing) {
    return existing;
  }
  const request = requestJson<T>(url).catch((error) => {
    matchDetailRequests.delete(url);
    throw error;
  });
  matchDetailRequests.set(url, request);
  return request;
}

function matchDetailUrl(
  matchId: string,
  playerPuuid: string,
  part = "",
): string {
  return `/api/matches/${encodeURIComponent(matchId)}/detail${part}?playerPuuid=${encodeURIComponent(
    playerPuuid,
  )}`;
}

export const api = {
  startup: () =>
    requestJson<MetaOptions>("/api/startup", {
      method: "POST",
    }),
  getMeta: () => requestJson<MetaOptions>("/api/meta/options"),
  refreshMeta: () =>
    requestJson<MetaOptions>("/api/meta/refresh", { method: "POST" }),
  createMultiSearchJob: (body: MultiSearchBody) =>
    requestJson<{ jobId: string }>("/api/jobs/multi-search", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  createPlayerMatchCollectionJob: (confirmed: boolean) =>
    requestJson<{ jobId: string }>("/api/jobs/player-match-collection", {
      method: "POST",
      body: JSON.stringify({ confirmed }),
    }),
  listJobs: (recentLimit = 20) =>
    requestJson<JobListPayload>(`/api/jobs?recentLimit=${recentLimit}`),
  getJobResult: requestJobResult,
  dbSearch: (body: DbSearchBody) =>
    requestJson<DbSearchPayload>("/api/db-search", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getDbEnemyRiotIds: (body: DbSearchBody) =>
    requestJson<{ items: string[] }>("/api/db-search/enemy-riot-ids", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getMatchDetail: (matchId: string, playerPuuid: string) =>
    requestMatchDetailPart<MatchDetail>(matchDetailUrl(matchId, playerPuuid)),
  getMatchBuildDetail: (matchId: string, playerPuuid: string) =>
    requestMatchDetailPart<MatchBuildDetail>(
      matchDetailUrl(matchId, playerPuuid, "/build"),
    ),
  getMatchRankDetail: (matchId: string, playerPuuid: string) =>
    requestMatchDetailPart<MatchRankDetail>(
      matchDetailUrl(matchId, playerPuuid, "/ranks"),
    ),
  listPlayerMemos: () =>
    requestJson<{ items: PlayerMemo[] }>("/api/player-memos"),
  resolvePlayerMemoRiotId: (riotIdRaw: string) =>
    requestJson<ResolvePlayerMemoPayload>("/api/player-memos/resolve-riot-id", {
      method: "POST",
      body: JSON.stringify({ riotIdRaw }),
    }),
  upsertPlayerMemo: (puuid: string, memo: string) =>
    requestJson<{ ok: boolean }>(
      `/api/player-memos/${encodeURIComponent(puuid)}`,
      {
        method: "PUT",
        body: JSON.stringify({ memo }),
      },
    ),
  updatePlayerMemoAutoRefresh: (puuid: string, enabled: boolean) =>
    requestJson<{ ok: boolean }>(
      `/api/player-memos/${encodeURIComponent(puuid)}/auto-refresh`,
      {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      },
    ),
  deletePlayerMemo: (puuid: string) =>
    requestJson<{ ok: boolean }>(
      `/api/player-memos/${encodeURIComponent(puuid)}`,
      {
        method: "DELETE",
      },
    ),
  exportDbSearch: async (body: DbSearchBody) => {
    const response = await fetch("/api/exports/db-search.csv", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      throw new Error("CSV 다운로드에 실패했습니다.");
    }
    return response.blob();
  },
};
