"""Assemble the installer from a fixed set of files and the two frozen apps."""

import hashlib
import sys
from importlib.metadata import distributions
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build-v4"
RELEASE = ROOT / "release"
DOCS = ("README.md", "README.zh-CN.md", "README.zh-TW.md", "COPYRIGHT.txt", "shadow-enclave-mark.png")
LAUNCHERS = ("Install V4.cmd", "Install-V4.ps1", "Start V4.cmd")
PAYLOAD = (("EnclaveBackend", "backend"), ("ShadowEnclaveV4", "desktop"))


def write_notices(archive):
    python = Path(sys.base_prefix)
    for name in ("LICENSE.txt", "LICENSE_PYTHON.txt"):
        notice = python / name
        if notice.is_file():
            archive.write(notice, "licenses/Python-LICENSE.txt")
            break
    else:
        raise FileNotFoundError("Python's license file is missing from the build interpreter.")

    for directory in (python / "tcl", python / "Library" / "lib"):
        for notice in sorted(directory.glob("t*/license.terms")):
            archive.write(notice, f"licenses/{notice.parent.name}-license.terms")

    packages = Path(sys.prefix) / "Lib" / "site-packages"
    for dist in distributions(path=[str(packages)]):
        for entry in dist.files or ():
            if ".." in entry.parts or entry.is_absolute():
                continue
            named_notice = entry.name.lower().startswith(("license", "copying", "notice"))
            dist_notice = any(part.endswith(".dist-info") for part in entry.parts) and (
                "licenses" in entry.parts or named_notice
            )
            audio_notice = dist.name.lower() == "soundfile" and entry.parts[0] in (
                "_soundfile_data", "licensing"
            ) and (named_notice or entry.suffix == ".md")
            if dist_notice or audio_notice:
                archive.write(dist.locate_file(entry), f"licenses/{dist.name}/{entry.as_posix()}")


def checksum(file, output):
    with file.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    output.write_text(f"{digest}  {file.name}\n", encoding="ascii")


def main():
    RELEASE.mkdir(exist_ok=True)
    package = RELEASE / "ShadowEnclaveV4-package.zip"
    with ZipFile(package, "w", ZIP_DEFLATED) as archive:
        for application, target in PAYLOAD:
            base = BUILD / "dist" / application
            if not (base / f"{application}.exe").is_file():
                raise FileNotFoundError(base / f"{application}.exe")
            for file in sorted(base.rglob("*")):
                if file.is_file():
                    archive.write(file, f"{target}/{file.relative_to(base).as_posix()}")
        archive.write(BUILD / "BrokerService.exe", "BrokerService.exe")
        for name in ("hello_bridge.ps1", "COPYRIGHT.txt"):
            archive.write(ROOT / name, name)
        write_notices(archive)

    inner_hash = RELEASE / "SHA256.txt"
    checksum(package, inner_hash)
    bundle = RELEASE / "ShadowEnclaveV4-Windows-x64.zip"
    with ZipFile(bundle, "w", ZIP_DEFLATED) as archive:
        for name in (*LAUNCHERS, *DOCS):
            archive.write(ROOT / name, name)
        archive.write(package, f"release/{package.name}", compress_type=ZIP_STORED)
        archive.write(inner_hash, f"release/{inner_hash.name}")
    checksum(bundle, bundle.with_suffix(".sha256"))
    print(f"Built {bundle.name} and {bundle.with_suffix('.sha256').name}")


if __name__ == "__main__":
    main()
