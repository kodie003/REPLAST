import csv

from replast.logger import FIELDS, Transaction, TransactionLog


def test_add_read_and_export_round_trip(tmp_path):
    log = TransactionLog(tmp_path / "sub" / "t.db")
    log.add(Transaction(material="PET", command="SORT_PET", outcome="SORTED", mean_conf=0.91, frames_detected=5))
    log.add(Transaction(material="REJECT", command="REJECT", outcome="ERROR", error_msg="vision: x"))
    rows = log.rows()
    assert [r["id"] for r in rows] == [1, 2]
    assert rows[0]["mean_conf"] == 0.91
    n = log.export_csv(tmp_path / "out.csv")
    assert n == 2
    with open(tmp_path / "out.csv") as f:
        back = list(csv.DictReader(f))
    assert back[1]["error_msg"] == "vision: x"
    assert set(FIELDS) <= set(back[0].keys())
    log.close()


def test_log_survives_reopening(tmp_path):
    p = tmp_path / "t.db"
    TransactionLog(p).add(Transaction(material="PAPER"))
    assert TransactionLog(p).rows()[0]["material"] == "PAPER"
