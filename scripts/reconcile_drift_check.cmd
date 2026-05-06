@echo off
REM TR-15 reconciliation drift check wrapper for Windows Task Scheduler.
REM Invoked by the scheduled task 'ShopifyConnectorReconcileDrift'.
REM Logs to logs\reconcile_drift_YYYYMMDD_HHMM.log

setlocal

REM Force UTF-8 so em-dashes etc. in Python output don't mojibake to '?'.
chcp 65001 > nul
set PYTHONIOENCODING=utf-8

set PROJECT=C:\Users\jodom\projects\shopify_connector
cd /d "%PROJECT%"

REM Build a timestamp suffix for the log filename: YYYYMMDD_HHMM.
REM wmic was removed in Windows 11 24H2, so use PowerShell instead.
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmm"') do set STAMP=%%I
set LOGFILE=%PROJECT%\logs\reconcile_drift_%STAMP%.log

echo [%DATE% %TIME%] starting reconcile drift check > "%LOGFILE%"
"%PROJECT%\.venv\Scripts\python.exe" "%PROJECT%\scripts\reconcile_drift_check.py" >> "%LOGFILE%" 2>&1
set RC=%ERRORLEVEL%
echo. >> "%LOGFILE%"
echo [%DATE% %TIME%] exit code: %RC% >> "%LOGFILE%"

endlocal & exit /b %RC%
