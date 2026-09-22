@echo off
chcp 65001 >nul
title Zing Music MCP - Bridge
REM ============================================================
REM  Bridge ket noi server nhac local voi "Diem cuoi MCP" cua
REM  xiaozhi.me - giua cua so nay mo de trang web hien "Da ket noi"
REM
REM  LAN 1: Mo file nay bang Notepad, dan URL Diem cuoi MCP
REM         (lay tren web xiaozhi.me) vao bien ben duoi
REM ============================================================

set "MCP_ENDPOINT=wss://api.xiaozhi.me/mcp/?token=eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOjEwMTk1MzgsImFnZW50SWQiOjIyMzYwMjYsImVuZHBvaW50SWQiOiJhZ2VudF8yMjM2MDI2IiwicHVycG9zZSI6Im1jcC1lbmRwb2ludCIsImlhdCI6MTc4ODgyODEyMywiZXhwIjoxODIwMzg1NzIzfQ.T6g69-fwN6p5VHSw2hITyU-Qyxlfrocc2Q3O-5AWrBusikMTUJl2_0mLDX6P_t8f5XF4lXQZziK_Vc7ytC_5Rw"

cd /d "%~dp0"
echo Dang ket noi den xiaozhi.me... Giua cua so nay mo.
echo Khi log hien "Successfully connected" la OK.
echo Nhan Ctrl+C de dung.
python mcp_pipe.py server.py
pause