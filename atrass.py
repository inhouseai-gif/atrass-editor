#!/usr/bin/env python3
"""Portable entry point. Editorial reasoning is performed by the hosting agent."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent
KIT = ROOT / 'outputs/atrass-editing-kit'
ENV = ROOT / '.venv-atrass'
PYTHON = ENV / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'setup':
        parser = argparse.ArgumentParser()
        parser.add_argument('command')
        parser.add_argument('--asr', choices=['none', 'mlx', 'cpu'], default='none')
        args = parser.parse_args()
        if not PYTHON.exists():
            venv.EnvBuilder(with_pip=True).create(ENV)
        packages = ['imageio-ffmpeg>=0.6,<0.7']
        if args.asr == 'mlx':
            if sys.platform != 'darwin':
                parser.error('MLX transcription requires an Apple Silicon Mac; use cpu on Windows/Linux')
            packages += ['mlx-whisper>=0.4,<0.5']
        elif args.asr == 'cpu':
            packages += ['faster-whisper>=1.1,<2']
        subprocess.run([str(PYTHON), '-m', 'pip', 'install', '-r', str(KIT/'engine/requirements.txt'), *packages], check=True)
        print(json.dumps({'python': str(PYTHON), 'asr': args.asr, 'media_uploaded': False}))
        return
    # A caller's working environment may be used; a clone's private environment
    # takes precedence. Never change global Python, shell profiles, or accounts.
    target = PYTHON if PYTHON.exists() else Path(sys.executable)
    subprocess.run([str(target), str(KIT/'engine/production.py'), *sys.argv[1:]], check=True)


if __name__ == '__main__':
    main()
