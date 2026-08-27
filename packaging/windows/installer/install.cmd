@echo off
rem packaging/windows/installer/install.cmd
rem Bootstrap run by the CleanStreamTS-install.exe SFX after it unpacks its
rem payload (this file + install.ps1 + 7zr.exe + the app archive) to a temp
rem folder.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
exit /b %ERRORLEVEL%
