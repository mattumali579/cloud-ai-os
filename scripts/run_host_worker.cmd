@echo off
REM Cloud AI OS host worker.
REM Runs OUTSIDE Docker on purpose: task.run shells out to the Claude Code CLI,
REM whose subscription credentials live in the Windows user profile.
setlocal
set "DATABASE_URL=postgresql://cloudos:cloudos@localhost:5432/cloudos"
set "INSTANCE_NAME=host-worker"
set "WORKER_POLL_INTERVAL_SECONDS=3"
set "WORKER_CONCURRENCY=1"
set "TASK_WORKSPACE_ROOT=%USERPROFILE%\AI-Second-Brain\Brain_Vault\Task_Runs"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

REM Billing guard: never let a metered credential reach the CLI child process.
set "ANTHROPIC_API_KEY="
set "OPENAI_API_KEY="
set "GEMINI_API_KEY="
set "GOOGLE_API_KEY="
set "GOOGLE_APPLICATION_CREDENTIALS="

cd /d "%~dp0.."
if not exist "logs" mkdir "logs"
echo [%date% %time%] host worker starting >> "logs\host_worker.log"
python -m cloudos.worker >> "logs\host_worker.log" 2>&1
echo [%date% %time%] host worker exited with %ERRORLEVEL% >> "logs\host_worker.log"
endlocal
