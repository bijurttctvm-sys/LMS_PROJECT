@echo off
setlocal

cd /d "%~dp0"

echo Starting LMS stack from %CD%
echo.

where docker >nul 2>nul
if %errorlevel%==0 (
  docker ps --format "{{.Names}}" | findstr /i "^lms-redis$" >nul
  if %errorlevel% neq 0 (
    echo Starting Redis via Docker...
    docker run -d -p 6379:6379 --name lms-redis redis:alpine >nul 2>nul
    if %errorlevel% neq 0 (
      echo Redis container already exists. Attempting to start it...
      docker start lms-redis >nul 2>nul
    )
  ) else (
    echo Redis container is already running.
  )
  echo.
) else (
  echo Docker not found. Start Redis manually if it is not already running on port 6379.
  echo.
)

echo Starting Celery worker in a new window...
start "LMS Celery Worker" cmd /k ".\venv\Scripts\python.exe -m celery -A lms_project worker -l info -P solo"

echo Starting Django development server in a new window...
start "LMS Django Server" cmd /k ".\venv\Scripts\python.exe manage.py runserver"

echo.
echo LMS stack launch requested.
echo.
echo Expected windows:
echo 1. Redis (via Docker if available)
echo 2. Celery worker
echo 3. Django runserver
echo.
echo If quiz/content processing still stalls, verify Celery logs for task pickup errors.

endlocal
