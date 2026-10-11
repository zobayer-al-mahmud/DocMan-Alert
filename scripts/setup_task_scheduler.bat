@echo off
REM ============================================================
REM  Sets up all 4 DocMan Alert scheduled tasks at once.
REM  Run this ONCE, as Administrator, on the new host PC.
REM
REM  How to run as Administrator:
REM    Right-click this file -> "Run as administrator"
REM ============================================================

REM Auto-detect the project directory (parent of this scripts\ folder) so this
REM works regardless of where the application is installed.
set SCRIPT_DIR=%~dp0
for %%I in ("%SCRIPT_DIR%..") do set PROJECT_DIR=%%~fI

echo Creating ComplianceTracker_Server (runs at system startup)...
schtasks /create /tn "ComplianceTracker_Server" ^
  /tr "\"%PROJECT_DIR%\scripts\run_server.bat\"" ^
  /sc onstart ^
  /ru SYSTEM ^
  /rl highest ^
  /f

echo Creating ComplianceTracker_Backup (daily at 6:30 PM)...
schtasks /create /tn "ComplianceTracker_Backup" ^
  /tr "\"%PROJECT_DIR%\scripts\run_backup.bat\"" ^
  /sc daily ^
  /st 18:30 ^
  /ru SYSTEM ^
  /rl highest ^
  /f

echo Creating ComplianceTracker_EmailAlert (weekly, Saturday at 9:00 AM)...
schtasks /create /tn "ComplianceTracker_EmailAlert" ^
  /tr "\"%PROJECT_DIR%\scripts\run_email_alert.bat\"" ^
  /sc weekly ^
  /d SAT ^
  /st 09:00 ^
  /ru SYSTEM ^
  /rl highest ^
  /f

echo Creating ComplianceTracker_StatusRecalc (daily at 9:00 AM)...
schtasks /create /tn "ComplianceTracker_StatusRecalc" ^
  /tr "\"%PROJECT_DIR%\scripts\run_status_recalc.bat\"" ^
  /sc daily ^
  /st 09:00 ^
  /ru SYSTEM ^
  /rl highest ^
  /f

echo.
echo ============================================================
echo All 4 tasks created. Verify in Task Scheduler Library:
echo   ComplianceTracker_Server
echo   ComplianceTracker_Backup
echo   ComplianceTracker_EmailAlert
echo   ComplianceTracker_StatusRecalc
echo ============================================================
pause
