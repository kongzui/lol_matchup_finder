import type {
  DbSearchBody,
  DbSearchPayload,
  JobPayload,
  JobStatus,
  MatchDetail,
  MetaOptions,
  MultiSearchBody,
  SearchBody,
} from "./types";

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
    throw new Error(message);
  }
  return (await response.json()) as T;
}

export const api = {
  getMeta: () => requestJson<MetaOptions>("/api/meta/options"),
  createSearchJob: (body: SearchBody) =>
    requestJson<{ jobId: string }>("/api/jobs/search", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  createMultiSearchJob: (body: MultiSearchBody) =>
    requestJson<{ jobId: string }>("/api/jobs/multi-search", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getJob: (jobId: string) => requestJson<JobStatus>(`/api/jobs/${jobId}`),
  getJobResult: (jobId: string) =>
    requestJson<JobPayload>(`/api/jobs/${jobId}/result`),
  dbSearch: (body: DbSearchBody) =>
    requestJson<DbSearchPayload>("/api/db-search", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getMatchDetail: (matchId: string, playerPuuid: string) =>
    requestJson<MatchDetail>(
      `/api/matches/${encodeURIComponent(matchId)}/detail?playerPuuid=${encodeURIComponent(
        playerPuuid,
      )}`,
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

export function searchCsvUrl(jobId: string): string {
  return `/api/exports/search/${encodeURIComponent(jobId)}.csv`;
}
