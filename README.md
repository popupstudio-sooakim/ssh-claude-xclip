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

macOS + **VS Code** (Remote-SSH), a Linux server with `bash`, and non-interactive SSH from the Mac.
Hostnames, aliases and accounts throughout the docs are Popup Studio's own — swap in yours.
Uploads and Claude Code must run as the **same account**, sharing the **same `/tmp`** (a container
or systemd `PrivateTmp` breaks this).

### Known limits

- **VS Code only.** Cursor and VS Code Insiders install extensions elsewhere, so `Cmd+Shift+V` is
  missing there; uploads and `Ctrl+V` still work.
- Targets are set by `SSH_CLAUDE_XCLIP_REMOTE` in the plist (comma-separated for several
  accounts/hosts at once). A server demanding 2FA on every connection needs an SSH
  `ControlMaster` session to piggyback on.
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
| 서버 | Linux(+bash), 맥에서 **사람 개입 없이** ssh 접속 가능 (키 인증. 2FA가 걸린 서버는 [아래](#2fatotp가-걸린-서버) 참고) |

---

## ⚠️ 먼저: 이 문서에 박혀 있는 "우리 조직 값"

아래 예시들은 Popup Studio 내부 환경 기준이다. **외부에서 쓰는 사람은 전부 자기 값으로 바꿔야 한다.**

| 이 문서의 값 | 정체 | 당신이 할 일 |
|---|---|---|
| `popup-server`, `popup-ubuntu-dev` | 우리 `~/.ssh/config`의 **Host 별칭** | 당신이 쓰는 별칭으로 교체 |
| `43.200.38.93` | 우리 개발 서버 IP | 당신 서버 주소로 교체 |
| `sooa`, `ubuntu` | 우리가 쓰는 서버 계정 | 당신 계정으로 교체 |
| `popupstudio-sooakim/ssh-claude-xclip` | 이 저장소 | 포크했다면 당신 저장소로 교체 |

별칭 이름 자체는 뭐든 상관없다. **맥 `~/.ssh/config`의 Host 이름과 plist의
`SSH_CLAUDE_XCLIP_REMOTE` 값이 서로 같기만 하면 된다.**

---

## 설치법

**0) SSH 별칭 + 키 인증 (맥에서)**

데몬은 사람이 없는 상태에서 `ssh <별칭> 'cat > /tmp/...'`를 쏜다. 그래서 비밀번호를
묻지 않고 바로 들어가지는 상태여야 한다.

`~/.ssh/config`를 열고:

```bash
code ~/.ssh/config
```

Host 블록을 추가한다 (**값은 당신 서버 것으로**):

```
Host my-server              # ← 별칭. 아무 이름이나. plist에 적을 이름과 같아야 함
    HostName 10.0.0.5       # ← 당신 서버 주소
    User myaccount          # ← 당신 서버 계정
    IdentityFile ~/.ssh/id_ed25519
```

<details>
<summary>참고: Popup Studio 내부 설정은 이렇게 되어 있다</summary>

```
Host popup-server
    HostName 43.200.38.93
    User sooa
    IdentityFile ~/.ssh/id_ed25519

Host popup-ubuntu-dev
    HostName 43.200.38.93
    User ubuntu
    IdentityFile ~/.ssh/popupstudio-ubuntu-sooa
```

VS Code 왼쪽 아래에 이미 `SSH: popup-server` 같은 게 떠 있다면 별칭은 이미 등록된 것이다.
</details>

아직 키가 없다면 만들어서 서버에 등록한다:

```bash
ls ~/.ssh/id_ed25519 || ssh-keygen -t ed25519 -C "$(whoami)@$(hostname)"
ssh-copy-id my-server        # ← 여기서 한 번은 비밀번호를 묻는다
```

확인 — **아무것도 묻지 않고** `OK`가 떠야 한다:

```bash
ssh my-server 'echo OK'
```

> 비밀번호나 인증 코드를 묻는다면 아직 설치하면 안 된다. 2FA가 걸린 서버라면
> [아래 절](#2fatotp가-걸린-서버)을 먼저 볼 것.

**1) 맥에서**

```bash
git clone https://github.com/popupstudio-sooakim/ssh-claude-xclip.git /tmp/ssh-claude-xclip
cd /tmp/ssh-claude-xclip

mkdir -p ~/bin && cp ssh-claude-xclip-mac.py ~/bin/
cp ai.popup.ssh-claude-xclip.plist ~/Library/LaunchAgents/
sed -i '' "s/YOURNAME/$(whoami)/" ~/Library/LaunchAgents/ai.popup.ssh-claude-xclip.plist

mkdir -p ~/.vscode/extensions && cp -r ssh-claude-xclip-vscode ~/.vscode/extensions/
```

**업로드 대상을 당신 별칭으로 바꾼다.** plist에는 `popup-server`가 기본값으로 박혀 있어서,
이걸 안 고치면 존재하지 않는 호스트로 업로드를 시도하게 된다:

```bash
# my-server 자리에 0번에서 정한 별칭을 넣을 것
sed -i '' "s|<string>popup-server</string>|<string>my-server</string>|" \
  ~/Library/LaunchAgents/ai.popup.ssh-claude-xclip.plist

launchctl load ~/Library/LaunchAgents/ai.popup.ssh-claude-xclip.plist
```

그리고 VS Code에서 `Cmd+Shift+P` → `Developer: Reload Window` (⌘⇧V 확장 반영).

**2) 서버에서**
```bash
git clone https://github.com/popupstudio-sooakim/ssh-claude-xclip.git ~/ssh-claude-xclip
mkdir -p ~/.local/bin
ln -sf ~/ssh-claude-xclip/ssh-claude-xclip-server ~/.local/bin/xclip

# 확인: 가짜 xclip이 진짜 xclip보다 PATH 앞에 있어야 한다
command -v xclip     # → /home/<나>/.local/bin/xclip 이 나와야 성공
```

