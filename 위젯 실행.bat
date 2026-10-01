@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYW="
set "PY_VER=3.12.10"
set "PY_URL=https://www.python.org/ftp/python/%PY_VER%/python-%PY_VER%-amd64.exe"

rem ── 1) PATH에 있는 Python 확인 (Microsoft Store 가짜 바로가기는 실행이 실패해서 자동으로 걸러짐)
for /f "delims=" %%P in ('python -c "import sys, os, tkinter; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))" 2^>nul') do set "PYW=%%P"
if defined PYW if exist "%PYW%" goto run
set "PYW="

rem ── 2) 사용자 폴더에 설치된 Python 확인 (PATH 등록이 안 된 경우)
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\pythonw.exe" set "PYW=%%D\pythonw.exe"
if defined PYW goto run

rem ── 3) 없으면 자동으로 내려받아 설치 (관리자 권한 불필요, 현재 사용자에게만 설치)
echo.
echo  Python이 설치되어 있지 않아 자동으로 설치합니다.
echo  (약 25MB, 1~2분 정도 걸립니다. 창을 닫지 마세요)
echo.
set "INST=%TEMP%\python-%PY_VER%-amd64.exe"
echo  [1/2] 다운로드 중...
curl -L --fail --silent --show-error -o "%INST%" "%PY_URL%"
if errorlevel 1 goto fail
echo  [2/2] 설치 중...
"%INST%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=0 Include_test=0 Include_doc=0
if errorlevel 1 goto fail
del "%INST%" >nul 2>&1
set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not exist "%PYW%" goto fail
echo  설치 완료! 위젯을 실행합니다.

:run
start "" "%PYW%" "%~dp0stock_widget.pyw"
exit /b 0

:fail
del "%INST%" >nul 2>&1
echo.
echo  Python 자동 설치에 실패했습니다. 인터넷 연결을 확인한 뒤 다시 실행하거나,
echo  https://www.python.org/downloads/ 에서 직접 설치해 주세요.
echo.
pause
exit /b 1
