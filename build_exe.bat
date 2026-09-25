@echo off
setlocal
REM ==========================================================================
REM  L5X Auditor - genera el ejecutable de Windows
REM  Desarrollado por Joetan Saldaña
REM
REM  Uso:
REM     build_exe.bat            -> dist\L5X_Auditor.exe   (un solo archivo)
REM     build_exe.bat onedir     -> dist\L5X_Auditor\      (carpeta, abre mas rapido)
REM
REM  Firma digital opcional (Authenticode), si tienes un certificado .pfx:
REM     set SIGN_PFX=C:\ruta\certificado.pfx
REM     set SIGN_PASS=contraseña
REM     build_exe.bat
REM ==========================================================================
chcp 65001 >nul

set MODE=--onefile
set OUT=dist\L5X_Auditor.exe
if /I "%~1"=="onedir" (
    set MODE=--onedir
    set OUT=dist\L5X_Auditor\L5X_Auditor.exe
)

echo [1/3] Instalando dependencias...
python -m pip install --upgrade pyinstaller pillow openpyxl tkinterdnd2 || goto :error

if not exist assets\l5x_auditor.ico (
    echo Generando icono...
    python tools\make_icon.py || goto :error
)

echo [2/3] Compilando (%MODE%)...
python -m PyInstaller --noconfirm --clean %MODE% --windowed --name L5X_Auditor ^
    --icon assets\l5x_auditor.ico ^
    --version-file version_info.txt ^
    --add-data "assets;assets" ^
    --collect-all tkinterdnd2 ^
    l5x_auditor_gui.py || goto :error

echo [3/3] Firma digital...
if "%SIGN_PFX%"=="" (
    echo    Sin certificado: se omite la firma Authenticode. El autor queda en Propiedades ^> Detalles.
) else (
    signtool sign /f "%SIGN_PFX%" /p "%SIGN_PASS%" /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 ^
        /d "L5X Auditor" "%OUT%" || goto :error
    signtool verify /pa "%OUT%"
)

echo.
echo Listo: %OUT%
goto :eof

:error
echo.
echo Hubo un error al generar el ejecutable.
exit /b 1
