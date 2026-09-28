@echo off
rem Windows: double-click this file. It runs "claude_switch.py menu" from this folder.
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
where py >nul 2>nul
if errorlevel 1 goto trypython
py -3 "%~dp0claude_switch.py" menu
goto done
:trypython
where python >nul 2>nul
if errorlevel 1 goto nopython
python "%~dp0claude_switch.py" menu
goto done
:nopython
echo Python 3 is required: https://www.python.org/downloads/
echo During setup tick "Add python.exe to PATH", then run this file again.
:done
echo.
pause
endlocal
