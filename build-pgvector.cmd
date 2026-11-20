@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 exit /b %errorlevel%
set "PGROOT=%~dp0.deployment\postgresql17\pgsql"
cd /d "%~dp0.deployment\pgvector-source\pgvector-0.8.7"
if errorlevel 1 exit /b %errorlevel%
nmake /NOLOGO /F Makefile.win
if errorlevel 1 exit /b %errorlevel%
nmake /NOLOGO /F Makefile.win install
