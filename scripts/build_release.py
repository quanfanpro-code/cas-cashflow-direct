from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path


ROOT_FILES = ("SKILL.md", "README.md", "LICENSE")
ROOT_DIRECTORIES = ("references", "scripts", "src")


@dataclass(frozen=True, slots=True)
class ReleaseResult:
    root: Path
    zip_path: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_release(source_root: Path, output_root: Path) -> ReleaseResult:
    source = Path(source_root).resolve()
    output = Path(output_root).resolve()
    archive = output.with_suffix(".zip")
    if output.exists() or archive.exists():
        raise FileExistsError(f"分发目标已存在，不会覆盖：{output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()
    for name in ROOT_FILES:
        shutil.copy2(source / name, output / name)
    for name in ROOT_DIRECTORIES:
        shutil.copytree(
            source / name,
            output / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )

    files = sorted(
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file()
    )
    hashes = {name: _sha256(output / name) for name in files}
    manifest = {"schema_version": "1.0", "files": files, "sha256": hashes}
    with (output / "release_manifest.json").open("w", encoding="utf-8-sig", newline="\n") as target:
        json.dump(manifest, target, ensure_ascii=False, indent=2)

    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for path in sorted(item for item in output.rglob("*") if item.is_file()):
            package.write(path, (Path(output.name) / path.relative_to(output)).as_posix())
    return ReleaseResult(output, archive)


def main() -> int:
    parser = argparse.ArgumentParser(description="组装直接法现金流量表 Skill 干净分发包")
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = build_release(root, Path(arguments.output))
    print(json.dumps({"root": str(result.root), "zip": str(result.zip_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
