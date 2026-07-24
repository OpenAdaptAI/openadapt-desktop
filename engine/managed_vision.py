"""Exact-hash first-use provisioning for Desktop's local vision runtime.

OpenCV's upstream wheels include separately licensed codec libraries. They are
therefore not copied into OpenAdapt's MIT wheel, frozen sidecar, or installer.
Desktop downloads the exact reviewed OpenCV and RapidOCR wheels only when a
Flow command first needs the vision stack, verifies their published hashes,
extracts them into a versioned user-data cache, and loads them as an optional
runtime component. Re-running the command retries a failed download; a
partially prepared directory is never treated as ready.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import platform
import secrets
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from importlib.metadata import distribution
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable

MANIFEST_PATH = Path(__file__).with_name("vision-runtime-manifest.json")
MARKER_NAME = ".complete.json"
MAX_EXTRACTED_BYTES = 700 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
ALLOWED_DOWNLOAD_HOST = "files.pythonhosted.org"
RAPIDOCR_NOTICE_MEMBERS = (
    ("LICENSE", "LICENSE"),
    ("NOTICE", "NOTICE"),
)
SUPPORTED_TARGETS = frozenset(
    {
        "aarch64-apple-darwin",
        "x86_64-apple-darwin",
        "x86_64-pc-windows-msvc",
        "x86_64-unknown-linux-gnu",
    }
)


class ManagedVisionRuntimeError(RuntimeError):
    """The separately provisioned vision runtime could not be made ready."""


@dataclass(frozen=True)
class Wheel:
    distribution: str
    version: str
    url: str
    sha256: str
    bytes: int
    license_expression: str


@dataclass(frozen=True)
class RuntimeContract:
    runtime_version: str
    target: str
    wheels: tuple[Wheel, ...]
    manifest_sha256: str

    @property
    def build_id(self) -> str:
        return f"{self.runtime_version}-{self.manifest_sha256[:12]}"


def current_target(
    *,
    system: str | None = None,
    machine: str | None = None,
) -> str:
    """Return the release-manifest target for this host."""

    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    if machine in {"amd64", "x64"}:
        machine = "x86_64"
    elif machine in {"arm64", "aarch64"}:
        machine = "aarch64"
    target = {
        ("darwin", "aarch64"): "aarch64-apple-darwin",
        ("darwin", "x86_64"): "x86_64-apple-darwin",
        ("windows", "x86_64"): "x86_64-pc-windows-msvc",
        ("linux", "x86_64"): "x86_64-unknown-linux-gnu",
    }.get((system, machine))
    if target is None:
        raise ManagedVisionRuntimeError(
            f"no reviewed local vision runtime is published for {system}/{machine}"
        )
    return target


def _wheel(record: object) -> Wheel:
    if not isinstance(record, dict):
        raise ManagedVisionRuntimeError("vision runtime wheel record is not an object")
    try:
        wheel = Wheel(
            distribution=str(record["distribution"]),
            version=str(record["version"]),
            url=str(record["url"]),
            sha256=str(record["sha256"]),
            bytes=int(record["bytes"]),
            license_expression=str(record["license_expression"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ManagedVisionRuntimeError("vision runtime wheel record is malformed") from exc
    parsed = urllib.parse.urlparse(wheel.url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != ALLOWED_DOWNLOAD_HOST
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith(".whl")
    ):
        raise ManagedVisionRuntimeError(
            f"vision runtime wheel URL is not an exact trusted PyPI file: {wheel.url}"
        )
    if (
        not wheel.distribution
        or not wheel.version
        or not wheel.license_expression
        or len(wheel.sha256) != 64
        or any(character not in "0123456789abcdef" for character in wheel.sha256)
        or wheel.bytes <= 0
        or wheel.bytes > 150 * 1024 * 1024
    ):
        raise ManagedVisionRuntimeError(
            f"vision runtime wheel metadata is invalid for {wheel.distribution or 'unknown'}"
        )
    return wheel


def load_contract(
    *,
    manifest_path: Path = MANIFEST_PATH,
    target: str | None = None,
) -> RuntimeContract:
    """Load and strictly validate the embedded release-reviewed manifest."""

    try:
        payload = manifest_path.read_bytes()
        manifest = json.loads(payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise ManagedVisionRuntimeError("embedded vision runtime manifest is invalid") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ManagedVisionRuntimeError("unsupported vision runtime manifest schema")
    if manifest.get("runtime") != "openadapt-managed-vision":
        raise ManagedVisionRuntimeError("unexpected vision runtime identity")
    runtime_version = manifest.get("runtime_version")
    shared = manifest.get("shared_wheels")
    artifacts = manifest.get("artifacts")
    if (
        not isinstance(runtime_version, str)
        or not runtime_version
        or not isinstance(shared, list)
        or not shared
        or not isinstance(artifacts, list)
    ):
        raise ManagedVisionRuntimeError("vision runtime manifest is incomplete")

    target_names = [artifact.get("target") for artifact in artifacts if isinstance(artifact, dict)]
    if set(target_names) != SUPPORTED_TARGETS or len(target_names) != len(SUPPORTED_TARGETS):
        raise ManagedVisionRuntimeError(
            "vision runtime manifest must cover each supported target exactly once"
        )
    selected_target = target or current_target()
    selected = next(
        (
            artifact
            for artifact in artifacts
            if isinstance(artifact, dict) and artifact.get("target") == selected_target
        ),
        None,
    )
    if selected is None or not isinstance(selected.get("wheels"), list):
        raise ManagedVisionRuntimeError(
            f"vision runtime manifest has no artifact for {selected_target}"
        )
    wheels = tuple(_wheel(record) for record in [*shared, *selected["wheels"]])
    names = [wheel.distribution for wheel in wheels]
    if sorted(names) != ["opencv-python", "rapidocr-onnxruntime"]:
        raise ManagedVisionRuntimeError(
            f"vision runtime must contain exact OpenCV and RapidOCR wheels, found {names}"
        )
    return RuntimeContract(
        runtime_version=runtime_version,
        target=selected_target,
        wheels=wheels,
        manifest_sha256=hashlib.sha256(payload).hexdigest(),
    )


def runtime_root() -> Path:
    """Return the versioned-cache parent, with an absolute offline override."""

    override = os.environ.get("OPENADAPT_VISION_RUNTIME_ROOT", "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ManagedVisionRuntimeError(
                "OPENADAPT_VISION_RUNTIME_ROOT must be an absolute directory"
            )
        return path
    return Path.home() / ".openadapt" / "vision-runtime"


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(DOWNLOAD_CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _download(
    wheel: Wheel,
    destination: Path,
    *,
    opener: Callable[..., BinaryIO] = urllib.request.urlopen,
) -> None:
    request = urllib.request.Request(
        wheel.url,
        headers={"User-Agent": "OpenAdapt-Desktop-managed-vision/1"},
    )
    try:
        response = opener(request, timeout=60)
        with response, destination.open("xb") as output:
            final_url = getattr(response, "geturl", lambda: wheel.url)()
            parsed = urllib.parse.urlparse(final_url)
            if parsed.scheme != "https" or parsed.hostname != ALLOWED_DOWNLOAD_HOST:
                raise ManagedVisionRuntimeError(
                    f"vision runtime download redirected outside trusted PyPI: {final_url}"
                )
            digest = hashlib.sha256()
            size = 0
            while chunk := response.read(DOWNLOAD_CHUNK_BYTES):
                size += len(chunk)
                if size > wheel.bytes:
                    raise ManagedVisionRuntimeError(
                        f"{wheel.distribution} download exceeded its reviewed size"
                    )
                digest.update(chunk)
                output.write(chunk)
    except ManagedVisionRuntimeError:
        raise
    except (OSError, ValueError) as exc:
        raise ManagedVisionRuntimeError(f"could not download {wheel.distribution}: {exc}") from exc
    if size != wheel.bytes or digest.hexdigest() != wheel.sha256:
        raise ManagedVisionRuntimeError(
            f"{wheel.distribution} wheel failed exact size/hash verification"
        )


def _safe_member(name: str) -> PurePosixPath:
    if not name or "\\" in name:
        raise ManagedVisionRuntimeError(f"unsafe vision runtime wheel member: {name!r}")
    member = PurePosixPath(name)
    if member.is_absolute() or any(part in {"", ".", ".."} for part in member.parts):
        raise ManagedVisionRuntimeError(f"unsafe vision runtime wheel member: {name!r}")
    return member


def _extract_wheels(
    archives: tuple[tuple[Wheel, Path], ...],
    staging: Path,
) -> list[dict[str, object]]:
    """Extract exact wheels without links, traversal, duplicates, or overrun."""

    files: list[dict[str, object]] = []
    seen: set[str] = set()
    extracted_bytes = 0
    for wheel, archive in archives:
        with zipfile.ZipFile(archive) as package:
            for info in package.infolist():
                if info.is_dir():
                    continue
                member = _safe_member(info.filename)
                key = member.as_posix().casefold()
                if key in seen:
                    raise ManagedVisionRuntimeError(
                        f"duplicate vision runtime wheel member: {member}"
                    )
                seen.add(key)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise ManagedVisionRuntimeError(
                        f"vision runtime wheel contains a symlink: {member}"
                    )
                extracted_bytes += info.file_size
                if extracted_bytes > MAX_EXTRACTED_BYTES:
                    raise ManagedVisionRuntimeError(
                        "vision runtime wheels exceed the extracted-size limit"
                    )
                destination = staging.joinpath(*member.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with package.open(info) as source, destination.open("xb") as output:
                    while chunk := source.read(DOWNLOAD_CHUNK_BYTES):
                        size += len(chunk)
                        digest.update(chunk)
                        output.write(chunk)
                if size != info.file_size:
                    raise ManagedVisionRuntimeError(
                        f"short extraction for vision runtime member: {member}"
                    )
                files.append(
                    {
                        "member": member.as_posix(),
                        "sha256": digest.hexdigest(),
                        "bytes": size,
                    }
                )
    return sorted(files, key=lambda record: str(record["member"]))


def _install_runtime_notices(
    staging: Path,
    *,
    notice_root: Path | None = None,
) -> list[dict[str, object]]:
    """Install the reviewed RapidOCR license beside its provisioned runtime."""

    if notice_root is None:
        resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        notice_root = resource_root / "third_party" / "rapidocr"
    destination_root = staging / "rapidocr_onnxruntime-1.4.4.dist-info" / "licenses"
    records: list[dict[str, object]] = []
    for source_name, destination_name in RAPIDOCR_NOTICE_MEMBERS:
        source = notice_root / source_name
        if not source.is_file():
            raise ManagedVisionRuntimeError(
                f"reviewed RapidOCR {source_name} is missing from Desktop"
            )
        destination = destination_root / destination_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        digest, size = _hash_file(destination)
        records.append(
            {
                "member": destination.relative_to(staging).as_posix(),
                "sha256": digest,
                "bytes": size,
            }
        )
    return records


def _marker(contract: RuntimeContract, files: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "runtime_version": contract.runtime_version,
        "target": contract.target,
        "manifest_sha256": contract.manifest_sha256,
        "wheels": [
            {
                "distribution": wheel.distribution,
                "version": wheel.version,
                "url": wheel.url,
                "sha256": wheel.sha256,
                "bytes": wheel.bytes,
                "license_expression": wheel.license_expression,
            }
            for wheel in contract.wheels
        ],
        "files": files,
    }


def _cache_is_valid(path: Path, contract: RuntimeContract) -> bool:
    try:
        marker = json.loads((path / MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    expected = _marker(contract, marker.get("files", []) if isinstance(marker, dict) else [])
    if not isinstance(marker, dict):
        return False
    for key in (
        "schema_version",
        "runtime_version",
        "target",
        "manifest_sha256",
        "wheels",
    ):
        if marker.get(key) != expected[key]:
            return False
    files = marker.get("files")
    if not isinstance(files, list) or not files:
        return False
    for record in files:
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("member"), str)
            or not isinstance(record.get("sha256"), str)
            or not isinstance(record.get("bytes"), int)
        ):
            return False
        try:
            member = _safe_member(record["member"])
        except ManagedVisionRuntimeError:
            return False
        candidate = path.joinpath(*member.parts)
        if candidate.is_symlink() or not candidate.is_file():
            return False
        digest, size = _hash_file(candidate)
        if size != record["bytes"] or digest != record["sha256"]:
            return False
    return True


def _activate(path: Path, contract: RuntimeContract) -> None:
    resolved = str(path.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)
    importlib.invalidate_caches()

    try:
        import cv2
        import rapidocr_onnxruntime
    except ImportError as exc:
        raise ManagedVisionRuntimeError(
            "managed vision runtime imports failed after verified extraction"
        ) from exc
    expected = {wheel.distribution: wheel.version for wheel in contract.wheels}
    # Keep names data-driven from the signed manifest. Besides avoiding a
    # second source of truth, this prevents PyInstaller's metadata scanner from
    # copying these externally provisioned distributions into the sidecar.
    versions = {name: distribution(name).version for name in expected}
    expected_cv2 = ".".join(expected["opencv-python"].split(".")[:3])
    if versions != expected or cv2.__version__ != expected_cv2:
        raise ManagedVisionRuntimeError(
            f"managed vision runtime version drift: expected {expected}, found {versions}"
        )
    runtime_path = path.resolve()
    for module in (cv2, rapidocr_onnxruntime):
        module_path = Path(module.__file__ or "").resolve()
        if runtime_path not in module_path.parents:
            raise ManagedVisionRuntimeError(
                f"{module.__name__} loaded outside the verified managed runtime"
            )


def ensure_managed_vision_runtime(
    *,
    status: Callable[[str], None] | None = None,
) -> Path:
    """Ensure, verify, activate, and return the optional runtime directory."""

    status = status or (lambda message: print(message, file=sys.stderr, flush=True))
    contract = load_contract()
    root = runtime_root()
    final = root / contract.build_id / contract.target
    if _cache_is_valid(final, contract):
        _activate(final, contract)
        return final

    status("OpenAdapt: preparing the separately licensed local vision runtime (one-time download).")
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".vision-", dir=root))
    archives: list[tuple[Wheel, Path]] = []
    try:
        for wheel in contract.wheels:
            status(f"OpenAdapt: downloading {wheel.distribution} {wheel.version}...")
            archive = staging / f"{wheel.distribution}-{wheel.version}.whl"
            _download(wheel, archive)
            archives.append((wheel, archive))
        payload = staging / "payload"
        payload.mkdir()
        status("OpenAdapt: verifying and installing the local vision runtime...")
        files = _extract_wheels(tuple(archives), payload)
        files.extend(_install_runtime_notices(payload))
        files.sort(key=lambda record: str(record["member"]))
        (payload / MARKER_NAME).write_text(
            json.dumps(_marker(contract, files), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            quarantine = final.with_name(
                f"{final.name}.invalid-{os.getpid()}-{secrets.token_hex(4)}"
            )
            os.replace(final, quarantine)
        os.replace(payload, final)
    except Exception as exc:
        if isinstance(exc, ManagedVisionRuntimeError):
            detail = str(exc)
        else:
            detail = f"unexpected provisioning failure: {exc}"
        raise ManagedVisionRuntimeError(
            f"{detail}. Run the command again to retry; partial downloads were not activated."
        ) from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    if not _cache_is_valid(final, contract):
        raise ManagedVisionRuntimeError(
            "managed vision runtime failed its post-install integrity check"
        )
    _activate(final, contract)
    status("OpenAdapt: local vision runtime is ready.")
    return final
