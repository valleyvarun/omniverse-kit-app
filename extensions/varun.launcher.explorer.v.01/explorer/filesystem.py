"""Asynchronous Omniverse filesystem access without overwriting existing files."""

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import omni.client

CLIENT: Any = omni.client


@dataclass(frozen=True)
class Entry:
    url: str
    name: str
    directory: bool


def normalize(path: str) -> str:
    if "://" not in path:
        return Path(path).absolute().as_uri()
    return path.rstrip("/") + "/" if urlsplit(path).path in ("", "/") else path.rstrip("/")


def child_url(parent: str, name: str) -> str:
    return parent.rstrip("/") + "/" + quote(name, safe="")


def display_name(url: str) -> str:
    return unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]) or urlsplit(url).netloc or url


def validate_name(name: str) -> None:
    if not name or not name.strip():
        raise ValueError("Enter a name.")
    if name in (".", "..") or name.endswith((" ", ".")):
        raise ValueError("Names cannot end with a space or period.")
    character: str
    for character in name:
        if character in '<>:"/\\|?*' or ord(character) < 32:
            raise ValueError('Names cannot contain < > : " / \\ | ? * or control characters.')
    stem: str = name.split(".", 1)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL"} or (
        len(stem) == 4 and stem[:3] in ("COM", "LPT") and stem[-1] in "123456789"
    ):
        raise ValueError("That name is reserved by Windows.")


def check_result(result: Any, action: str) -> None:
    if result == CLIENT.Result.OK:
        return
    if result == CLIENT.Result.ERROR_ALREADY_EXISTS:
        raise FileExistsError("An item with that name already exists.")
    if result == CLIENT.Result.ERROR_NOT_FOUND:
        raise FileNotFoundError("The folder or file no longer exists.")
    if result == CLIENT.Result.ERROR_ACCESS_DENIED:
        raise PermissionError("You do not have permission to access this location.")
    raise OSError(f"Could not {action}: {result}.")


async def read_directory(url: str) -> list[Entry]:
    result: Any
    items: Any
    result, items = await CLIENT.list_async(url)
    check_result(result, "read folder")
    entries: list[Entry] = []
    item: Any
    for item in items:
        name: str = str(item.relative_path)
        if name not in (".", ".."):
            entries.append(Entry(child_url(url, name), name, bool(item.flags & CLIENT.ItemFlags.CAN_HAVE_CHILDREN)))
    entries.sort(key=entry_order)
    return entries


def entry_order(entry: Entry) -> tuple[bool, str, str]:
    return not entry.directory, entry.name.casefold(), entry.name


async def verify_directory(url: str) -> None:
    result: Any
    item: Any
    result, item = await CLIENT.stat_async(url)
    check_result(result, "open folder")
    if not item.flags & CLIENT.ItemFlags.CAN_HAVE_CHILDREN:
        raise NotADirectoryError("Choose a folder.")


async def create_entry(parent: str, name: str, directory: bool) -> Entry:
    validate_name(name)
    url: str = child_url(parent, name)
    result: Any
    if directory:
        result = await CLIENT.create_folder_async(url)
    else:
        # Exclusive copy also protects remote destinations from name collisions.
        temporary: str
        with TemporaryDirectory(prefix="launcher-explorer-") as temporary:
            empty: Path = Path(temporary) / "empty"
            empty.write_bytes(b"")
            result = await CLIENT.copy_async(empty.as_uri(), url, CLIENT.CopyBehavior.ERROR_IF_EXISTS)
    check_result(result, "create folder" if directory else "create file")
    return Entry(url, name, directory)
