# CD — release 브랜치 자동 배포

프로덕션(이 맥)은 `main` 이 아니라 **`release` 브랜치**에서만 배포한다. 운영 중 개발로 인한 사고를
막기 위해, 개발·검증은 `main`, 배포는 `release` 로 분리한다.

## 흐름

```
feature 브랜치 ─PR→ main (CI: lint+test)         ← 개발·검증
                     │
                     └─ release 로 병합 ─push→ CD (self-hosted runner)  ← 배포
                                                 └ diff 로 worker/api/web 중 변경분만
                                                   git pull + build + launchctl/docker 재시작
```

배포 대상은 `scripts/deploy.sh` 가 diff 로 판단한다(worker=docker 재빌드, api/web=launchd 재시작).

## release 로 배포하기

```bash
# main 의 검증된 변경을 release 로 올린다
git checkout release && git pull
git merge --ff-only main        # 또는 특정 커밋까지: git merge --ff-only <sha>
git push origin release         # → CD 워크플로 트리거
```

되돌리기(롤백): `release` 를 직전 배포 커밋으로 리셋 후 강제 push 하면 그 커밋으로 재배포된다.

```bash
git checkout release && git reset --hard <good-sha> && git push -f origin release
```

수동 재배포/특정 대상만: GitHub → Actions → CD → **Run workflow**, `targets` 에 `api web worker` 중 입력.

## self-hosted runner 설치 (프로덕션 맥에서 1회)

GitHub Actions 클라우드 러너는 이 맥에 접근할 수 없으므로, 맥에 self-hosted runner 를 등록한다.

1. GitHub → repo **Settings → Actions → Runners → New self-hosted runner** (macOS) 에서
   등록 토큰과 명령을 받는다.
2. 아래처럼 설치한다(라벨을 `reporter-prod` 로 지정 — 워크플로가 이 라벨로 잡는다):

   ```bash
   mkdir -p ~/actions-runner && cd ~/actions-runner
   # GitHub 이 준 다운로드 명령 실행(버전은 UI 기준)
   curl -o actions-runner-osx.tar.gz -L <UI가_준_URL>
   tar xzf actions-runner-osx.tar.gz
   ./config.sh --url https://github.com/solduma/reporter \
     --token <UI가_준_토큰> \
     --labels reporter-prod \
     --name reporter-prod-mac
   ```

3. 로그인 세션(Aqua)에서 상시 구동 — `launchctl` 이 GUI 도메인을 쓰므로 **로그인 사용자**로 돌려야 한다:

   ```bash
   ./svc.sh install       # LaunchAgent 로 등록(현재 사용자)
   ./svc.sh start
   ```

   > 주의: runner 를 `sudo`/시스템 데몬으로 돌리면 `launchctl kickstart gui/<uid>/...` 가
   > 세션 밖이라 실패한다. 반드시 로그인 사용자 세션에서 구동할 것.

4. Docker Desktop 이 실행 중이어야 worker 재빌드가 된다(로그인 시 자동 시작 권장).

## 확인

- runner 온라인: repo → Settings → Actions → Runners 에 `reporter-prod-mac` Idle 표시
- 배포 로그: repo → Actions → CD → 해당 run
- 로컬 서비스: `launchctl list | grep com.reporter.server`, `docker ps | grep reporter-worker`
