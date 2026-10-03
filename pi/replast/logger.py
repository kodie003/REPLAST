"""Records every item the machine handles in an SQLite file, with CSV export.

One row per item: what the camera saw, what was decided and why, what the
mechanism did, and how long it took. These rows feed the results sheet.
"""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

COLUMNS = [
    ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
    ("timestamp", "TEXT"),
    ("material", "TEXT"),          # PET / ALUMINIUM / PAPER / REJECT
    ("command", "TEXT"),           # serial command sent, e.g. SORT_PAPER
    ("reason_code", "TEXT"),       # first failing rule, or OK
    ("reasons", "TEXT"),           # every failing rule, comma separated
    ("winning_class", "TEXT"),     # model class, e.g. PLASTIC
    ("mean_conf", "REAL"),
    ("frames_detected", "INTEGER"),
    ("frames_agreeing", "INTEGER"),
    ("outcome", "TEXT"),           # SORTED / REJECTED / ERROR
    ("response_time_s", "REAL"),   # item detected -> sort command sent
    ("cycle_time_s", "REAL"),      # item detected -> mechanism back home
    ("inference_ms", "REAL"),      # mean model time per frame
    ("item_cleared", "INTEGER"),   # 1 if the IR saw the platform empty afterwards
    ("true_label", "TEXT"),        # filled in by the acceptance test, else empty
    ("error_msg", "TEXT"),
    ("images", "TEXT"),            # saved photo paths, if any
]
FIELDS = [name for name, _ in COLUMNS if name != "id"]


@dataclass
class Transaction:
    material: str = ""
    command: str = ""
    reason_code: str = ""
    reasons: str = ""
    winning_class: str = ""
    mean_conf: Optional[float] = None
    frames_detected: Optional[int] = None
    frames_agreeing: Optional[int] = None
    outcome: str = ""
    response_time_s: Optional[float] = None
    cycle_time_s: Optional[float] = None
    inference_ms: Optional[float] = None
    item_cleared: Optional[int] = None
    true_label: str = ""
    error_msg: str = ""
    images: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class TransactionLog:
    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        cols = ", ".join(f"{n} {t}" for n, t in COLUMNS)
        self.db.execute(f"CREATE TABLE IF NOT EXISTS transactions ({cols})")
        self.db.commit()

    def add(self, t: Transaction) -> int:
        row = asdict(t)
        placeholders = ", ".join("?" for _ in FIELDS)
        cur = self.db.execute(
            f"INSERT INTO transactions ({', '.join(FIELDS)}) VALUES ({placeholders})",
            [row[f] for f in FIELDS],
        )
        self.db.commit()  # commit every row: a power cut must not lose the log
        return int(cur.lastrowid)

    def rows(self) -> list[dict]:
        cur = self.db.execute("SELECT * FROM transactions ORDER BY id")
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    def export_csv(self, path: Union[str, Path]) -> int:
        rows = self.rows()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[n for n, _ in COLUMNS])
            w.writeheader()
            w.writerows(rows)
        return len(rows)

    def close(self) -> None:
        self.db.close()
