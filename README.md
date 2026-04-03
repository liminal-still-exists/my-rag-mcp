# my-rag-mcp

Notion Markdown export를 로컬 ChromaDB에 임베딩한 뒤, 이를 원격 MCP 서버로 제공하는 프로젝트입니다.

이 문서는 "처음 설치할 때 무엇을 해야 하는지", "평소에는 무엇만 하면 되는지", "문제가 생기면 무엇부터 보면 되는지" 기준으로 정리합니다.

## 30초 요약

주인님이 평소에 기억할 것은 거의 이것 하나입니다.

```powershell
.\refresh_data.cmd
```

이 명령은:

1. Notion 데이터를 다시 임베딩하고
2. ChromaDB를 다시 만들고
3. 오래된 Chroma 찌꺼기 폴더를 정리하고
4. MCP 서버 서비스를 재시작합니다

즉, **Notion 데이터가 바뀌면 이것만 실행하면 됩니다.**

## 이 프로젝트가 하는 일

이 프로젝트는 크게 3단계로 동작합니다.

1. `notion workspace/` 안의 Notion Markdown export를 읽습니다.
2. 내용을 청크로 나누고 임베딩해서 `notion_chroma_db/`에 저장합니다.
3. 그 데이터를 MCP 서버를 통해 외부 클라이언트가 질의할 수 있게 합니다.

외부에서는 HTTPS로 접근하고, 내부에서는 Python MCP 서버와 Caddy가 함께 동작합니다.

## 처음 설치할 때

### 1. Python 가상환경 만들기

```powershell
python -m venv .venv
```

### 2. 의존성 설치

```powershell
.\.venv\Scripts\python -m pip install -r requirements.txt
```

### 3. 환경 설정 파일 준비

`.env.example`를 참고해서 루트에 `.env` 파일을 준비합니다.

최소한 아래 값들은 실제 운영값으로 채워야 합니다.

- `MCP_PUBLIC_BASE_URL`
- `MCP_PUBLIC_HOST`
- `MCP_OAUTH_APPROVAL_SECRET`

서비스 실행과 로컬 실행은 모두 같은 `.env`를 읽습니다.

임베딩 CPU 사용 강도를 조절하고 싶으면 `.env`에 아래 값을 넣을 수 있습니다.

- `EMBED_CPU_MODE=fast`
  - 임베딩 속도 우선
- `EMBED_CPU_MODE=balanced`
  - 다른 작업과 같이 쓰기 쉬운 쪽으로 CPU 사용량 완화

### 4. Notion export 넣기

`notion workspace/` 폴더 안에 Notion Markdown export 파일들을 넣습니다.

이 폴더가 임베딩 대상 원본입니다.

### 5. 최초 데이터 생성

```powershell
.\refresh_data.cmd
```

이 단계까지 끝나면 데이터가 생성되고, 서버가 새 데이터를 읽을 수 있는 상태가 됩니다.

## 평소 운영

### 데이터가 바뀌었을 때

```powershell
.\refresh_data.cmd
```

이게 평소 운영에서 가장 중요한 명령입니다.

주의:

- `refresh_data.cmd`를 더블클릭하면 관리자 권한을 요청한 뒤 실행됩니다.
- `refresh_data.cmd`는 더블클릭용 진입점입니다.
- 관리자 권한 상승과 실행 창 유지는 내부적으로 `refresh_data_launcher.ps1`가 처리합니다.
- 실제 데이터 갱신 로직은 `refresh_data.ps1`가 처리합니다.

### 데이터 갱신 없이 서버만 다시 시작하고 싶을 때

관리자 PowerShell에서:

```powershell
Restart-Service MyRagMcpServer
```

일반적인 코드/인증 변경 반영은 이 명령으로 충분합니다.
`Caddyfile`을 바꾼 경우에만 `MyRagCaddy`까지 다시 시작하면 됩니다.

### 서비스 로그 경로를 다시 맞추고 싶을 때

관리자 PowerShell에서:

```powershell
.\ops\register_windows_services.ps1
```

이 스크립트를 실행하려면 프로젝트 루트의 `bin/nssm.exe`가 준비되어 있어야 합니다.

이 스크립트는:

- MCP 서버 서비스 재등록
- Caddy 서비스 재등록
- 로그 경로를 `logs/`로 정리

를 처리합니다.

### 로그 자동 정리 작업을 다시 등록하고 싶을 때

관리자 PowerShell에서:

```powershell
.\ops\register_log_cleanup_task.ps1
```

이 작업은 매일 로그를 정리합니다.
설정한 시각에 컴퓨터가 꺼져 있어도, 켜진 뒤 가능한 시점에 실행되도록 등록합니다.

## 로컬에서 직접 실행하고 싶을 때

서비스 대신 직접 띄우려면:

```powershell
.\start_all.ps1
```

이 스크립트는 MCP 서버와 Caddy를 같은 로컬 환경에서 함께 실행합니다.

즉:

- 평소 운영: 서비스 기반
- 수동 확인/개발 실행: `start_all.ps1`

로 보면 됩니다.

## 폴더 구조

### 루트 주요 파일

