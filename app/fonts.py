from __future__ import annotations

import ipaddress
import re
import socket
import struct
import urllib.parse
import urllib.request
from pathlib import Path
from typing import BinaryIO


class FontError(ValueError):
    pass


class FontManager:
    """Manage export-compatible fonts stored inside the local app data folder."""

    MAX_BYTES = 25 * 1024 * 1024
    SUFFIXES = {".ttf", ".otf"}
    DOWNLOAD_HOSTS = {
        "fonts.gstatic.com",
        "github.com",
        "raw.githubusercontent.com",
        "objects.githubusercontent.com",
    }
    SYSTEM_FONTS = (
        "Arial", "Helvetica", "Verdana", "Georgia", "Impact",
        "Poppins", "Montserrat", "Amsi Pro",
    )

    def __init__(self, root: Path):
        self.directory = root / "fonts"
        self.directory.mkdir(parents=True, exist_ok=True)

    def list_fonts(self) -> list[dict[str, str | bool]]:
        fonts = {
            family.casefold(): {"family": family, "filename": "", "url": "", "custom": False}
            for family in self.SYSTEM_FONTS
        }
        for path in sorted(self.directory.iterdir(), key=lambda item: item.name.casefold()):
            if path.is_file() and path.suffix.lower() in self.SUFFIXES:
                family = self.family_from_file(path)
                fonts[family.casefold()] = {
                    "family": family,
                    "filename": path.name,
                    "url": f"/fonts/{urllib.parse.quote(path.name)}",
                    "custom": True,
                }
        return list(fonts.values())

    def save_upload(self, filename: str, stream: BinaryIO, length: int) -> dict[str, str | bool]:
        safe_name = self._safe_filename(filename)
        if length <= 0:
            raise FontError("Choose a TTF or OTF font file")
        if length > self.MAX_BYTES:
            raise FontError("Font files must be 25 MB or smaller")
        destination = self.directory / safe_name
        self._write_stream(stream, destination, length)
        try:
            self._validate_file(destination)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return self._payload(destination)

    def download(self, url: str) -> dict[str, str | bool]:
        parsed = urllib.parse.urlparse(str(url).strip())
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or hostname not in self.DOWNLOAD_HOSTS:
            allowed = ", ".join(sorted(self.DOWNLOAD_HOSTS))
            raise FontError(f"Use a direct HTTPS TTF/OTF link from: {allowed}")
        self._reject_private_address(hostname)
        filename = Path(urllib.parse.unquote(parsed.path)).name
        safe_name = self._safe_filename(filename)
        request = urllib.request.Request(url, headers={"User-Agent": "LocalVideoStudio/0.6.9"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                final = urllib.parse.urlparse(response.geturl())
                final_host = (final.hostname or "").lower()
                if final_host not in self.DOWNLOAD_HOSTS:
                    raise FontError("The font download redirected to an unsupported host")
                self._reject_private_address(final_host)
                declared = int(response.headers.get("Content-Length") or 0)
                if declared > self.MAX_BYTES:
                    raise FontError("Font files must be 25 MB or smaller")
                data = response.read(self.MAX_BYTES + 1)
        except FontError:
            raise
        except Exception as error:
            raise FontError(f"Font download failed: {error}") from error
        if len(data) > self.MAX_BYTES:
            raise FontError("Font files must be 25 MB or smaller")
        destination = self.directory / safe_name
        destination.write_bytes(data)
        try:
            self._validate_file(destination)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return self._payload(destination)

    def resolve(self, filename: str) -> Path:
        safe_name = self._safe_filename(filename)
        path = (self.directory / safe_name).resolve()
        if path.parent != self.directory.resolve() or not path.is_file():
            raise FontError("Font not found")
        return path

    @staticmethod
    def family_from_filename(filename: str) -> str:
        stem = Path(filename).stem.replace("_", " ").replace("-", " ")
        return re.sub(r"\s+", " ", stem).strip()[:100] or "Custom font"

    @classmethod
    def family_from_file(cls, path: Path) -> str:
        """Read the OpenType name table, falling back to a friendly filename."""
        try:
            data = path.read_bytes()
            num_tables = struct.unpack_from(">H", data, 4)[0]
            if num_tables > 512:
                raise ValueError("invalid table count")
            name_offset = None
            for index in range(num_tables):
                record = 12 + index * 16
                if data[record:record + 4] == b"name":
                    name_offset = struct.unpack_from(">I", data, record + 8)[0]
                    break
            if name_offset is None:
                raise ValueError("missing name table")
            count, string_offset = struct.unpack_from(">HH", data, name_offset + 2)
            candidates: list[tuple[int, int, str]] = []
            for index in range(count):
                record = name_offset + 6 + index * 12
                platform, _encoding, language, name_id, length, offset = struct.unpack_from(">HHHHHH", data, record)
                if name_id not in {1, 16}:
                    continue
                start = name_offset + string_offset + offset
                raw = data[start:start + length]
                text = raw.decode("utf-16-be" if platform in {0, 3} else "mac_roman", errors="ignore").strip()
                if text:
                    priority = (0 if name_id == 16 else 1) + (0 if language in {0, 0x409} else 2)
                    candidates.append((priority, index, text))
            if candidates:
                return min(candidates)[2][:100]
        except (OSError, ValueError, IndexError, struct.error):
            pass
        return cls.family_from_filename(path.name)

    def _safe_filename(self, filename: str) -> str:
        name = Path(filename).name
        suffix = Path(name).suffix.lower()
        if suffix not in self.SUFFIXES:
            raise FontError("Only TTF and OTF fonts are supported")
        stem = re.sub(r"[^A-Za-z0-9._ -]+", "", Path(name).stem).strip(" .")[:90]
        if not stem:
            raise FontError("The font filename is invalid")
        return f"{stem}{suffix}"

    def _write_stream(self, stream: BinaryIO, destination: Path, length: int) -> None:
        remaining = length
        with destination.open("wb") as file:
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                file.write(chunk)
                remaining -= len(chunk)
        if remaining:
            destination.unlink(missing_ok=True)
            raise FontError("Font upload ended before all bytes arrived")

    @staticmethod
    def _validate_file(path: Path) -> None:
        with path.open("rb") as file:
            signature = file.read(4)
        if signature not in {b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf"}:
            raise FontError("This file does not appear to be a valid TTF or OTF font")

    @staticmethod
    def _reject_private_address(hostname: str) -> None:
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)}
        except OSError as error:
            raise FontError("The font host could not be resolved") from error
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise FontError("Private or local font download addresses are not allowed")

    def _payload(self, path: Path) -> dict[str, str | bool]:
        return {
            "family": self.family_from_file(path),
            "filename": path.name,
            "url": f"/fonts/{urllib.parse.quote(path.name)}",
            "custom": True,
        }
