# my-rag-mcp

## 프로젝트 개요
윈도우 환경에서 Notion 워크스페이스 내보내기 파일들을 청크로 분할해 임베딩 기반 벡터 검색과 BM25가 가능한 형태로 로컬 ChromaDB에 저장하고, 리랭커를 포함한 하이브리드 검색을 로컬 MCP 서버로 제공한다.

## 자주 쓰는 명령

### 데이터 다시 반영

```powershell
.\scripts\refresh_data.cmd
```

원본 데이터 신규 등록/변경 시에 실행하는 기본 명령이다. 청크 재생성, 임베딩 재계산, ChromaDB 갱신, 오래된 Chroma 세그먼트 정리, MCP 서버 재시작까지 한 번에 처리한다.

실행 중 오류가 발생하면 즉시 중단하며, 실행 창에 오류 내용을 남긴다.

### 서버만 다시 시작

```powershell
Restart-Service MyRagMcpServer
```

코드 변경이나 인증 설정 변경만 반영할 때 사용한다.

### Windows 서비스 재등록

```powershell
.\ops\register_windows_services.ps1
```

로그 경로 재설정, 서비스 재등록, 시작 유형 재설정이 필요할 때 사용한다. 실행 전제는 `bin/nssm.exe` 존재다.

## 초기 설치

### 1. 가상환경 생성

```powershell
python -m venv .venv
```

### 2. 의존성 설치

```powershell
.\.venv\Scripts\python -m pip install -r requirements.txt
```

### 3. 환경 변수 파일 준비

프로젝트 루트에 `.env` 파일을 둔다. 기본 형식은 `.env.example`을 기준으로 맞춘다.

필수 값은 아래와 같다.

- `MCP_OAUTH_APPROVAL_SECRET`

임베딩 CPU 사용 강도는 아래 값으로 조절할 수 있다.

- `EMBED_CPU_MODE=fast`
- `EMBED_CPU_MODE=balanced`

`fast`는 임베딩 속도 우선 모드다. `balanced`는 다른 작업과 함께 사용할 때 CPU 사용량을 완화하는 모드다.

### 4. Notion 내보내기 데이터 배치

Notion에서 내보낸 압축파일의 압축을 해제한 뒤에 폴더의 내용물을 `notion workspace/` 폴더에 넣는다.

### 5. 최초 데이터 생성

```powershell
.\scripts\refresh_data.cmd
```

최초 실행이 끝나면 ChromaDB가 생성되고 서비스가 새 데이터를 읽을 수 있는 상태가 된다.

## 운영 기준

### 서비스 구성

- `MyRagMcpServer`: Python MCP 서버

서비스는 재등록 시 `자동(지연된 시작)` 기준으로 설정한다.

### 서비스 재등록 스크립트가 하는 일

`ops/register_windows_services.ps1`는 아래 항목을 맞춘다.

- MCP 서버 서비스 재등록
- 서비스 로그 경로를 `logs/`로 설정
- 서비스 시작 유형을 `자동(지연된 시작)`으로 설정

### 로그 정리 기준

- 서비스 로그 저장 위치: `logs/`
- 로그 회전 방식: `nssm` 온라인 회전
- 로그 회전 기준 용량: `1MB`

## 데이터 갱신 흐름

`scripts/refresh_data.cmd` 실행 시 `scripts/refresh_data_launcher.ps1`가 관리자 권한 상승과 실행 창 유지를 처리한다. 실제 데이터 갱신은 `scripts/refresh_data.ps1`가 담당한다.

`embed.py`는 아래 순서로 동작한다.

1. `notion workspace/` 안의 파일을 읽는다.
2. 문서를 청크로 분할한다.
3. 임베딩을 계산해 `notion_chroma_db/`에 저장한다.
4. 현재 DB에서 참조하지 않는 오래된 UUID 폴더를 정리한다.

마지막 단계에서 `scripts/refresh_data.ps1`가 MCP 서버 서비스를 재시작한다.

## 로컬 실행

서비스 대신 직접 실행하려면 아래 명령을 사용한다.

```powershell
.\scripts\start_all.ps1
```

이 방식은 MCP 서버를 로컬 `127.0.0.1:18444`에서 직접 띄울 때 사용한다.

## 문제 발생 시 확인

### Claude 연결 실패

```powershell
Get-Service MyRagMcpServer
```

서비스가 `Running` 상태인지 먼저 확인한다.

### 데이터 반영 실패 의심

```powershell
.\scripts\refresh_data.cmd
```

데이터 반영 문제는 이 명령으로 먼저 다시 확인한다.

### 로그가 루트에 남는 경우

```powershell
.\ops\register_windows_services.ps1
```

실행 중인 서비스가 점유한 활성 로그 파일은 재등록 전까지 루트에 남을 수 있다.

## 주요 파일과 디렉터리

### 루트 주요 파일

- `embed.py`: Notion 파일 청크 분할, 임베딩 및 ChromaDB 재생성
- `mcp_server.py`: MCP 서버 본체
- `notion_store.py`: ChromaDB 조회, BM25, 하이브리드 검색, reranker
- `oauth_provider.py`: OAuth 승인, 토큰 발급, 동적 클라이언트 등록
- `scripts/load_local_env.ps1`: `.env`를 PowerShell 환경변수로 불러오는 공용 로더
- `scripts/run_server.ps1`: `.env`를 읽어 MCP 서버 실행
- `scripts/start_all.ps1`: MCP 서버 로컬 실행
- `scripts/refresh_data.ps1`: 데이터 갱신 진입점
- `scripts/refresh_data_launcher.ps1`: 관리자 권한 상승 및 실행 창 유지
- `scripts/refresh_data.cmd`: 더블클릭용 데이터 갱신 진입점

### 주요 디렉터리

- `ops/`: 운영 스크립트
- `scripts/`: 로컬 실행 및 데이터 반영 스크립트
- `logs/`: 서비스 로그
- `runtime/`: 런타임 상태 파일과 OAuth 상태 파일
- `bin/`: 실행 바이너리
- `notion workspace/`: Notion 워크스페이스 내보내기 파일 원본
- `notion_chroma_db/`: ChromaDB 저장소
- `.venv/`: 프로젝트 내부 가상환경

## 로컬 주소

로컬 MCP 주소 형식은 아래와 같다.

```text
http://127.0.0.1:18444/myrag
```

포트는 `.env`의 `MCP_PORT`로 바꿀 수 있다. 값을 지정하지 않으면 기본값은 `18444`다.

## OAuth

이 프로젝트는 OAuth 보호형 MCP 서버다.

기본 흐름은 아래와 같다.

1. 클라이언트를 등록한다.
2. 승인 페이지로 이동한다.
3. 승인 후 토큰을 발급한다.
4. 이후 `/myrag` 엔드포인트에 접근한다.
