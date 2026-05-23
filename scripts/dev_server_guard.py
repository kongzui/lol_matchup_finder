"""백엔드/프론트엔드 개발 서버 포트의 기존 프로세스를 정리한다.

같은 프로젝트(lol_matchup_finder)의 기존 서버라면 자동으로 종료하고,
다른 프로세스가 점유 중이면 안내 메시지를 출력한 뒤 비-0 코드로 종료한다.
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 이 프로젝트의 개발 서버 프로세스를 식별하기 위한 커맨드라인 키워드.
# - 프로젝트 절대 경로: vite/node 프로세스 식별 (모듈 경로가 cmd에 그대로 노출됨)
# - src.api.main:app: uvicorn으로 띄운 백엔드 식별 (시스템 Python으로 띄워도 매칭됨)
PROJECT_MARKERS: tuple[str, ...] = (
    str(PROJECT_ROOT).lower(),
    "src.api.main:app",
)

BIND_HOST = "127.0.0.1"
BACKEND_PORT = 8000
FRONTEND_PORT = 5173
PORT_FREE_TIMEOUT_SEC = 3.0


def log(message: str) -> None:
    """배치 파일에서 그대로 보이도록 stdout에 메시지를 출력한다."""
    print(message, flush=True)


def is_port_listening(host: str, port: int) -> bool:
    """지정 포트가 로컬에서 열려 있는지 빠르게 확인한다."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) == 0


def find_listening_pids(port: int) -> list[int]:
    """netstat 결과에서 지정 포트의 LISTENING PID 목록을 추출한다."""
    result = subprocess.run(
        ["netstat", "-ano"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
    )
    pids: set[int] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        if not parts[0].upper().startswith("TCP"):
            continue
        if parts[3].upper() != "LISTENING":
            continue
        local = parts[1]
        if not local.endswith(f":{port}"):
            continue
        try:
            pids.add(int(parts[4]))
        except ValueError:
            continue
    return sorted(pids)


def get_process_command(pid: int) -> str:
    """PowerShell로 지정 PID의 전체 커맨드라인을 얻는다."""
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine',
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
    )
    return (result.stdout or "").strip()


def is_same_project(command_line: str) -> bool:
    """커맨드라인에 프로젝트 식별 키워드 중 하나가 포함되어 있으면 같은 프로젝트로 본다."""
    lowered = command_line.lower()
    return any(marker in lowered for marker in PROJECT_MARKERS)


def kill_process_tree(pid: int) -> None:
    """taskkill로 지정 PID와 자식 프로세스를 강제 종료한다."""
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
    )


def wait_until_port_free(port: int, timeout: float = PORT_FREE_TIMEOUT_SEC) -> bool:
    """프로세스 종료 후 포트가 비기를 잠깐 기다린다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_port_listening(BIND_HOST, port):
            return True
        time.sleep(0.1)
    return not is_port_listening(BIND_HOST, port)


def guard_port(label: str, port: int) -> int:
    """단일 포트에 대해 동일 프로젝트면 종료, 외부 프로세스면 안내한다."""
    if not is_port_listening(BIND_HOST, port):
        return 0

    pids = find_listening_pids(port)
    if not pids:
        log(
            f"[{label}] {port} 포트가 열려 있지만 PID를 확인하지 못했습니다. 그대로 진행합니다."
        )
        return 0

    same_project: list[int] = []
    foreign: list[tuple[int, str]] = []
    for pid in pids:
        command = get_process_command(pid)
        if is_same_project(command):
            same_project.append(pid)
        else:
            foreign.append((pid, command))

    if foreign:
        log(f"[{label}] {port} 포트를 다른 프로젝트의 프로세스가 사용 중입니다.")
        for pid, command in foreign:
            shown = command if command else "(커맨드라인을 읽을 수 없음)"
            log(f"  - pid={pid} cmd={shown}")
        log("  해당 프로세스를 직접 종료한 뒤 다시 실행해 주세요.")
        return 1

    for pid in same_project:
        log(f"[{label}] 같은 프로젝트의 기존 서버를 종료합니다 (pid={pid}).")
        kill_process_tree(pid)

    if not wait_until_port_free(port):
        log(f"[{label}] {port} 포트가 비워지지 않았습니다. 잠시 후 다시 시도해 주세요.")
        return 1
    return 0


def main() -> int:
    """인자로 지정한 대상(backend / frontend / both)을 정리한다."""
    parser = argparse.ArgumentParser(description="개발 서버 포트 가드")
    parser.add_argument(
        "targets",
        nargs="+",
        choices=["backend", "frontend", "both"],
        help="정리할 대상",
    )
    args = parser.parse_args()

    targets: set[str] = set()
    for value in args.targets:
        if value == "both":
            targets.update({"backend", "frontend"})
        else:
            targets.add(value)

    exit_code = 0
    if "backend" in targets:
        exit_code |= guard_port("backend", BACKEND_PORT)
    if "frontend" in targets:
        exit_code |= guard_port("frontend", FRONTEND_PORT)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
