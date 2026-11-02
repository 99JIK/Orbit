# Orbit 서버 배포 명세 (v0.1)

| 항목 | 내용 |
| --- | --- |
| 상태 | M1 구현 기준 (`ingest` 프로세스) |
| 템플릿 | `deploy/` |
| 상위 문서 | [spec.md](spec.md) 4.7, 10.2 |

## 1. 개요

### 1.1 목적

홈서버 프로세스를 플랫폼별 서비스 관리자에 등록해, 부팅 시 자동으로 시작하고 비정상 종료 시 다시 시작하게 하는 절차를 정의한다.

### 1.2 범위

| 플랫폼 | 등급 | 방식 | 템플릿 |
| --- | --- | --- | --- |
| Linux | 1 | systemd 사용자 서비스 | `deploy/systemd/orbit-ingest.service` |
| macOS | 1 | launchd 사용자 에이전트 | `deploy/launchd/dev.orbit.ingest.plist` |
| Docker·NAS | 1 | Docker Compose | `deploy/docker/compose.yaml` |
| Windows | 2 | 작업 스케줄러 | `deploy/windows/install-task.ps1` |

등급의 정의는 spec 4.7을 따른다. M1에서는 `ingest`만 등록한다. `worker`·`bot`·`watchers`는 해당 마일스톤에서 같은 방식으로 추가한다.

### 1.3 공통 요구 사항

- Python 3.11 이상 (Docker 제외)
- 데이터 디렉터리 `ORBIT_HOME`(기본 `~/orbit`)은 로컬 디스크에 있어야 한다. 네트워크 공유나 동기화 폴더는 쓰지 않아야 한다 (spec 4.4)
- 서버 포트(기본 8787)는 tailnet 밖에 노출하지 않아야 한다. 공유기 포트 포워딩을 하지 않는다
- 정전 복구 설정은 spec 10.2를 따른다

### 1.4 공통 설치 단계 (Docker 제외)

```sh
git clone https://github.com/99JIK/Orbit.git ~/Orbit
cd ~/Orbit/server
python3 -m venv .venv
.venv/bin/pip install .                  # Windows: .venv\Scripts\pip install .
.venv/bin/orbit-server device add galaxy-01
```

`device add`가 출력한 토큰을 Companion 앱에 입력한다 ([companion.md](companion.md) 4.2).

## 2. Linux (systemd)

1. 템플릿의 `ExecStart` 경로를 1.4에서 만든 가상환경에 맞게 고친다. 기본값은 `~/Orbit/server/.venv`다
2. 등록한다

   ```sh
   mkdir -p ~/.config/systemd/user
   cp ~/Orbit/deploy/systemd/orbit-ingest.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now orbit-ingest
   loginctl enable-linger "$USER"
   ```

3. `enable-linger`를 해야 로그인하지 않은 상태에서도 부팅 시 서비스가 시작된다
4. 확인: `systemctl --user status orbit-ingest`, 로그: `journalctl --user -u orbit-ingest`

## 3. macOS (launchd)

1. 템플릿의 `__HOME__`을 홈 디렉터리 절대 경로(예: `/Users/jik`)로 바꾼다. launchd는 `~`와 환경 변수를 확장하지 않는다
2. 로그 디렉터리를 만들고 등록한다

   ```sh
   mkdir -p ~/orbit/logs
   cp ~/Orbit/deploy/launchd/dev.orbit.ingest.plist ~/Library/LaunchAgents/
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.orbit.ingest.plist
   ```

3. 사용자 에이전트는 로그인 후 시작된다. 무인 부팅에는 자동 로그인이 필요하다 (spec 10.2)
4. 확인: `launchctl print gui/$(id -u)/dev.orbit.ingest`, 로그: `~/orbit/logs/ingest.log`

## 4. Docker·NAS

```sh
cd ~/Orbit/deploy/docker
export ORBIT_BIND=100.x.y.z        # 호스트의 Tailscale IP (tailscale ip -4)
docker compose up -d --build
docker compose run --rm ingest orbit-server device add galaxy-01
```

| 변수 | 기본값 | 의미 |
| --- | --- | --- |
| `ORBIT_BIND` | `0.0.0.0` | 포트를 열 호스트 주소. Tailscale IP를 권장한다 |
| `ORBIT_DATA` | `./orbit-data` | 컨테이너 `/data`에 연결할 호스트 디렉터리 |

- 이미지는 `linux/amd64`, `linux/arm64`를 지원한다
- 컨테이너는 UID 1000으로 실행한다. `ORBIT_DATA` 디렉터리는 이 UID가 쓸 수 있어야 한다
- NAS 컨테이너 관리 도구(Synology Container Manager 등)에서는 `compose.yaml`을 프로젝트로 가져와 같은 변수를 지정한다
- 저사양 NAS는 코어 전용으로 운영한다 (spec 4.5)

## 5. Windows (작업 스케줄러)

1. 1.4 단계를 PowerShell에서 수행한다
2. 관리자 권한 PowerShell에서 등록 스크립트를 실행한다

   ```powershell
   cd C:\Github\Orbit\deploy\windows
   .\install-task.ps1 -ServerExe C:\Github\Orbit\server\.venv\Scripts\orbit-server.exe
   ```

3. 스크립트는 현재 계정 암호를 묻고, "로그온 여부와 관계없이 실행"하는 작업을 만든다. 실패하면 1분 간격으로 다시 시작한다
4. 처음 실행 시 Windows 방화벽이 Python의 네트워크 접근을 물으면 개인 네트워크만 허용한다
5. 확인: `Get-ScheduledTask -TaskName 'Orbit Ingest'`, 동작 확인: `curl http://localhost:8787/health`
6. 해제: `Unregister-ScheduledTask -TaskName 'Orbit Ingest'`

## 6. 검증

플랫폼과 무관하게 다음을 확인한다.

| 확인 항목 | 방법 | 기대 결과 |
| --- | --- | --- |
| 서비스 응답 | `curl http://<tailnet-host>:8787/health` | `{"status":"ok", ...}` |
| 기기 인증 | Companion "저장 및 연결 확인" | "연결됨 · 기기 <device_id>" |
| 자동 재시작 | 서버 프로세스를 강제 종료 | 1분 안에 다시 응답 |
| 부팅 시 시작 | 재부팅 후 로그인하지 않은 상태에서 `/health` | 응답 (macOS는 자동 로그인 전제) |
