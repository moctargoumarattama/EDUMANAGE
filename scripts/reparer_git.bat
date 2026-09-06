@echo off
echo ========================================================
echo Reparation de l'index Git EDUMANAGE / KLASORA
echo ========================================================
if exist .git\index.lock del /f /q .git\index.lock
if exist .git\index del /f /q .git\index
git reset
echo.
echo Index Git repare avec succes !
pause
