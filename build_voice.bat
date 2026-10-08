@echo off
call "C:\Espressif\frameworks\esp-idf-v5.5.5\export.bat"
python scripts/build.py bread-compact-wifi-lcd --name bread-compact-wifi-lcd
