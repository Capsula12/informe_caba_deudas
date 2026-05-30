@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM  servir.bat — Levanta el servidor local del Informe CABA y abre el browser.
REM
REM  Doble-click para usar.
REM
REM  Qué hace:
REM    1. Posiciona la consola en la carpeta del .bat (Otras\Informe_CABA\).
REM    2. Verifica que python esté en el PATH (si no, muestra mensaje claro).
REM    3. Levanta python -m http.server en el puerto 8766 (bind 127.0.0.1).
REM    4. Espera 1 segundo y abre http://localhost:8766/ en el navegador.
REM    5. Deja la consola abierta con los logs del servidor.
REM    6. Cuando cerrás la consola (Ctrl+C o X) se detiene el servidor.
REM
REM  Configurable:
REM    - PUERTO: cambiar a otro número si 8766 está ocupado.
REM    - BIND:   127.0.0.1 = sólo accesible desde esta máquina (default seguro).
REM              0.0.0.0 (o vacío) = accesible desde otras máquinas de la LAN.
REM ─────────────────────────────────────────────────────────────────────────────

setlocal
set PUERTO=8766
set BIND=127.0.0.1

REM Carpeta del script (sin el slash final)
set SCRIPT_DIR=%~dp0
if "%SCRIPT_DIR:~-1%"=="\" set SCRIPT_DIR=%SCRIPT_DIR:~0,-1%
cd /d "%SCRIPT_DIR%"

title Informe CABA · Servidor local

echo ============================================================================
echo  Informe CABA · servidor local
echo ============================================================================
echo  Carpeta: %SCRIPT_DIR%
echo  URL:     http://localhost:%PUERTO%/
echo.
echo  Cerrá esta ventana (X o Ctrl+C) para detener el servidor.
echo ============================================================================
echo.

REM Verificar Python
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python no esta en el PATH.
    echo         Instalalo desde https://www.python.org/downloads/ y reintentá.
    echo.
    pause
    exit /b 1
)

REM Abrir el browser despues de un breve delay (en paralelo con el server)
REM Usamos timeout para asegurar que el server tenga tiempo de bindear el puerto.
start "" cmd /c "timeout /t 1 /nobreak >nul & start """" http://localhost:%PUERTO%/"

REM Levantar el server (foreground, bloqueante)
python -m http.server %PUERTO% --bind %BIND%

REM Si caemos acá es porque el server termino (Ctrl+C o error)
echo.
echo Servidor detenido.
pause
endlocal
