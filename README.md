# ssh-claude-xclip

맥에서 캡쳐한 스크린샷을 SSH 너머 서버의 Claude Code에 바로 붙여넣는 도구.
서버에 포트를 열지 않는다. 동작 원리·트러블슈팅은 [ARCHITECTURE.md](ARCHITECTURE.md) 참고.

<details>
<summary><b>In English</b> (full docs below are in Korean)</summary>

### What it is

Paste a Mac screenshot straight into Claude Code running on a remote server over SSH.
**No listening port, no reverse tunnel, no token, nothing to run as a service on the server.**

### How it's different

Existing tools **pull at paste time**: the server reaches back to your Mac through an SSH
reverse tunnel, which needs a dedicated port, a bearer token, a binary deployed on the server,
and sometimes an Xvfb bridge to fake a clipboard on a headless box. Others upload at keypress,
so you wait every time.

This one **pushes at capture time**. A small `launchd` daemon on the Mac watches the pasteboard
and, the moment you take a screenshot, ships the file to the server's `/tmp` over plain outbound
SSH, before you ever hit paste. At paste time the server only reads a local file, so the tunnel
and the port disappear.

Two paste modes, because showing an image to an AI and seeing it yourself are different needs:

| Key | Result |
|---|---|
| `Cmd+V` | attaches the image to Claude as `[Image #N]` |
| `Cmd+Shift+V` | types the server path instead, so you can `Cmd`-click and open the image yourself |

No cleanup code: the server's `/tmp` empties itself (on reboot, and swept periodically before
that). Save anything worth keeping somewhere else.

### Moving parts

| File | Role | Installed at |
|---|---|---|
| `ssh-claude-xclip-mac.py` | Mac daemon: watch clipboard, pre-upload (~210 lines, zero deps: stock `python3`, `osascript`, `ssh`) | Mac `~/bin/` |
| `ai.popup.ssh-claude-xclip.plist` | launchd registration | Mac `~/Library/LaunchAgents/` |
| `ssh-claude-xclip-server` | fake `xclip` intercepting the call Claude Code already makes (~60 lines of bash) | Server `~/.local/bin/xclip` |
| `ssh-claude-xclip-vscode/` | mini VS Code extension for `Cmd+Shift+V` (21 lines) | Mac `~/.vscode/extensions/` |

### Requirements

macOS + **VS Code** (Remote-SSH), a Linux server with `bash`, and password-less SSH from the Mac.
Uploads and Claude Code must run as the **same account**, sharing the **same `/tmp`** (a container
or systemd `PrivateTmp` breaks this).

### Known limits

- **VS Code only.** Cursor and VS Code Insiders install extensions elsewhere, so `Cmd+Shift+V` is
  missing there; uploads and `Ctrl+V` still work.
- **One server at a time** (set by `SSH_CLAUDE_XCLIP_REMOTE` in the plist).
- `Ctrl+V` direct-attach is single-PNG only; jpg/jpeg and multi-image go through the path channel.
- Relies on Claude Code's internal `xclip` call signature, so an update there can break it.

Install steps are in the Korean sections below and read fine as shell commands.
Design notes and troubleshooting: [ARCHITECTURE.md](ARCHITECTURE.md) (Korean).

</details>

## 사용법

캡쳐(⌃⌘⇧4)하면 자동으로 서버에 업로드된다 (1~2초). Claude Code 터미널에서:

| 키 | 결과 | 용도 |
|---|---|---|
| **Cmd+V** | `[Image #N]` 첨부 | Claude에게 이미지 직접 보여주기 |
| **Cmd+Shift+V** | `/tmp/clip-....png ` 경로 타이핑 | **⌘클릭으로 열어서 확인**, 경로로 참조 |

- 캡쳐 외에도 이미지를 클립보드에 복사하면(브라우저 이미지 복사 등) 똑같이 동작한다.
- Finder에서 이미지 파일을 복사(⌘C)해도 똑같이 동작한다 — 여러 장을 한 번에 복사하면 전부
  업로드되고, ⌘⇧V로 경로가 한꺼번에 타이핑된다. (여러 장 동시 첨부는 ⌘⇧V 경로 방식으로만
  확실하고, ⌘V 단일 첨부·Ctrl+V는 한 장씩.)
- 업로드 파일은 서버 `/tmp`에 있어 재부팅 때 자동 삭제 — 정리 불필요,
  오래 볼 이미지는 딴 데 저장할 것.

## 요구사항

| | 필요한 것 |
|---|---|
| 맥 | macOS + **VS Code** (Remote-SSH로 서버에 접속) |
| 에디터 | **VS Code 전용.** Cursor·VS Code Insiders·JetBrains·순수 터미널은 **아직 지원하지 않는다**. ⌘⇧V 경로 타이핑만 빠지고, 업로드와 Ctrl+V 첨부는 동작한다 |
| 서버 | Linux(+bash), 맥에서 **비밀번호 없이** ssh 접속 가능 |

## 설치법

**0) SSH 별칭 등록 (맥에서, vscode 상에서 왼쪽 하단에 이미 ssh:popups-server로 설정이 되어 있다면 스킵)**

