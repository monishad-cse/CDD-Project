@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo             EduTrack Launcher
echo ==========================================

where docker >nul 2>nul
if errorlevel 1 (
  echo ERROR: Docker CLI was not found. Install Docker Desktop first.
  pause
  exit /b 1
)

docker info >nul 2>nul
if errorlevel 1 (
  echo ERROR: Docker Desktop is installed but its engine is not running.
  echo Start Docker Desktop, wait until it says Engine running, then retry.
  pause
  exit /b 1
)

if not exist .env (
  copy .env.example .env >nul
  echo Created .env from .env.example.
  echo For a local demo, the example values will run, but change all passwords
  echo before exposing this application to the internet.
)

echo Building and starting EduTrack containers...
docker compose up --build -d
if errorlevel 1 (
  echo.
  echo EduTrack did not start. Review the error above.
  echo Useful command: docker compose logs --tail=100
  pause
  exit /b 1
)

echo.
echo EduTrack containers:
docker compose ps
echo.
echo React landing page: http://localhost:8080
echo Sign in through the React website at http://localhost:8080
echo FastAPI docs: http://localhost:8080/api/docs
echo API health: http://localhost:8080/api/health
echo Stop the application with: docker compose down
pause
