@echo off
chcp 65001 >nul
echo =====================================================================
echo  Запуск Bluetooth RFCOMM-моста на телефоне для Xiaomi Watch 5
echo =====================================================================
echo.

set /p PHONE_ADB="Введите IP или serial телефона для ADB (например 192.168.0.100:5555): "
set /p WATCH_MAC="Введите Bluetooth MAC-адрес часов (например AA:BB:CC:DD:EE:FF): "

echo.
echo [1/2] Загрузка rfcomm_bridge.jar на телефон...
adb -s %PHONE_ADB% push rfcomm_bridge.jar /data/local/tmp/
if %errorlevel% neq 0 (
    echo Ошибка загрузки файла на телефон! Проверьте подключение ADB.
    pause
    exit /b %errorlevel%
)

echo [2/2] Запуск процесса моста через app_process...
adb -s %PHONE_ADB% shell "nohup /system/bin/app_process -Djava.class.path=/data/local/tmp/rfcomm_bridge.jar /data/local/tmp com.kaiserdev.rfcomm.RfcommBridge %WATCH_MAC% 5601 > /data/local/tmp/bridge.log 2>&1 &"

echo.
echo Проверка статуса моста:
timeout /t 2 /nobreak >nul
adb -s %PHONE_ADB% shell "cat /data/local/tmp/bridge.log"

echo.
echo =====================================================================
echo  Мост успешно запущен и слушает порт 5601!
echo  Теперь вы можете запустить activate_gemini.bat
echo =====================================================================
pause
