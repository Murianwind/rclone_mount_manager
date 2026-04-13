[![pytest](https://github.com/Murianwind/rclone_mount_manager/actions/workflows/run-tests.yml/badge.svg)](https://github.com/Murianwind/rclone_mount_manager/actions/workflows/run-tests.yml)
[![codecov](https://codecov.io/gh/Murianwind/rclone_mount_manager/branch/main/graph/badge.svg)](https://codecov.io/gh/Murianwind/rclone_mount_manager)

# 🚀 RcloneManager

[](https://www.google.com/search?q=https://github.com/Murianwind/rclone_mount_manager/actions/workflows/run-tests.yml)
[](https://github.com/Murianwind/rclone_mount_manager)

**RcloneManager**는 복잡한 `rclone` 마운트 명령어를 GUI 환경에서 직관적으로 관리할 수 있게 돕는 Windows 전용 트레이 애플리케이션입니다. 번거로운 터미널 입력 없이 클릭 몇 번으로 클라우드 스토리지를 내 컴퓨터의 드라이브처럼 사용하세요.

-----

## ✨ 주요 기능

  * **GUI 기반 마운트 관리**: 등록된 리모트를 불러와 서브 디렉토리, 드라이브 문자, 볼륨 이름을 손쉽게 설정합니다.
  * **VFS 캐시 최적화**: `off`, `minimal`, `writes`, `full` 등 다양한 캐시 모드를 개별 설정할 수 있습니다.
  * **지능형 rclone 관리**: 최신 버전의 `rclone.exe`를 체크하고 자동으로 설치하거나 기존 경로를 등록할 수 있습니다.
  * **Windows 시스템 통합**:
      * **시스템 트레이**: 백그라운드에서 실행되며 트레이 아이콘을 통해 빠른 접근이 가능합니다.
      * **자동 시작**: Windows 시작 프로그램 등록/해제 기능을 제공합니다.
  * **중복 실행 방지**: 이미 앱이 실행 중이라면 기존 창을 활성화하여 리소스 낭비를 방지합니다.

-----

## 🚀 시작하기

### 1\. 설치 및 실행

1.  [Releases](https://www.google.com/search?q=https://github.com/Murianwind/rclone_mount_manager/releases) 페이지에서 최신 버전의 압축 파일을 다운로드합니다.
2.  압축을 해제한 후 `RcloneManager.exe` 파일을 실행합니다.

### 2\. rclone 및 리모트 설정

1.  **rclone 등록**: 앱 상단의 'rclone 경로' 입력창에 기존에 설치된 `rclone.exe` 경로를 지정하거나, 버전 라벨을 클릭하여 최신 버전을 자동으로 설치합니다.
2.  **리모트 구성**: 터미널(CMD/PowerShell)에서 `rclone config` 명령어를 사용하여 클라우드 리모트 설정을 완료합니다.
3.  **설정 연동**: 생성된 `rclone.conf`를 프로그램에 추가한 후 등록된 리모트 이름을 확인하고, 앱의 `+ 추가` 버튼을 통해 마운트 설정을 진행합니다.

-----

## ⚙️ 마운트 설정 상세

마운트 추가/편집 시 다음 항목을 설정할 수 있습니다.

  * **리모트 이름**: `rclone.conf`에 설정된 리모트 이름을 정확히 입력합니다.
  * **서브 디렉토리**: 특정 폴더만 마운트하고 싶은 경우 경로를 입력합니다 (예: `MyFiles/Backup`). 비워두면 루트 전체가 마운트됩니다.
  * **드라이브 문자**: `Z:`, `Y:` 등 마운트될 드라이브 문자를 선택합니다. (빈칸으로 둘 경우 rclone 기본 설정에 따릅니다.)
  * **캐시 디렉토리**: VFS 캐시가 저장될 로컬 경로를 지정합니다.
  * **캐시 모드**: 성능과 안정성에 따라 `full`(권장), `writes` 등을 선택합니다.
  * **추가 플래그**: rclone의 다양한 옵션을 직접 입력할 수 있습니다 (구분자: `;` 또는 줄바꿈).
  * **시작 시 자동 마운트**: 체크 시 앱이 실행될 때 해당 리모트를 자동으로 마운트합니다.

-----

## 📂 설정 관리

앱과 동일한 경로에 `mounts.json` 파일이 생성되어 사용자가 설정한 마운트 목록과 환경설정 값이 저장됩니다.

-----

## 🐞 이슈 리포트

앱 내의 `!` 버튼을 클릭하면 현재 앱 버전 및 시스템 정보가 포함된 이슈 작성 페이지로 연결됩니다. 사용 중 불편한 점이나 결함 발견 시 제보 부탁드립니다.
