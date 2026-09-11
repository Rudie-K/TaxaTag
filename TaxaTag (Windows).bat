@echo off
REM ---------------------------------------------------------------------------
REM Starts TaxaTag on Windows.
REM
REM Prefers the Python bundled inside bin\win64\python_embed so the program
REM works on a computer with no Python installed. Falls back to a system
REM Python if the bundled one is not there.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "EMBEDDED=%~dp0bin\win64\python_embed\python.exe"

if exist "%EMBEDDED%" (
    "%EMBEDDED%" "%~dp0taxatag.py" %*
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo.
        echo Python was not found on this computer, and the bundled copy is missing.
        echo Install Python 3.11 or newer from https://www.python.org/downloads/
        echo.
        pause
        exit /b 1
    )
    python "%~dp0taxatag.py" %*
)

if errorlevel 1 pause
endlocal
