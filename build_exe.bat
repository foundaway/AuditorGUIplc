@echo off
REM Genera dist\L5X_Auditor.exe (requiere Python en el PATH)
python -m pip install --upgrade pyinstaller openpyxl tkinterdnd2 || goto :error
python -m PyInstaller --noconfirm --onefile --windowed --name L5X_Auditor --collect-all tkinterdnd2 l5x_auditor_gui.py || goto :error
echo.
echo Listo: dist\L5X_Auditor.exe
goto :eof
:error
echo Hubo un error al generar el ejecutable.
exit /b 1
