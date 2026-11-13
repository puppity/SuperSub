# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

# The NVIDIA cuBLAS/cuDNN DLLs (2 GB) are left out on purpose: speech recognition then runs on
# the CPU, or on the GPU when the machine has its own CUDA + cuDNN install.
a = Analysis(
    ["subtitle_translator/__main__.py"],
    pathex=["."],
    binaries=[("bin/ffmpeg.exe", "bin"), ("bin/ffprobe.exe", "bin"), ("bin/libmpv-2.dll", "bin")],
    datas=collect_data_files("faster_whisper"),
    hiddenimports=["keyring.backends.Windows", "faster_whisper", "ctranslate2", "mpv"],
    excludes=["tkinter", "nvidia"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SubTranslator", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="SubTranslator")
