@echo off
setlocal
rem User-authorized Windows Home workaround. Never reboot automatically.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0enable_p01_hyperv.ps1"
exit /b %errorlevel%
