#!/usr/bin/env python3
# ssh-claude-xclip-mac.py — 맥 상주 데몬 (ssh-claude-xclip의 맥 쪽 절반)
#
# 역할 하나: 클립보드 감시. 새 이미지가 들어오면(스크린샷 캡쳐 등)
#   자동으로 서버 /tmp/clip-YYYYmmdd-HHMMSS.png 에 업로드하고,
#   클립보드에 "이미지 + 경로 텍스트"를 함께 담는다.
#   → Ctrl+V: 서버의 가짜 xclip(ssh-claude-xclip-server)이 업로드된 최신 파일을 읽어 [Image #N] 첨부
#   → Cmd+Shift+V: 미니 확장(ssh-claude-xclip-vscode)이 경로를 터미널에 타이핑 (⌘클릭으로 열림)
#
# 설정은 환경변수로: SSH_CLAUDE_XCLIP_REMOTE(기본 popup-server)
#   — launchd는 셸 환경을 상속하지 않으므로 plist의 EnvironmentVariables로 줄 것
#   — 쉼표로 여러 개를 줄 수 있다: "popup-ubuntu-dev,popup-server"
#     같은 서버라도 접속 계정이 다르면 업로드한 /tmp 파일의 주인이 달라진다.
#     어느 계정으로 작업하든 되게 하려면 쓰는 계정을 전부 나열할 것.
#     한 곳이라도 성공하면 그 캡쳐는 성공으로 친다.
# 실패 로그: /tmp/ssh-claude-xclip.log (SSH_CLAUDE_XCLIP_LOG)
# 경로 텍스트 뒤에 붙일 접미사 SSH_CLAUDE_XCLIP_PATH_SUFFIX (기본 없음 → Cmd+V 붙여넣기는 [Image #N]으로 변환됨.
# ":1"(줄번호 문법)로 주면 Cmd+V도 경로 텍스트로 남는다 — 존재하지 않는 파일명이라 변환 회피)
# 의존성 없음 (macOS 기본 python3 + osascript + ssh)
import datetime
import os
import re
import subprocess
import tempfile
import time

REMOTES = [r for r in re.split(r"[,\s]+",
           os.environ.get("SSH_CLAUDE_XCLIP_REMOTE", "popup-server").strip()) if r]

# ssh 호출 규약 — 두 가지를 반드시 지킨다.
#
# (1) BatchMode=yes 를 쓰지 않는다.
#     이 옵션은 keyboard-interactive 인증 "방식 자체"를 클라이언트가 시도조차 않게 만든다.
#     서버가 AuthenticationMethods 로 publickey+keyboard-interactive 를 요구하면,
#     TOTP가 걸려 있지 않아 프롬프트 없이 통과하는 계정까지 전부 거부당한다.
#     실제로 이것 때문에 업로드가 통째로 죽은 적이 있다.
#
# (2) 대신 askpass 를 강제로 실패시킨다.
#     데몬에는 TTY가 없어 프롬프트에 답할 사람이 없다. 그대로 두면 ssh가 멈춰 있거나,
#     최악의 경우 stdin으로 흘려보내는 "이미지 데이터"를 프롬프트가 읽어버린다.
#     ASKPASS를 /usr/bin/false로 강제하면 프롬프트가 뜨는 순간 즉시 실패로 끝난다.
#     → TOTP 없는 계정: 그대로 성공 / TOTP 있는 계정: 즉시 실패(로그에 남음)
#
# TOTP가 매번 필요한 계정까지 되게 하려면 ~/.ssh/config 의 ControlMaster 를 쓴다.
# 사람이 한 번 인증해 맺어둔 세션에 데몬이 채널만 얹으므로 재인증이 일어나지 않는다.
# 설정은 ~/.ssh/config 에서 하고 여기서 덮어쓰지 않는다 — VS Code도 같은 config를 읽어야
# 같은 소켓을 공유하기 때문이다.
SSH_CMD = ["ssh", "-o", "ConnectTimeout=10"]
SSH_ENV = dict(os.environ,
               SSH_ASKPASS="/usr/bin/false", SSH_ASKPASS_REQUIRE="force")
PATH_SUFFIX = os.environ.get("SSH_CLAUDE_XCLIP_PATH_SUFFIX", "")
LOG = os.environ.get("SSH_CLAUDE_XCLIP_LOG", "/tmp/ssh-claude-xclip.log")
FAIL_NOTIFY_GAP = float(os.environ.get("SSH_CLAUDE_XCLIP_FAIL_NOTIFY_GAP", "60"))

