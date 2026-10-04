import os
import unittest
from unittest.mock import patch

os.environ.setdefault("FLASK_SECRET_KEY", "unit-test-secret")

import app as application

INSERTED = []


class FakeQuery:
    def __init__(self, name):
        self.name = name
        self.filters = {}

    def select(self, *_args, **_kwargs):
        return self

    def insert(self, values):
        INSERTED.append((self.name, values))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def gte(self, *_args, **_kwargs):
        return self

    def lte(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self.name == "classes":
            return type("Result", (), {"data": [{"id": "class-1", "name": "Class 1", "description": None}]})()
        if self.name == "events":
            return type("Result", (), {"data": [{
                "id": "event-1", "class_id": "class-1", "title": "Math test",
                "description": "Chapter 2", "color": "red",
                "event_date": application.date.today().isoformat(),
            }]})()
        if self.name == "documents" and self.filters.get("id") == "document-1":
            return type("Result", (), {"data": [{
                "id": "document-1", "storage_path": "class-1/worksheet.pdf",
            }]})()
        if self.name == "users":
            records = {
                "manager-1": {
                    "id": "manager-1", "email": "manager@example.com", "name": "Manager",
                    "role": "class_manager", "assigned_class_id": "class-1", "is_banned": False,
                },
                "admin-1": {
                    "id": "admin-1", "email": "admin@example.com", "name": "Admin",
                    "role": "admin", "assigned_class_id": None, "is_banned": False,
                },
            }
            if self.filters.get("id"):
                record = records.get(self.filters["id"])
                return type("Result", (), {"data": [record] if record else []})()
            return type("Result", (), {"data": []})()
        return type("Result", (), {"data": []})()


class FlaskAppTests(unittest.TestCase):
    def setUp(self):
        self.client = application.app.test_client()
        INSERTED.clear()
        self.table_patch = patch.object(application, "table", side_effect=lambda name: FakeQuery(name))
        self.table_patch.start()
        self.addCleanup(self.table_patch.stop)

    def test_public_home_shows_class_week_and_events(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Class 1", response.data)
        self.assertIn(b"Math test", response.data)
        self.assertIn(b"Nothing scheduled", response.data)

    def test_public_document_library_renders(self):
        response = self.client.get("/documents")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Class resources", response.data)

    def test_admin_dashboard_renders_for_admin(self):
        with self.client.session_transaction() as session:
            session["user_id"] = "admin-1"
            session["csrf_token"] = "csrf-test-token"
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Admin dashboard", response.data)

    def test_profane_content_is_detected_with_common_evasions(self):
        self.assertTrue(application.is_profane("This is sh1t"))
        self.assertTrue(application.is_profane("f.v.c.k"))
        self.assertFalse(application.is_profane("class assignment"))

    def test_mutation_requires_csrf_token(self):
        response = self.client.post("/events/create", data={"class_id": "class-1"})
        self.assertEqual(response.status_code, 400)

    def test_manager_cannot_create_events_for_another_class(self):
        with self.client.session_transaction() as session:
            session["user_id"] = "manager-1"
            session["csrf_token"] = "csrf-test-token"
        response = self.client.post("/events/create", data={
            "csrf_token": "csrf-test-token",
            "class_id": "other-class",
            "title": "Restricted",
            "event_date": application.date.today().isoformat(),
            "color": "blue",
        })
        self.assertEqual(response.status_code, 403)

    def test_manager_can_create_an_event_for_assigned_class(self):
        with self.client.session_transaction() as session:
            session["user_id"] = "manager-1"
            session["csrf_token"] = "csrf-test-token"
        response = self.client.post("/events/create", data={
            "csrf_token": "csrf-test-token",
            "class_id": "class-1",
            "title": "Science quiz",
            "event_date": application.date.today().isoformat(),
            "color": "yellow",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(INSERTED[0][0], "events")
        self.assertEqual(INSERTED[0][1]["title"], "Science quiz")

    def test_blocked_event_is_added_to_moderation_queue(self):
        with self.client.session_transaction() as session:
            session["user_id"] = "manager-1"
            session["csrf_token"] = "csrf-test-token"
        response = self.client.post("/events/create", data={
            "csrf_token": "csrf-test-token",
            "class_id": "class-1",
            "title": "sh1t",
            "event_date": application.date.today().isoformat(),
            "color": "red",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(INSERTED[0][0], "flagged_events")
        self.assertEqual(INSERTED[0][1]["title"], "sh1t")

    def test_signed_upload_returns_direct_supabase_upload_data(self):
        class FakeBucket:
            def create_signed_upload_url(self, path):
                return {
                    "signed_url": f"https://example.supabase.co/storage/v1/object/upload/sign/{path}?token=upload-token",
                    "token": "upload-token",
                }

        class FakeStorage:
            def from_(self, _bucket):
                return FakeBucket()

        class FakeSupabase:
            storage = FakeStorage()

        with self.client.session_transaction() as session:
            session["user_id"] = "manager-1"
            session["csrf_token"] = "csrf-test-token"
        with patch.object(application, "db", return_value=FakeSupabase()):
            response = self.client.post("/documents/upload-sign", data={
                "csrf_token": "csrf-test-token",
                "class_id": "class-1",
                "file_name": "worksheet.pdf",
                "mime_type": "application/pdf",
                "size_bytes": "100",
            })
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"upload-token", response.data)
        self.assertIn(b"signed_url", response.data)

    def test_public_document_route_redirects_to_temporary_storage_url(self):
        class FakeBucket:
            def create_signed_url(self, _path, _expires):
                return {"signedURL": "https://example.supabase.co/signed-document"}

        class FakeStorage:
            def from_(self, _bucket):
                return FakeBucket()

        class FakeSupabase:
            storage = FakeStorage()

        with patch.object(application, "db", return_value=FakeSupabase()):
            response = self.client.get("/documents/document-1/file")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "https://example.supabase.co/signed-document")


if __name__ == "__main__":
    unittest.main()
