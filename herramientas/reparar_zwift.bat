@echo off
rem Repara el ultimo inProgressActivity.fit de Descargas para poder subirlo a Garmin Connect.
rem Crea Descargas\zwift_AAAA-MM-DD_reparado.fit con la fecha de hoy.
setlocal
set "DL=%USERPROFILE%\Downloads"
if not exist "%DL%\inProgressActivity.fit" (
  echo No encuentro "%DL%\inProgressActivity.fit".
  pause
  exit /b 1
)
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "HOY=%%d"
python "%~dp0reparar_fit.py" "%DL%\inProgressActivity.fit" "%DL%\zwift_%HOY%_reparado.fit"
if errorlevel 1 (
  echo Algo ha fallado. Pasale el archivo a Claude.
) else (
  echo.
  echo Listo: %DL%\zwift_%HOY%_reparado.fit
  echo Subelo en connect.garmin.com ^> Importar datos.
)
pause
