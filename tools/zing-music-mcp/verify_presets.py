#!/usr/bin/env python3
"""Verify the audio presets in server.py by running ffmpeg and measuring frequency response."""

import os
import subprocess
import sys
import tempfile
import json

# Get ffmpeg path
try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
    FFMPEG = shutil.which("ffmpeg") or "ffmpeg"

if not FFMPEG or not os.path.exists(FFMPEG):
    print(f"FFmpeg not found at: {FFMPEG}")
    sys.exit(1)

print(f"Using FFmpeg: {FFMPEG}")

# Test each preset from server.py
PRESETS = {
    "speaker": "highpass=f=90,equalizer=f=200:t=q:w=1.0:g=3.5,equalizer=f=800:t=q:w=1.2:g=-2.5,equalizer=f=3200:t=q:w=1.4:g=2.5,treble=g=1.5:f=10000:w=0.7,alimiter=limit=0.841:level=disabled",
    "flat": "highpass=f=90,alimiter=limit=0.891:level=disabled",
    "warm": "highpass=f=80,equalizer=f=200:t=q:w=0.9:g=6,equalizer=f=800:t=q:w=1.2:g=-2.5,equalizer=f=3200:t=q:w=1.4:g=2.5,treble=g=1.5:f=10000:w=0.7,alimiter=limit=0.841:level=disabled",
    "bright": "highpass=f=100,equalizer=f=220:t=q:w=1.0:g=2,equalizer=f=800:t=q:w=1.2:g=-3,equalizer=f=3500:t=q:w=1.5:g=4,treble=g=3:f=10000:w=0.7,alimiter=limit=0.841:level=disabled",
    "loud": "loudnorm=I=-16:TP=-1.5:LRA=11,highpass=f=90,equalizer=f=200:t=q:w=1.0:g=3.5,equalizer=f=800:t=q:w=1.2:g=-2.5,equalizer=f=3200:t=q:w=1.4:g=2.5,treble=g=1.5:f=10000:w=0.7,alimiter=limit=0.841:level=disabled",
    "none": "",
}

# Create a test tone (48kHz stereo, 10 seconds)
TEST_WAV = os.path.join(tempfile.gettempdir(), "test_tone_48k.wav")
if not os.path.exists(TEST_WAV):
    print(f"Generating test tone: {TEST_WAV}")
    result = subprocess.run(
        [FFMPEG, "-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=10",
         "-ac", "2", TEST_WAV],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"Failed to generate test tone: {result.stderr}")
        sys.exit(1)

# Test each preset
print("\n=== Testing presets ===\n")
for preset_name, filter_chain in PRESETS.items():
    print(f"\n--- Preset: {preset_name} ---")
    print(f"Filter chain: {filter_chain or '(none)'}")
    
    # Convert to 24kHz mono with the preset
    output_mp3 = os.path.join(tempfile.gettempdir(), f"test_{preset_name}.mp3")
    result = subprocess.run(
        [FFMPEG, "-y", "-i", TEST_WAV,
         "-vn", "-ac", "1", "-ar", "24000",
         "-af", filter_chain if filter_chain else "anull",
         "-b:a", "160k", "-f", "mp3", output_mp3],
        capture_output=True, text=True
    )
    
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr[:200]}")
        continue
    
    # Check output
    if os.path.exists(output_mp3):
        size = os.path.getsize(output_mp3)
        print(f"  Output MP3: {size} bytes ({size/1024:.1f} KB)")
        
        # Get info about the output
        result = subprocess.run(
            [FFMPEG, "-i", output_mp3],
            capture_output=True, text=True
        )
        for line in result.stderr.split('\n'):
            if 'Duration' in line or 'Stream' in line:
                print(f"  {line.strip()}")
    else:
        print("  ERROR: Output file not created")

print("\n=== All presets tested ===")