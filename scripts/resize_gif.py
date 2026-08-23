#!/usr/bin/env python3
"""Resize GIF files to target dimensions for OLED display."""

import cv2
import numpy as np
import os
import sys
import glob

def resize_gif(input_path, output_path, width=128, height=64):
    """Resize GIF to target dimensions."""
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        print(f"Failed to open GIF: {input_path}")
        return False

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0 or fps > 30:
        fps = 10  # Default FPS for GIFs

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"GIF: {input_path}, frames: {frame_count}, fps: {fps}")

    # Use VideoWriter to create resized GIF
    fourcc = cv2.VideoWriter_fourcc(*'MJPG')
    temp_avi = output_path + '.tmp.avi'
    out = cv2.VideoWriter(temp_avi, fourcc, fps, (width, height))

    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        resized = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        out.write(resized)
        frame_idx += 1

    cap.release()
    out.release()

    # Convert AVI to GIF using cv2
    cap2 = cv2.VideoCapture(temp_avi)
    frames = []
    while True:
        ret, frame = cap2.read()
        if not ret:
            break
        # Invert colors for OLED monochrome display
        frame = cv2.bitwise_not(frame)
        frames.append(frame)
    cap2.release()

    if not frames:
        print(f"No frames captured from {input_path}")
        os.remove(temp_avi)
        return False

    # Write GIF using imageio if available, otherwise use PIL
    try:
        import imageio
        imageio.mimsave(output_path, frames, fps=fps)
        print(f"Resized GIF saved to {output_path} ({width}x{height}, {len(frames)} frames)")
        os.remove(temp_avi)
        return True
    except ImportError:
        pass

    try:
        from PIL import Image
        pil_frames = [Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)) for f in frames]
        pil_frames[0].save(output_path, save_all=True, append_images=pil_frames[1:], 
                          duration=int(1000/fps), loop=0)
        print(f"Resized GIF saved to {output_path} ({width}x{height}, {len(frames)} frames)")
        os.remove(temp_avi)
        return True
    except ImportError:
        print("Need imageio or PIL to save GIF")
        os.remove(temp_avi)
        return False

if __name__ == '__main__':
    input_dir = "D:/xiaozhi/bieucam"
    output_dir = "D:/xiaozhi/bieucam/resized"
    os.makedirs(output_dir, exist_ok=True)
    
    gif_files = glob.glob(os.path.join(input_dir, "*.gif"))
    for gif_file in gif_files:
        filename = os.path.basename(gif_file)
        output_path = os.path.join(output_dir, filename)
        resize_gif(gif_file, output_path, width=128, height=64)