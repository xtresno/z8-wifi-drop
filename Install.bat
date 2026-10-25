@echo off
rem Double-click to install. Windows asks for admin rights, then runs install-windows.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -NoExit -File \"%~dp0install-windows.ps1\"'"
