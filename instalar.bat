@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Instalacao - Levantamento da Frota
echo ================================================================
echo  INSTALACAO UNICA (o notebook precisa estar com INTERNET agora)
echo ================================================================
echo.
echo [1/3] Instalando bibliotecas obrigatorias...
python -m pip install --upgrade pillow segno cryptography
echo.
echo [2/3] Instalando bibliotecas opcionais (podem falhar sem problema)...
python -m pip install openpyxl
python -m pip install pillow-heif
echo.
echo [3/3] Criando o certificado do GPS...
python servidor.py --preparar
echo.
echo Pronto! Para usar, abra o arquivo iniciar.bat
pause