# Finder에서 복사(⌘C)한 "이미지 파일"만 취급 — 이 확장자들만 업로드 대상.
# 실측으로 Claude Code 첨부가 확인된 포맷만 둔다 (png/jpg 확인, heic는 미첨부라 제외).
IMG_EXTS = (".png", ".jpg", ".jpeg")

# ── JXA 조각들 ──────────────────────────────────────────────
JXA_COUNT = "ObjC.import('AppKit'); $.NSPasteboard.generalPasteboard.changeCount"

# 클립보드의 이미지 "데이터"를 PNG 파일로 저장 (파일 복사는 watcher (1)분기에서 먼저
# 처리하므로, 여기 도달하는 file-url은 이미지 아닌 파일이라 SKIP)
JXA_GRAB = """
ObjC.import('AppKit')
function run(argv) {
  const pb = $.NSPasteboard.generalPasteboard
  if (!pb.stringForType('public.file-url').isNil()) return 'SKIP'
  let data = pb.dataForType('public.png')
  if (data.isNil()) {
    const tiff = pb.dataForType('public.tiff')
    if (tiff.isNil()) return 'SKIP'
    const rep = $.NSBitmapImageRep.imageRepWithData(tiff)
    data = rep.representationUsingTypeProperties(4, $.NSDictionary.dictionary)
    if (data.isNil()) return 'SKIP'
  }
  return data.writeToFileAtomically(argv[0], true) ? 'OK' : 'SKIP'
}
"""

# 클립보드를 "이미지 + 경로 텍스트" 두 표현으로 다시 채움 → 새 changeCount 반환
JXA_REWRITE = """
ObjC.import('AppKit')
function run(argv) {
  const pb = $.NSPasteboard.generalPasteboard
  const data = $.NSData.dataWithContentsOfFile(argv[0])
  pb.clearContents
  pb.setDataForType(data, 'public.png')
  pb.setStringForType(argv[1], 'public.utf8-plain-text')
  return String(pb.changeCount)
}
"""

# 클립보드에 담긴 파일 URL을 "전부" 읽어 로컬 경로 목록(줄바꿈 구분)으로 반환.
# stringForType로 첫 항목만 보던 걸 pasteboardItems(item 배열) 순회로 바꿔 N개를 다 집는다.
JXA_FILE_URLS = """
ObjC.import('AppKit')
function run() {
  const pb = $.NSPasteboard.generalPasteboard
  const items = pb.pasteboardItems
  if (items.isNil()) return ''
  const out = []
  for (let i = 0; i < items.count; i++) {
    const u = items.objectAtIndex(i).stringForType('public.file-url')
    if (u.isNil()) continue
    const url = $.NSURL.URLWithString(u)
    if (url.isNil() || url.path.isNil()) continue
    out.push(ObjC.unwrap(url.path))
  }
  return out.join('\\n')
}
"""

# 클립보드를 "경로 텍스트(여러 줄)"만으로 다시 채움 → 새 changeCount 반환.
# 파일 복사는 원본이 이미 파일이라 이미지 데이터를 다시 넣을 필요 없이 경로만 남긴다.
JXA_SET_TEXT = """
ObjC.import('AppKit')
function run(argv) {
  const pb = $.NSPasteboard.generalPasteboard
  pb.clearContents
  pb.setStringForType(argv[0], 'public.utf8-plain-text')
  return String(pb.changeCount)
}
"""

def jxa(script, *args):
    r = subprocess.run(["osascript", "-l", "JavaScript", "-e", script, *args],
                       capture_output=True, text=True, timeout=15)
    return r.stdout.strip()

def notify(msg):
    # 따옴표가 섞이면 osascript 문자열이 깨지므로 제거
    msg = msg.replace('"', "'").replace("\\", "/")
    subprocess.run(["osascript", "-e",
                    f'display notification "{msg}" with title "ssh-claude-xclip"'],
                   capture_output=True, timeout=5)

_last_fail = 0.0

def log(tag, msg, detail=""):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + f" [{tag}] {msg}"
    if detail:
        line += "\n    " + detail.strip().replace("\n", "\n    ")
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def fail(msg, detail=""):
    # 업로드 실패는 반드시 보이게 한다 — 예전엔 except로 통째로 삼켜서 "왜 안 되지"만 남았다.
    # 로그는 매번, 맥 알림은 FAIL_NOTIFY_GAP초에 한 번만 (서버가 꺼져 있으면 캡쳐마다 뜨므로).
    global _last_fail
    log("FAIL", msg, detail)
    now = time.time()
    if now - _last_fail >= FAIL_NOTIFY_GAP:
        _last_fail = now
        notify(msg)

