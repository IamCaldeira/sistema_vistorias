@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Levantamento da Frota - Caratinga
python servidor.py
echo.
echo O servidor foi encerrado. Pressione uma tecla para fechar.
pause >nul