맥 `~/.ssh/config`에 서버가 `popup-server`라는 별칭으로 등록되어 있어야 한다.

```bash
code ~/.ssh/config
```

열린 파일에 Host 부분을 popup-server로 저장한다

```
Host popup-server
    HostName 43.200.38.93
```

**1) 로컬에서**

```bash
git clone https://github.com/Sooa-Popupstudio/ssh-claude-xclip.git /tmp/ssh-claude-xclip
cd /tmp/ssh-claude-xclip

mkdir -p ~/bin && cp ssh-claude-xclip-mac.py ~/bin/
cp ai.popup.ssh-claude-xclip.plist ~/Library/LaunchAgents/
sed -i '' "s/YOURNAME/$(whoami)/" ~/Library/LaunchAgents/ai.popup.ssh-claude-xclip.plist
launchctl load ~/Library/LaunchAgents/ai.popup.ssh-claude-xclip.plist

mkdir -p ~/.vscode/extensions && cp -r ssh-claude-xclip-vscode ~/.vscode/extensions/
```

**2) 서버에서**
```bash
git clone https://github.com/Sooa-Popupstudio/ssh-claude-xclip.git ~/ssh-claude-xclip
mkdir -p ~/.local/bin
ln -sf ~/ssh-claude-xclip/ssh-claude-xclip-server ~/.local/bin/xclip

# 확인: 가짜 xclip이 진짜 xclip보다 PATH 앞에 있어야 한다
command -v xclip     # → /home/<나>/.local/bin/xclip 이 나와야 성공
```

`~/.local/bin`이 안 나오거나 `/usr/bin/xclip`이 나오면 PATH 앞순위가 아니다. `~/.bashrc`(zsh면 `~/.zshrc`)에 아래를 넣고 셸을 다시 열 것:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

설치 확인: 맥에서 캡쳐 한 번 → 서버에서 `ls -t /tmp/clip-*.png | head -1`에 방금 파일이 보이면 성공.
(안 보이면 맥의 `/tmp/ssh-claude-xclip.log`에 실패 사유가 남는다.)

**서버를 바꾸려면** `~/Library/LaunchAgents/ai.popup.ssh-claude-xclip.plist`의
`SSH_CLAUDE_XCLIP_REMOTE` 값을 고치고 데몬을 재기동한다 (launchd는 셸의 `export`를 상속하지 않아
터미널에서 환경변수를 줘도 데몬엔 닿지 않는다):

```bash
launchctl unload ~/Library/LaunchAgents/ai.popup.ssh-claude-xclip.plist
launchctl load   ~/Library/LaunchAgents/ai.popup.ssh-claude-xclip.plist
```

### 다른 서버에 붙일 때 미리 확인할 것

| 확인 | 명령 (서버에서) | 왜 |
|---|---|---|
| Linux/GNU 또는 BSD stat | `stat -c %Y /tmp \|\| stat -f %m /tmp` | 둘 다 실패하면 신선도 검사가 전부 "오래됨"이 된다 |
| bash 존재 | `command -v bash` | 가짜 xclip이 bash 전용 (`$SECONDS`, `[ -O ]`) |
| xclip 앞순위 | `command -v xclip` | 진짜 xclip이 먼저 잡히면 가로채기 실패 |
| `/tmp` 공유 | 맥에서 `ssh <서버> 'touch /tmp/t'` → Claude 터미널에서 `ls /tmp/t` | Docker/devcontainer나 systemd `PrivateTmp`면 /tmp가 분리돼 안 보인다 |
| 무인증 ssh | 맥에서 `ssh -o BatchMode=yes <서버> true` | 비밀번호·2FA 프롬프트가 뜨면 데몬이 업로드 못 한다 |

업로드하는 계정과 Claude Code를 띄우는 계정이 같아야 한다 (가짜 xclip이 자기 소유 파일만 집는다).

## 업데이트

이미 설치한 사람은 **맥에서** 아래 한 줄이면 최신으로 갱신된다 (파일 덮어쓰기 + 데몬 재기동).
실행 후 VS Code에서 `Developer: Reload Window`로 확장까지 반영:

```bash
curl -fsSL https://raw.githubusercontent.com/Sooa-Popupstudio/ssh-claude-xclip/main/ssh-claude-xclip-update | bash
```
서버 쪽(가짜 xclip)도 갱신하려면 **서버에서** 같은 한 줄을 실행하거나 `git -C ~/ssh-claude-xclip pull`.
(스크립트가 맥/서버를 자동 판별한다. plist는 사용자명이 박혀 있어 덮지 않으니, plist가 바뀐 릴리스면 그때만 수동 재설치.)


## 버전

- **v1** — 스크린샷 캡쳐(⌃⌘⇧4)나 클립보드에 복사한 이미지를 서버로 자동 업로드 →
  ⌘V로 `[Image #N]` 첨부, ⌘⇧V로 경로 타이핑. (PNG)
- **v2** — **jpg·jpeg** 지원 추가, **Finder에서 이미지 파일을 복사(⌘C)해 붙여넣기**까지 지원.
  여러 장을 한 번에 복사하면 전부 업로드되고 ⌘⇧V로 경로가 한꺼번에 타이핑된다.
