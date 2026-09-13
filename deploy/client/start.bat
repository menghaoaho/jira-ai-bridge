@echo off
chcp 65001 >nul
title Jira 穿透服务启动器

echo ==========================================
echo [1/3] 正在检查环境与前置依赖...
echo ==========================================

cd /d "%~dp0"

if not exist "nginx\nginx.exe" (
    echo [错误] 未在当前目录下找到 nginx\nginx.exe，请检查文件结构！
    pause
    exit /b 1
)

if not exist "frpc\frpc.exe" (
    echo [错误] 未在当前目录下找到 frpc\frpc.exe，请检查文件结构！
    pause
    exit /b 1
)

echo ==========================================
echo [2/3] 正在启动本地 Nginx 反代...
echo ==========================================
taskkill /f /im nginx.exe >nul 2>&1
cd nginx
start "" nginx.exe
cd ..
timeout /t 2 /nobreak >nul

echo ==========================================
echo [3/3] 正在启动 frpc 客户端...
echo ==========================================
taskkill /f /im frpc.exe >nul 2>&1
cd frpc
start "frpc-service" frpc.exe -c frpc.toml
cd ..

echo.
echo ==========================================
echo 服务启动完成！
echo 1. 请观察弹出的 frpc 窗口，若出现 'login to server success' 说明连接成功。
echo 2. 如需停止，请直接双击运行 stop.bat。
echo ==========================================
pause
