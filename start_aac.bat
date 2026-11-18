@echo off
rem ============================================================================
rem  Start the AAC system: opens the Flutter app window. The app itself starts
rem  the AI backend (backend\main.py) and the eye tracker (tracker_service.py)
rem  and stops them again when you close the window.
rem  The app is rebuilt only when its code changed since the last build.
rem  Usage:  start_aac.bat            normal start
rem          start_aac.bat rebuild    force a fresh build
rem ============================================================================
setlocal
set "ROOT=%~dp0"
set "FRONT=%ROOT%frontend"
set "EXE=%FRONT%\build\windows\x64\runner\Release\synaptalk.exe"
set "AAC_SYSTEM_ROOT=%ROOT:~0,-1%"

where python >nul 2>nul || (echo [!] Python was not found on PATH. The eye tracker needs it. & pause & exit /b 1)

rem ---- build if needed --------------------------------------------------------
set "NEED_BUILD=0"
if /i "%~1"=="rebuild" set "NEED_BUILD=1"
if not exist "%EXE%" set "NEED_BUILD=1"
if "%NEED_BUILD%"=="0" (
  powershell -NoProfile -Command ^
    "$t=(Get-Item -LiteralPath $env:EXE).LastWriteTime; $n=(Get-ChildItem -LiteralPath (Join-Path $env:FRONT 'lib'),(Join-Path $env:FRONT 'pubspec.yaml') -Recurse -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime; if ($n -gt $t) { exit 1 } else { exit 0 }"
  if errorlevel 1 set "NEED_BUILD=1"
)

if "%NEED_BUILD%"=="0" goto launch
where flutter >nul 2>nul || (echo [!] Flutter was not found on PATH, cannot build the app. & pause & exit /b 1)
echo Building the AAC app ^(only needed after code changes, about 1-3 minutes^)...
pushd "%FRONT%"
call flutter build windows --release
if errorlevel 1 (
  rem the 2nd try starts from a clean build folder: this fixes a briefly locked file AND old cached build
  rem settings, e.g. "generator ... does not match the generator used previously" after a Visual Studio
  rem install. Only generated build output is removed (frontend\build\windows), never code or settings.
  echo First build attempt failed, cleaning the old build files and trying once more...
  if exist "%FRONT%\build\windows" rmdir /s /q "%FRONT%\build\windows"
  call flutter build windows --release
)
if errorlevel 1 (
  popd
  echo [!] The build failed twice, see the messages above. The app was NOT started.
  pause
  exit /b 1
)
popd

:launch
if defined AAC_NO_LAUNCH (echo Build check done, not launching. & exit /b 0)

rem ---- launch -----------------------------------------------------------------
echo Starting the AAC app. The AI backend and the eye tracker start with it
echo ^(logs: "%ROOT%logs"^).
start "" /D "%ROOT%" "%EXE%"
endlocal
exit /b 0