def upload_all(pairs):
    """pairs: [(로컬경로, 원격경로), ...] 를 REMOTES "전부"에 병렬 업로드.

    장(pair) × 서버(REMOTES) 를 전부 동시에 띄우고 한꺼번에 기다린다. 순차로 돌면
    장수 × 서버수 만큼 걸리지만, 병렬이면 대략 한 장 올리는 시간이면 끝난다.

    성공 판정은 서버별이 아니라 "장별"이다 — 한 서버라도 올라갔으면 그 장은 성공.
    어느 계정으로 접속해 작업 중인지 데몬은 알 수 없으니 일단 전부에 뿌려두고,
    쓸 수 있는 곳에서 쓰게 한다. 그래서 TOTP로 막힌 계정이 섞여 있어도
    나머지 계정의 붙여넣기는 멀쩡히 동작한다.

    반환: 한 곳 이상 성공한 원격경로를, 넘어온 순서 그대로.
    """
    jobs = []
    for lp, rpath in pairs:
        for remote in REMOTES:
            fh = open(lp, "rb")  # 서버마다 따로 읽어야 하므로 핸들도 따로
            p = subprocess.Popen(SSH_CMD + [remote, f"cat > {rpath}"],
                                 stdin=fh, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE, text=True, env=SSH_ENV)
            jobs.append((p, fh, rpath, remote))

    ok, errs = set(), []
    for p, fh, rpath, remote in jobs:
        try:
            _, err = p.communicate(timeout=60)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            p.kill()
            rc, err = 1, "시간초과(60초) — ssh가 응답/인증 대기 중일 수 있음"
        fh.close()
        if rc == 0:
            ok.add(rpath)
        else:
            errs.append(f"{remote} ← {os.path.basename(rpath)}: {(err or '').strip()}")

    lost = [rp for _, rp in pairs if rp not in ok]
    if lost:
        # 모든 서버에서 실패한 장이 있다 — 붙여넣기가 실제로 안 되는 상황이라 알림까지.
        fail(f"업로드 실패 {len(lost)}/{len(pairs)}장 — 대상 서버 전부 실패",
             "\n".join(errs))
    elif errs:
        # 일부 서버만 실패. 다른 서버로는 붙여넣기가 되므로 알림 없이 로그만 남긴다.
        # (TOTP 걸린 계정에 ControlMaster 세션이 없을 때가 주로 여기로 온다.)
        log("WARN", f"일부 서버 업로드 실패 ({len(errs)}건) — 나머지 서버는 성공",
            "\n".join(errs))
    return [rp for _, rp in pairs if rp in ok]

# ── 클립보드 감시 → 자동 업로드 + 경로 병기 ─────────────────
def watcher():
    last = jxa(JXA_COUNT)
    while True:
        time.sleep(0.8)
        try:
            cur = jxa(JXA_COUNT)
            if not cur or cur == last:
                continue
            last = cur

            # (1) Finder에서 이미지 "파일"을 복사(⌘C) — 여러 개면 전부 업로드하고
            #     경로를 여러 줄로 병기 (⌘⇧V가 다 타이핑, ⌘V는 텍스트로 붙음)
            files = [p for p in jxa(JXA_FILE_URLS).splitlines()
                     if p.lower().endswith(IMG_EXTS) and os.path.isfile(p)]
            if files:
                ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                pairs = [(lp, f"/tmp/clip-{ts}-{i}{os.path.splitext(lp)[1].lower()}")
                         for i, lp in enumerate(files)]
                rpaths = upload_all(pairs)  # 장 × 서버를 전부 동시에 올림
                if rpaths:
                    text = "\n".join(rp + PATH_SUFFIX for rp in rpaths)
                    last = jxa(JXA_SET_TEXT, text) or last
                continue

            # (2) 이미지 "데이터"가 클립보드에 (스크린샷 캡쳐, 브라우저 이미지 복사 등)
            #     — 기존 단일 흐름: 임시 PNG로 뽑아 업로드하고 이미지+경로 병기
            tmp = tempfile.mktemp(suffix=".png")
            if jxa(JXA_GRAB, tmp) != "OK":
                continue
            name = "clip-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".png"
            rpath = f"/tmp/{name}"
            if upload_all([(tmp, rpath)]):
                # 클립보드에 이미지 + 경로 텍스트를 함께 담고, 우리가 만든 변경은 무시
                last = jxa(JXA_REWRITE, tmp, rpath + PATH_SUFFIX) or last
            # 업로드 실패면 클립보드를 건드리지 않는다 — 원본 이미지가 그대로 남아
            # 로컬 붙여넣기는 살아 있고, 실패는 fail()이 알림/로그로 보고한다.
            os.unlink(tmp)
        except Exception as e:
            fail(f"데몬 오류: {type(e).__name__}", str(e))

if __name__ == "__main__":
    watcher()
