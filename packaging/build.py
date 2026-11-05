"""Build a self-extracting Windows installer using the local Python runtime and C#."""
from pathlib import Path
import json
import subprocess
import sys
import zipfile

source = Path(__file__).resolve().parent
widget = source.parent
out = widget / "dist"
out.mkdir(exist_ok=True)
runtime = Path(sys.executable).parent
compiler = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")

def compile_exe(target, resource=None):
    args = [str(compiler), "/nologo", "/target:winexe", "/platform:x64", "/optimize+",
            "/reference:System.Windows.Forms.dll", "/reference:System.IO.Compression.dll",
            "/win32manifest:" + str(source / "app.manifest"), "/out:" + str(target)]
    if resource:
        args.append("/resource:" + str(resource) + ",payload.zip")
    subprocess.run(args + [str(source / "Launcher.cs")], check=True)

launcher = out / "CodexQuota.exe"
compile_exe(launcher)
payload = out / "payload.zip"
with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for name in ("quota_widget.py", "alpha_text.py"):
        archive.write(widget / name, name)
    archive.write(source / "entry.py", "entry.py")
    archive.write(launcher, launcher.name)
    archive.write(source / "使用说明.txt", "使用说明.txt")
    # No account data, usage cache, credentials, or local absolute paths in the package.
    archive.writestr("defaults.json", json.dumps({"mode": "primary", "locked": True,
                      "offset_right": 380.0, "offset_bottom": 54.4}, indent=2))
    for pattern in ("python*.exe", "python*.dll", "vcruntime*.dll", "LICENSE.txt"):
        for file in runtime.glob(pattern):
            archive.write(file, "runtime/" + file.name)
    excluded = {"site-packages", "__pycache__", "test", "tests", "idlelib", "ensurepip", "turtledemo"}
    for folder in ("Lib", "DLLs", "tcl"):
        for file in (runtime / folder).rglob("*"):
            relative = file.relative_to(runtime)
            if not file.is_file() or any(part in excluded for part in relative.parts):
                continue
            if file.suffix in (".pyc", ".pdb", ".chm"):
                continue
            archive.write(file, "runtime/" + relative.as_posix())
installer = out / "CodexQuotaWidget-Setup.exe"
compile_exe(installer, payload)
print(f"Built: {installer}\nSize: {installer.stat().st_size / 1024 / 1024:.1f} MB")
