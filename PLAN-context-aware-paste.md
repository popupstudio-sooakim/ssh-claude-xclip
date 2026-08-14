# 계획서 — 컨텍스트 인식 붙여넣기 (로컬/서버 양쪽 동작)

> 상태: **초안 (구현 전, 컨펌 대기)**
> 목표: 로컬 Claude Code에서도 이미지 붙여넣기가 되게 하되, 서버 동작은 그대로 유지.

## 1. 문제

현재 구조는 **캡처 시점에 클립보드를 서버 경로로 파괴적으로 덮어쓴다** (`mac.py`의
`JXA_REWRITE`, 데이터 분기; `JXA_SET_TEXT`, 파일 분기). 그런데 OS 클립보드는 전역이고
"이 이미지가 어디에 붙여질지"라는 정보가 없다. 데몬은 아직 사용자가 붙여넣을 목적지를
정하기도 전에 "이건 서버로 간다"고 미리 확정해버린다.

결과:
- **서버**에 붙여넣기 → 그 경로 `/tmp/clip-*.png`가 방금 업로드돼 실존 → `[Image #N]` 변환 ✅
- **로컬(맥)**에 붙여넣기 → 그 경로는 서버 경로라 맥에 없음 → 변환 실패, 경로 문자열만 박힘 ❌

즉 로컬이 깨지는 건 버그가 아니라 현재 설계의 필연이다. (역설적으로 서버가 안 닿아
업로드가 실패하면 rewrite가 안 일어나 원본 이미지가 남고, 그때만 로컬이 됨.)

## 2. 핵심 인사이트

**클립보드는 전역이고 목적지를 모른다. "어디에 붙일지" 아는 유일한 계층은 붙여넣기를
받는 앱이다.** 따라서 결정을 **캡처 시점 → 붙여넣기 시점**으로 옮기고, 그 앱이 아는
컨텍스트로 분기해야 한다.

우리는 VS Code Remote-SSH를 쓰므로 그 컨텍스트 신호가 이미 존재한다:

- `vscode.env.remoteName` — 로컬 창이면 `undefined`, 서버에 붙은 창이면 `"ssh-remote"`.
- 현재 확장이 `extensionKind: ["ui"]`(맥 로컬 실행)라, 창마다 이 값을 읽어
  "이 터미널이 로컬 소속이냐 서버 소속이냐"를 정확히 구분할 수 있다.
- 참고: VS Code 이슈 microsoft/vscode-remote-release#9083 이 바로 이 신호를 다룬다.

## 3. 재설계안

로직을 **데몬(캡처 트리거) → 확장(붙여넣기 트리거 + 컨텍스트 분기)** 로 옮긴다.
데몬의 강점인 "push 선행(미리 업로드)"은 그대로 유지해 속도 저하가 없다.

### 3-1. 데몬(`ssh-claude-xclip-mac.py`) — 클립보드를 더 이상 덮어쓰지 않는다

- 캡처 감지 → 서버로 **선제 업로드**까지만 하고, 클립보드엔 **원본 이미지를 그대로 둔다.**
  - 즉 `JXA_REWRITE` / `JXA_SET_TEXT` 로 클립보드를 다시 채우는 단계를 제거.
  - (파일 복사 분기도 마찬가지로 클립보드를 건드리지 않음.)
- 대신 업로드한 서버 경로를 **로컬 사이드카 파일**에 기록만 한다.
  - 예: `~/.cache/ssh-claude-xclip/last.json`
  - 내용: `{ "change_count": <업로드 시점 클립보드 changeCount>, "remote_paths": ["/tmp/clip-...png", ...], "ts": <epoch> }`
  - `change_count`는 붙여넣기 시점의 클립보드가 이 업로드와 같은 이미지인지 대조하는 키.

### 3-2. 확장(`ssh-claude-xclip-vscode`) — 붙여넣기를 소유하고 소속으로 분기

- `cmd+v` / `cmd+shift+v`를 터미널에서 가로챈다.
- 동작:
  - `vscode.env.remoteName === 'ssh-remote'` (서버 창)
    → 사이드카에서 미리 올려둔 경로를 읽어 `terminal.sendText(path, false)`로 타이핑
    → 서버 Claude Code가 `[Image #N]` 첨부. (지금과 동일, 이미 업로드돼 있어 여전히 빠름)
  - `vscode.env.remoteName === undefined` (로컬 창)
    → 아무 것도 하지 않고 **네이티브 붙여넣기로 통과**시킨다
    → 클립보드에 원본 이미지가 살아있으니 로컬 Claude Code가 바로 첨부.
