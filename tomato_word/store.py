"""本地书架与阅读进度（纯 JSON，落在 %LOCALAPPDATA%\\TomatoWord\\state.json）。"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .engine import app_data_dir


def state_path() -> Path:
    return app_data_dir() / "state.json"


class Store:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data = {
            "shelf": [],        # [{book_id,title,author}]
            "progress": {},     # book_id -> {index,item_id,offset,title}
            "ui": {"font_size": 12, "typing": True},
        }
        self.load()

    # -- 持久化 ----------------------------------------------------------
    def load(self) -> None:
        try:
            raw = json.loads(state_path().read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data["shelf"] = list(raw.get("shelf") or [])
                self._data["progress"] = dict(raw.get("progress") or {})
                self._data["ui"].update(raw.get("ui") or {})
        except Exception:
            pass

    def save(self) -> None:
        with self._lock:
            try:
                state_path().write_text(
                    json.dumps(self._data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass

    # -- 书架 ------------------------------------------------------------
    @property
    def shelf(self) -> list[dict]:
        return self._data["shelf"]

    def in_shelf(self, book_id: str) -> bool:
        return any(b.get("book_id") == str(book_id) for b in self.shelf)

    def add_book(self, book_id: str, title: str, author: str = "") -> None:
        book_id = str(book_id)
        if self.in_shelf(book_id):
            return
        self.shelf.insert(
            0,
            {"book_id": book_id, "title": title, "author": author, "added": int(time.time())},
        )
        self.save()

    def remove_book(self, book_id: str) -> None:
        book_id = str(book_id)
        self._data["shelf"] = [b for b in self.shelf if b.get("book_id") != book_id]
        self._data["progress"].pop(book_id, None)
        self.save()

    # -- 进度 ------------------------------------------------------------
    def get_progress(self, book_id: str) -> dict:
        return self._data["progress"].get(str(book_id)) or {}

    def set_progress(self, book_id: str, index: int, item_id: str, title: str, offset: float = 0.0) -> None:
        self._data["progress"][str(book_id)] = {
            "index": int(index),
            "item_id": str(item_id),
            "title": title,
            "offset": float(offset),
            "at": int(time.time()),
        }
        self.save()

    # -- UI 偏好 ---------------------------------------------------------
    def ui(self, key: str, default=None):
        return self._data["ui"].get(key, default)

    def set_ui(self, key: str, value) -> None:
        self._data["ui"][key] = value
        self.save()