`~/.local/bin`이 안 나오거나 `/usr/bin/xclip`이 나오면 PATH 앞순위가 아니다. `~/.bashrc`(zsh면 `~/.zshrc`)에 아래를 넣고 셸을 다시 열 것:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

**3) 설치 확인**

맥에서 캡쳐 한 번(⌃⌘⇧4) → 서버에서:

```bash
ls -t /tmp/clip-*.png | head -1      # 방금 파일이 보이면 성공
```

안 보이면 맥의 `/tmp/ssh-claude-xclip.log`에 실패 사유가 남는다.

---

## 여러 계정·여러 서버에 동시에 올리기

`SSH_CLAUDE_XCLIP_REMOTE`에 **쉼표로 여러 별칭**을 줄 수 있다. 전부에 병렬로 업로드하고,
한 곳이라도 성공하면 그 캡쳐는 성공으로 친다.

```xml
<key>SSH_CLAUDE_XCLIP_REMOTE</key><string>popup-ubuntu-dev,popup-server</string>
```

왜 필요하냐면 — **같은 서버라도 접속 계정이 다르면 업로드한 `/tmp` 파일의 주인이 달라진다.**
가짜 xclip은 자기 소유 파일만 집으므로, `sooa`로 올려둔 파일은 `ubuntu`로 띄운 Claude Code가
집지 못한다. 그날그날 어느 계정으로 붙을지 데몬은 알 수 없으니, **쓰는 계정을 전부 나열해두면**
어느 쪽으로 접속하든 붙여넣기가 된다.

> 위 예시의 두 별칭은 우리 조직 값이다. 당신이 쓰는 별칭으로 바꿀 것.

**바꾸고 나면 데몬 재기동**이 필요하다. launchd는 셸의 `export`를 상속하지 않아서,
터미널에서 환경변수를 줘봐야 데몬엔 닿지 않는다:

```bash
launchctl unload ~/Library/LaunchAgents/ai.popup.ssh-claude-xclip.plist
launchctl load   ~/Library/LaunchAgents/ai.popup.ssh-claude-xclip.plist
```

---

## 2FA/TOTP가 걸린 서버

서버가 `AuthenticationMethods publickey,keyboard-interactive`로 **매 접속마다 인증 코드를
요구**하면, 사람이 없는 데몬은 코드를 넣을 수 없어 업로드가 전부 막힌다.

서버 설정을 못 바꾸는 상황이라면 **ControlMaster(연결 다중화)** 로 해결한다. 사람이 VS Code로
한 번 인증해 맺어둔 세션을 데몬이 재사용하는 방식이다 — 재인증이 아니라, 이미 열려 있는
연결 안에 채널만 하나 더 여는 것이라 인증 절차 자체가 일어나지 않는다.

맥 `~/.ssh/config`의 해당 Host에 세 줄을 추가한다:

```
Host my-server
    HostName 10.0.0.5
    User myaccount
    ControlMaster auto
    ControlPath ~/.ssh/cm-%C
    ControlPersist 8h
```

