# -*- mode: python ; coding: utf-8 -*-
a = Analysis(
    ["subtitle_translator/__main__.py"],
    pathex=["."],
    binaries=[("bin/ffmpeg.exe", "bin"), ("bin/ffprobe.exe", "bin")],
    datas=[],
    hiddenimports=["keyring.backends.Windows"],
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SubTranslator", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="SubTranslator")