- `embed.py`
  - Notion Markdown를 읽고 ChromaDB를 다시 만듭니다.
  - 임베딩이 끝난 뒤 현재 DB에서 참조하지 않는 오래된 Chroma 세그먼트 폴더를 삭제합니다.
- `mcp_server.py`
  - MCP 서버 본체입니다.
  - OAuth 보호, 원격 접속용 메타데이터 응답, MCP 엔드포인트를 담당합니다.
- `notion_store.py`
  - ChromaDB 조회, BM25, 하이브리드 검색, reranker를 담당합니다.
- `oauth_provider.py`
  - OAuth 승인, 토큰 발급, 동적 클라이언트 등록을 담당합니다.
- `run_server.ps1`
  - `.env`를 읽어 MCP 서버를 실행하는 스크립트입니다.
- `run_caddy.ps1`
  - `.env`를 읽어 Caddy를 실행하는 스크립트입니다.
- `start_all.ps1`
  - 로컬에서 MCP 서버와 Caddy를 함께 띄웁니다.
- `load_local_env.ps1`
  - `.env`를 PowerShell 환경변수로 불러오는 공용 로더입니다.
- `refresh_data.ps1`
  - 데이터 갱신용 진입점입니다.
- `refresh_data_launcher.ps1`
  - `refresh_data.cmd`에서 호출하는 관리자 실행용 런처입니다.
- `refresh_data.cmd`
  - 더블클릭으로 데이터 갱신을 시작하는 진입점입니다.
- `Caddyfile`
  - 외부 HTTPS 요청을 내부 MCP 서버로 reverse proxy 합니다.

### 폴더별 역할

- `ops/`
  - 서비스 설치, 작업 스케줄러, 로그 청소 같은 운영 스크립트
- `logs/`
  - 서비스 로그와 시작 로그
- `runtime/`
  - PID 파일
- `bin/`
  - 실행 바이너리
- `notion workspace/`
  - Notion export 원본
- `notion_chroma_db/`
  - ChromaDB 저장소
- `.venv/`
  - 프로젝트 내부 가상환경

## 데이터 갱신이 내부적으로 하는 일

`refresh_data.cmd`를 실행하면 내부적으로 `refresh_data_launcher.ps1`가 호출됩니다.

그 다음 `refresh_data_launcher.ps1`가 관리자 권한을 올리고 `refresh_data.ps1`를 실행합니다.

그 안에서 `embed.py`가 돌아갑니다.

`embed.py`는 다음 순서로 동작합니다.

1. `notion workspace/` 안의 Markdown 파일을 읽습니다.
2. 헤딩 구조를 기준으로 청크를 만듭니다.
3. 임베딩을 계산해 `notion_chroma_db/`에 저장합니다.
4. 현재 DB에서 참조하지 않는 오래된 UUID 폴더를 정리합니다.

그 후 `refresh_data.ps1`가 MCP 서버 서비스를 재시작해서 새 데이터를 다시 읽게 합니다.

## 원격 접속 주소

원격 MCP 주소는 배포 환경의 공개 도메인과 MCP 경로를 합친 값입니다.

형식:

```text
https://<public-domain>/myrag
```

실제 도메인은 환경에 맞게 설정해서 사용합니다.

## OAuth 메모

이 프로젝트는 OAuth 보호된 MCP 서버입니다.

대략적인 흐름은 이렇습니다.

1. 클라이언트가 등록됩니다.
2. 승인 페이지로 이동합니다.
3. 승인 후 토큰이 발급됩니다.
4. 이후 `/myrag` 엔드포인트에 접근합니다.

구현 세부사항은 `mcp_server.py`, `oauth_provider.py`에서 처리합니다.

## 문제 생겼을 때 먼저 볼 것

### 1. Claude 연결이 안 될 때

먼저 서비스가 살아 있는지 확인합니다.

```powershell
Get-Service MyRagMcpServer,MyRagCaddy
```

둘 다 `Running`이어야 합니다.

### 2. 데이터가 반영되지 않는 것 같을 때

먼저 이것부터 다시 실행합니다.

```powershell
.\refresh_data.cmd
```

### 3. 로그가 루트에 남아 있을 때

관리자 PowerShell에서:

```powershell
.\ops\register_windows_services.ps1
```

현재 실행 중인 서비스가 붙잡고 있는 활성 로그 파일은 재등록 전까지 루트에 남아 있을 수 있습니다.

### 4. 로그가 너무 많이 쌓일 때

자동 정리 작업을 다시 등록합니다.

```powershell
.\ops\register_log_cleanup_task.ps1
```

## 로그 관리

로그는 소모품입니다.
오래된 로그는 운영상 꼭 보관할 필요가 없고, 최근 것만 조금 남아 있으면 충분합니다.

현재 구조는 다음과 같습니다.

- 서비스 로그는 `logs/`에 쌓이도록 정리 중
- 회전 로그는 `.\ops\cleanup_logs.ps1`로 정리
- 자동 정리는 작업 스케줄러가 담당

`cleanup_logs.ps1`는 로그 패턴별로 최근 10개만 남기고 오래된 로그를 삭제합니다.