- `cmd+v` 바인딩을 새로 잡을 때 **로컬 창에서는 반드시 기본 붙여넣기로 폴백**해야 한다
  (확장이 삼키면 안 됨). 방법 후보:
  - (a) 활성화 시 `remoteName` 기반 커스텀 컨텍스트 키를 세팅하고 `when` 절로 키바인딩을
    서버 창에서만 활성화, 또는
  - (b) 런타임에서 로컬이면 `workbench.action.terminal.paste`(기본 붙여넣기)로 위임.

### 3-3. 결과

- 로컬·서버 둘 다 동작. `launchctl` 수동 토글 불필요.
- 핵심은 "클립보드를 안 건드린다" + "붙여넣기 시점에 창 소속으로 분기".

## 4. 현실적 주의점

1. **로컬 네이티브 붙여넣기엔 `pngpaste`가 필요하다.**
   Claude Code는 맥에서 클립보드 이미지를 `pngpaste`로 읽는다 (리눅스는 `xclip`/`wl-paste`).
   로컬에서 `Cmd+V` 첨부가 되려면 이게 깔려 있어야 함.
   ```bash
   brew install pngpaste
   ```
   → 설치법(README) / 업데이트 스크립트에 이 의존성 추가 검토.

2. **이 깔끔한 분기는 "VS Code 안"에서만 성립한다.**
   VS Code만이 붙여넣기 훅(터미널 키바인딩) + 컨텍스트(`remoteName`)를 동시에 준다.
   iTerm/Terminal.app 같은 생 터미널로 SSH 하면 붙여넣기 목적지를 맥 쪽에서 알아낼 방법이
   마땅치 않다(포그라운드 프로세스/tty 추적은 취약). 그 경우엔 당분간 데몬 on/off 토글이
   가장 현실적. (필요하면 `xclip-toggle` 헬퍼 + zsh 별칭을 별도로 제공.)

3. **사이드카 신선도/정합성.**
   서버 쪽 가짜 xclip이 이미 쓰는 신선도 가드(기본 10분, `SSH_CLAUDE_XCLIP_MAX_AGE`)와
   동일한 개념을 확장에서도 적용. 가능하면 `change_count`로 "지금 클립보드 == 마지막
   업로드"인지 대조해 엉뚱한 옛 경로 타이핑을 방지.

## 5. 장기 방향 (참고, 이번 범위 아님)

"가짜 xclip"은 Claude Code 내부 구현(xclip 호출 규격)에 기댄 임시방편이다. 업스트림에
정공법 이슈가 있음: anthropics/claude-code#42712 "Support OSC 52/5522 clipboard for image
paste over SSH".

- OSC 52 = **텍스트 전용** → 이미지 불가.
- 이미지는 OSC 5522(Kitty 클립보드 프로토콜)로 SSH 너머 전송 가능하지만 **Kitty 터미널**
  필요. VS Code 내장 터미널(xterm.js)은 미지원 → 지금 워크플로엔 선택지 아님.
- Claude Code가 이걸 네이티브 지원하면 이 도구 전체가 필요 없어질 수 있다.

## 6. 구현 체크리스트 (다음에 돌아와서)

- [ ] `mac.py`: `JXA_REWRITE` / `JXA_SET_TEXT` 로 클립보드 재작성하는 단계 제거
- [ ] `mac.py`: 업로드 후 `~/.cache/ssh-claude-xclip/last.json` 사이드카 기록
      (`change_count`, `remote_paths`, `ts`)
- [ ] 확장 `package.json`: `cmd+v` 키바인딩 추가 (+ 로컬/서버 분기용 `when` 컨텍스트 키)
- [ ] 확장 `extension.js`: `vscode.env.remoteName` 분기
      — 서버: 사이드카 경로 타이핑 / 로컬: 기본 붙여넣기 위임
- [ ] 확장 `extension.js`: 사이드카 신선도·`change_count` 대조
- [ ] README/ARCHITECTURE: 새 동작 반영, `pngpaste` 로컬 의존성 명시
- [ ] 검증: 로컬 창 `Cmd+V` → 첨부 / 서버 창 `Cmd+V`·`Cmd+Shift+V` → 첨부 둘 다 확인

## 7. 참고 링크

- VS Code API — env.remoteName / remote extensions:
  https://code.visualstudio.com/api/advanced-topics/remote-extensions
- VS Code issue #9083 (remote 연결 식별):
  https://github.com/microsoft/vscode-remote-release/issues/9083
- claude-code#42712 (OSC 52/5522 이미지 붙여넣기):
  https://github.com/anthropics/claude-code/issues/42712
- claude-code#13966 (일부 터미널 이미지 붙여넣기 이슈):
  https://github.com/anthropics/claude-code/issues/13966
