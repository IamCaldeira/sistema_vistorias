@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Instalacao - Levantamento da Frota
echo ================================================================
echo  INSTALACAO UNICA (o notebook precisa estar com INTERNET agora)
echo ================================================================
echo.
echo [1/2] Instalando bibliotecas opcionais (podem falhar sem problema)...
python -m pip install openpyxl
echo.
echo Pronto! Para usar, abra o arquivo iniciar.bat
pause
