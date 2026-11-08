from datetime import datetime

from openpyxl import load_workbook

from storage import HEADERS, LEAD_FIELDS, Storage


def test_headers_match_export_fields():
    assert len(HEADERS) == len(LEAD_FIELDS)
    assert LEAD_FIELDS[0] == "id"
    assert LEAD_FIELDS[-1] == "photo"


def test_date_is_iso_in_db_and_readable_in_excel(tmp_path):
    storage = make_storage(tmp_path)
    try:
        storage.create_lead(
            {"user_id": 77, "name": "Анна", "date": "2026-11-05", "time": "10:00",
             "consent": True, "request_type": "booking"}
        )
        row = storage.conn.execute("SELECT date, user_id, created_at FROM leads").fetchone()
        assert row["date"] == "2026-11-05"
        assert row["user_id"] == 77
        assert row["created_at"].startswith("20")  # ISO-время, а не dd.mm.yyyy

        wb = load_workbook(tmp_path / "leads.xlsx")
        ws = wb["Заявки"]
        date_col = LEAD_FIELDS.index("date") + 1
        created_col = LEAD_FIELDS.index("created_at") + 1
        assert ws.cell(row=2, column=date_col).value == "05.11.2026"
        assert len(str(ws.cell(row=2, column=created_col).value)) == 16  # дд.мм.гггг чч:мм
        wb.close()
    finally:
        storage.close()


def make_storage(tmp_path) -> Storage:
    return Storage(tmp_path / "bot.db", tmp_path / "leads.xlsx", tmp_path / "outbox.jsonl")


def test_create_lead_and_export(tmp_path):
    storage = make_storage(tmp_path)
    try:
        lead_id = storage.create_lead(
            {
                "name": "Анна",
                "phone": "8 916 123-45-67",
                "request_type": "repair",
                "service": "Замена носоупоров",
                "consent": True,
                "comment": "просила не звонить",
            }
        )
        assert lead_id == "Ф-000001"
        assert storage.pending_leads() == []
        wb = load_workbook(tmp_path / "leads.xlsx")
        ws = wb["Заявки"]
        assert ws.cell(row=1, column=1).value == "ID заявки"
        row = [ws.cell(row=2, column=i).value for i in range(1, len(LEAD_FIELDS) + 1)]
        assert row[0] == "Ф-000001"
        assert row[3] == "+79161234567"
        assert row[20] == "да"
        wb.close()
    finally:
        storage.close()


def test_phone_dropped_without_consent(tmp_path):
    storage = make_storage(tmp_path)
    try:
        storage.create_lead(
            {"name": "Игорь", "phone": "+79161234567", "consent": False, "request_type": "question"}
        )
        wb = load_workbook(tmp_path / "leads.xlsx")
        ws = wb["Заявки"]
        phone_col = LEAD_FIELDS.index("phone") + 1
        consent_col = LEAD_FIELDS.index("consent") + 1
        assert ws.cell(row=2, column=phone_col).value is None
        assert ws.cell(row=2, column=consent_col).value == "нет"
        wb.close()
    finally:
        storage.close()


def test_excel_formula_injection_neutralized(tmp_path):
    storage = make_storage(tmp_path)
    try:
        storage.create_lead(
            {
                "name": "=HYPERLINK(\"http://evil.example\")",
                "phone": "+79161234567",
                "consent": True,
                "comment": "@SUM(1+1)",
            }
        )
        wb = load_workbook(tmp_path / "leads.xlsx")
        ws = wb["Заявки"]
        name_col = LEAD_FIELDS.index("name") + 1
        comment_col = LEAD_FIELDS.index("comment") + 1
        assert ws.cell(row=2, column=name_col).value.startswith("'=")
        assert ws.cell(row=2, column=comment_col).value.startswith("'@")
        wb.close()
    finally:
        storage.close()


def test_double_booking_prevented(tmp_path):
    storage = make_storage(tmp_path)
    try:
        booking = {
            "date": "2026-11-05",
            "time": "15:30",
            "duration": 30,
            "master": "Андрей",
            "service": "Замена носоупоров",
            "user_id": 1,
        }
        assert storage.create_booking(booking) is not None
        assert storage.create_booking(booking) is None
        # соседний слот свободен
        nxt = dict(booking, time="16:00")
        assert storage.create_booking(nxt) is not None
    finally:
        storage.close()


def test_lead_sequence_and_seen(tmp_path):
    storage = make_storage(tmp_path)
    try:
        assert storage.create_lead({"request_type": "question", "consent": False}) == "Ф-000001"
        assert storage.create_lead({"request_type": "question", "consent": False}) == "Ф-000002"
        assert storage.mark_seen("1:1") is True
        assert storage.mark_seen("1:1") is False
    finally:
        storage.close()


def test_history_window(tmp_path):
    storage = make_storage(tmp_path)
    try:
        storage.add_message(1, "user", "привет")
        storage.add_message(1, "assistant", "здравствуйте")
        history = storage.history(1)
        assert [m["role"] for m in history] == ["user", "assistant"]
        storage.reset_history(1)
        assert storage.history(1) == []
    finally:
        storage.close()
