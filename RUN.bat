@echo off
title SRF PRO License Server
echo ============================================
echo    SRF PRO LICENSE SERVER - STARTING...
echo ============================================
echo.
echo  Admin Dashboard: http://127.0.0.1:5000/
echo  Username: admin
echo  Password: AAfifaAfi128
echo.
echo ============================================
echo.
start http://127.0.0.1:5000/
python licensing_server.py
pause
