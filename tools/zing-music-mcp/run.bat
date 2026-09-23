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

REM ============================================================
REM  Chat luong nhac (bo trong = dung mac dinh trong server.py):
REM    AUDIO_SAMPLE_RATE  24000        - phai khop AUDIO_OUTPUT_SAMPLE_RATE cua board
REM    AUDIO_CHANNELS     1            - loa robot mono
REM    AUDIO_BITRATE      160k         - tran cua libmp3lame o 24 kHz (MPEG-2 LSF)
REM    AUDIO_PRESET       speaker      - bo loc bu tru loa nho (mac dinh)
REM                       flat|warm|bright|loud|none
REM                       Doi preset roi restart la nghe khac ngay -> thu A/B
REM    AUDIO_FILTERS      chuoi ffmpeg -af tuy y, DE preset khi duoc set
REM                       dat "" de tat DSP (tho hon, de re hon)
REM  Vi du:
REM    set "AUDIO_PRESET=warm"        -> nhieu bass hon
REM    set "AUDIO_PRESET=bright"      -> sang/thoang hon
REM    set "AUDIO_FILTERS=highpass=f=90,equalizer=f=250:t=q:w=1:g=5,alimiter=limit=0.841:level=disabled"
REM ============================================================

cd /d "%~dp0"
REM server.py gop MCP + HTTP stream; mcp_pipe.py can MCP che do stdio.
set "MCP_TRANSPORT=stdio"
echo Dang ket noi den xiaozhi.me... Giua cua so nay mo.
echo Khi log hien "Successfully connected" la OK.
echo Nhan Ctrl+C de dung.
python mcp_pipe.py server.py
pause