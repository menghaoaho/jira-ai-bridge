@echo off
chcp 65001 >nul
title Jira 穿透服务停止器

echo 正在停止 Jira 穿透相关进程...

taskkill /f /im frpc.exe >nul 2>&1
echo [-] frpc 进程已关闭

taskkill /f /im nginx.exe >nul 2>&1
echo [-] nginx 进程已关闭

echo.
echo ==========================================
echo 所有服务已彻底停止。
echo ==========================================
pause