VS Code Remote-SSH도 같은 `~/.ssh/config`를 읽는 시스템 `ssh`를 쓰므로, 설정 후
**VS Code를 한 번 다시 연결**하면 그 세션이 공유 가능한 master가 된다.

알고 있어야 할 것:

- **master가 죽으면 같이 막힌다.** VS Code 연결을 끊으면 `ControlPersist` 시간(위 예시 8시간)
  뒤에 소켓이 닫히고, 그 뒤로는 다시 업로드가 실패한다. 실질적으로 "아침에 한 번 접속하면
  그날은 동작"하는 모델이다.
- **소켓은 별칭이 아니라 `계정@호스트:포트`로 매칭된다.** 별칭이 둘이어도 접속 계정이 같으면
  소켓을 공유하고, 계정이 다르면 각각 따로 인증된 세션이 있어야 한다.
- **보안상 트레이드오프가 있다.** 소켓이 열려 있는 동안은 맥의 당신 계정에서 도는 프로세스가
  인증 코드 없이 서버에 붙을 수 있다. 2FA를 의무화한 조직이라면 관리자와 상의하고 쓸 것.

---

### 다른 서버에 붙일 때 미리 확인할 것

| 확인 | 명령 (서버에서) | 왜 |
|---|---|---|
| Linux/GNU 또는 BSD stat | `stat -c %Y /tmp \|\| stat -f %m /tmp` | 둘 다 실패하면 신선도 검사가 전부 "오래됨"이 된다 |
| bash 존재 | `command -v bash` | 가짜 xclip이 bash 전용 (`$SECONDS`, `[ -O ]`) |
| xclip 앞순위 | `command -v xclip` | 진짜 xclip이 먼저 잡히면 가로채기 실패 |
| `/tmp` 공유 | 맥에서 `ssh <별칭> 'touch /tmp/t'` → Claude 터미널에서 `ls /tmp/t` | Docker/devcontainer나 systemd `PrivateTmp`면 /tmp가 분리돼 안 보인다 |

무인증 ssh 확인은 **맥에서** 아래처럼 한다:

```bash
ssh -o ConnectTimeout=10 <별칭> true </dev/null && echo "무인증 OK"
```

> `ssh -o BatchMode=yes` 로 확인하지 말 것. BatchMode는 keyboard-interactive 인증 **방식
> 자체를 시도조차 않게** 만들어서, 서버가 `publickey,keyboard-interactive`를 요구하는 경우
> **TOTP가 걸려 있지 않아 원래는 통과할 계정까지** 거부로 나온다. 멀쩡한 서버를 "2FA 때문에
> 안 된다"고 오진하게 된다. (데몬도 같은 이유로 BatchMode를 쓰지 않는다.)

**업로드하는 계정과 Claude Code를 띄우는 계정이 같아야 한다** — 가짜 xclip이 자기 소유 파일만
집기 때문이다. 계정이 여러 개라면 위의 [여러 계정·여러 서버에 동시에 올리기](#여러-계정여러-서버에-동시에-올리기)를 쓸 것.

## 업데이트

이미 설치한 사람은 **맥에서** 아래 한 줄이면 최신으로 갱신된다 (파일 덮어쓰기 + 데몬 재기동).
실행 후 VS Code에서 `Developer: Reload Window`로 확장까지 반영:

```bash
curl -fsSL https://raw.githubusercontent.com/popupstudio-sooakim/ssh-claude-xclip/main/ssh-claude-xclip-update | bash
```
서버 쪽(가짜 xclip)도 갱신하려면 **서버에서** 같은 한 줄을 실행하거나 `git -C ~/ssh-claude-xclip pull`.
(스크립트가 맥/서버를 자동 판별한다. plist는 사용자명이 박혀 있어 덮지 않으니, plist가 바뀐 릴리스면 그때만 수동 재설치.)


## 버전

- **v1** — 스크린샷 캡쳐(⌃⌘⇧4)나 클립보드에 복사한 이미지를 서버로 자동 업로드 →
  ⌘V로 `[Image #N]` 첨부, ⌘⇧V로 경로 타이핑. (PNG)
- **v2** — **jpg·jpeg** 지원 추가, **Finder에서 이미지 파일을 복사(⌘C)해 붙여넣기**까지 지원.
  여러 장을 한 번에 복사하면 전부 업로드되고 ⌘⇧V로 경로가 한꺼번에 타이핑된다.
