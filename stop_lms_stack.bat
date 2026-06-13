@echo off
setlocal

cd /d "%~dp0"

echo Stopping LMS stack from %CD%
echo.

echo Stopping Django and Celery Python processes for this project...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$procs = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and ($_.CommandLine -match 'D:\\LMSPROJECT\\venv\\Scripts\\python.exe\" manage.py runserver' -or $_.CommandLine -match 'D:\\LMSPROJECT\\venv\\Scripts\\python.exe -m celery -A lms_project worker') }; foreach ($p in $procs) { try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; Write-Host ('Stopped PID ' + $p.ProcessId) } catch { Write-Host ('Could not stop PID ' + $p.ProcessId) } }"

echo Stopping Redis container if present...
where docker >nul 2>nul
if %errorlevel%==0 (
  docker ps -a --format "{{.Names}}" | findstr /i "^lms-redis$" >nul
  if %errorlevel%==0 (
    docker stop lms-redis >nul 2>nul
  )
)

echo.
echo LMS stop request complete.

endlocal
