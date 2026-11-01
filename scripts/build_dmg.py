#!/usr/bin/env python3
"""Build and verify the classic app -> Applications macOS disk image.

Requires the dmg optional dependency. Never copies usage data, installs the app,
changes Finder preferences or automates the user's desktop.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tokenlog import __version__


def run(*args, capture=False):
    return subprocess.run([str(a) for a in args], check=True,
        stdout=subprocess.PIPE if capture else None).stdout


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for data in iter(lambda:stream.read(1024*1024), b""):
            digest.update(data)
    return digest.hexdigest()


@contextmanager
def mounted(image):
    with tempfile.TemporaryDirectory(prefix="tokenledger-dmg-mount-") as directory:
        mountpoint = Path(directory)/"volume"
        mountpoint.mkdir()
        run("hdiutil","attach",image,"-readonly","-nobrowse","-noautoopen",
            "-mountpoint",mountpoint,"-plist",capture=True)
        try:
            yield mountpoint
        finally:
            run("hdiutil","detach",mountpoint)


def build(app, output):
    import dmgbuild
    from ds_store import DSStore
    sys.path.insert(0,str(ROOT/"packaging"))
    from audit_artifacts import audit
    if sys.platform != "darwin":
        raise RuntimeError("Build DMG on macOS")
    app = app.resolve()
    if not app.is_dir() or app.suffix != ".app":
        raise ValueError("Select an existing .app bundle")
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch = ROOT/"build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dmg-stage-",dir=scratch) as directory:
        staged = Path(directory)/app.name
        run("ditto",app,staged)
        plist = staged/"Contents/Info.plist"
        info = plistlib.loads(plist.read_bytes())
        info.update(CFBundleShortVersionString=__version__, CFBundleVersion=__version__,
                    LSArchitecturePriority=["arm64"], LSRequiresNativeExecution=True,
                    LSMinimumSystemVersion="11.0")
        plist.write_bytes(plistlib.dumps(info,sort_keys=False))
        run("codesign","--force","--deep","--sign","-",staged)
        run("codesign","--verify","--deep","--strict",staged)
        icon = staged/"Contents/Resources"/info.get("CFBundleIconFile","icon-windowed.icns")
        settings = {
            "format":"UDZO", "compression_level":9, "filesystem":"HFS+",
            "files":[str(staged)], "symlinks":{"Applications":"/Applications"},
            "icon_locations":{app.name:(140,120),"Applications":(500,120)},
            "background":"builtin-arrow", "window_rect":((180,180),(640,280)),
            "default_view":"icon-view", "arrange_by":None,
            "icon_size":128, "text_size":15, "label_pos":"bottom",
            # FinderInfo added by hide_extensions invalidates strict bundle
            # verification; leave the signed app's metadata untouched.
            "grid_spacing":90,
            "show_status_bar":False, "show_tab_view":False, "show_toolbar":False,
            "show_pathbar":False, "show_sidebar":False,
        }
        if icon.is_file():
            settings["icon"]=str(icon)
        dmgbuild.build_dmg(str(output),f"Token Ledger {__version__}",settings=settings)
    run("hdiutil","verify",output)
    with mounted(output) as volume:
        application=volume/app.name
        if not application.is_dir() or os.readlink(volume/"Applications") != "/Applications":
            raise RuntimeError("App or Applications link missing")
        with DSStore.open(str(volume/".DS_Store"),"r") as finder:
            app_position=finder[app.name]["Iloc"]
            applications_position=finder["Applications"]["Iloc"]
            view=finder["."]["icvp"]
            if tuple(app_position[:2]) != (140,120) or tuple(applications_position[:2]) != (500,120):
                raise RuntimeError("Finder icon placement incorrect")
            if view["iconSize"] != 128 or view["backgroundType"] != 2:
                raise RuntimeError("Finder background or icon size incorrect")
        image_version=plistlib.loads((application/"Contents/Info.plist").read_bytes())["CFBundleShortVersionString"]
        if image_version != __version__:
            raise RuntimeError("Bundle version incorrect")
        if not (application/"Contents/Resources/LICENSE").is_file():
            raise RuntimeError("Application license missing")
        for name in ("PYTHON.txt", "TCL.txt", "TK.txt"):
            if not (application/"Contents/Resources/licenses"/name).is_file():
                raise RuntimeError("Runtime license notice missing")
        count,findings=audit(application)
        if findings:
            raise RuntimeError("Private-looking files present in app")
        run("codesign","--verify","--deep","--strict",application)
        binary=application/"Contents/MacOS/Token Ledger"
        if run("lipo","-archs",binary,capture=True).decode().strip() != "arm64":
            raise RuntimeError("Installer does not contain the Apple Silicon app")
        # Model the user's drag-copy without touching the real /Applications.
        with tempfile.TemporaryDirectory(prefix="tokenledger-installed-") as destination:
            installed=Path(destination)/app.name
            run("ditto",application,installed)
            self_test=run(installed/"Contents/MacOS/Token Ledger","--self-test",capture=True)
            checked=json.loads(self_test)
            if checked.get("version") != __version__ or checked.get("native_ui") != "tkinter":
                raise RuntimeError("Relocated app self-test failed")
    result={"file":output.name,"version":__version__,"architecture":"arm64",
        "default_language":"en","languages":["en","zh_CN"],
        "sha256":sha256(output),"bytes":output.stat().st_size,
        "mount_verified":True,"applications_target":"/Applications",
        "app_icon_position":list(app_position[:2]),"applications_icon_position":list(applications_position[:2]),
        "finder_background":"arrow","relocated_app_self_test":True,
        "app_files_audited":count,"privacy_findings":0,
        "signed_release":False,"notarized":False,"distribution_signature":"ad-hoc"}
    (output.parent/"dmg-info.json").write_text(json.dumps(result,indent=2)+"\n")
    archives=sorted(p for p in output.parent.iterdir() if p.suffix in (".exe",".zip",".dmg") and p.is_file())
    (output.parent/"SHA256SUMS.txt").write_text("".join(f"{sha256(p)}  {p.name}\n" for p in archives),encoding="ascii")
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app",type=Path,default=ROOT/"dist/macos-arm64/Token Ledger.app")
    parser.add_argument("--output",type=Path,default=ROOT/f"dist/TokenLedger-{__version__}-macos-arm64.dmg")
    args=parser.parse_args()
    print(json.dumps(build(args.app,args.output.resolve()),indent=2))


if __name__=="__main__":
    main()
