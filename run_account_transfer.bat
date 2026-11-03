@echo off
chcp 65001 >nul
echo =====================================================================
echo  Перенос Google-аккаунта на Xiaomi Watch 5 (DirectTransfer)
echo =====================================================================
echo.
set /p ACCOUNT="Введите Google Email для переноса: "
set /p PHONE_IP="Введите IP телефона (например 192.168.0.100): "
set /p WATCH_ADB="Введите IP часов для ADB (например 192.168.0.50:5555): "
echo.
python transfer_account.py --account %ACCOUNT% --phone %PHONE_IP% --watch %WATCH_ADB%
echo.
pause
