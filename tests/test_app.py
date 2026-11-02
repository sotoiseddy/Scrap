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

    def update(self, values):
        INSERTED.append((self.name, values))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def in_(self, key, values):
        self.filters[key] = values
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
        if self.name == "tests":
            records = [
                {
                    "test_id": "test-confirmed", "title": "Physics exam", "subject": "Physics",
                    "teacher": "Teacher", "due_date": application.date.today().isoformat(),
                    "assigned_date": None, "description": None, "details_text": None,
                    "source_url": "https://example.com", "date_status": "confirmed",
                },
                {
                    "test_id": "test-inferred", "title": "Chemistry exam", "subject": "Chemistry",
                    "due_date": application.date.today().isoformat(), "date_status": "inferred",
                },
                {
                    "test_id": "test-review", "title": "Unconfirmed exam",
                    "due_date": application.date.today().isoformat(), "date_status": "needs_review",
                },
            ]
            test_id = self.filters.get("test_id")
            if test_id:
                records = [record for record in records if record["test_id"] == test_id]
            statuses = self.filters.get("date_status")
            return type("Result", (), {"data": [
                record for record in records
                if statuses is None or (
                    record["date_status"] in statuses
                    if isinstance(statuses, list)
                    else record["date_status"] == statuses
                )
            ]})()
        if self.name == "events":
            return type("Result", (), {"data": [{
                "id": "event-1", "class_id": "class-1", "title": "Math test",
                "description": "Chapter 2", "color": "red",
                "event_date": application.date.today().isoformat(),
                "created_by": "manager-1",
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
                user_ids = self.filters["id"]
                if not isinstance(user_ids, list):
                    user_ids = [user_ids]
                return type("Result", (), {"data": [
                    records[user_id] for user_id in user_ids if user_id in records
                ]})()
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
        self.assertIn(b"Physics exam", response.data)
        self.assertIn(b"Chemistry exam", response.data)
        self.assertNotIn(b"Unconfirmed exam", response.data)
        self.assertIn(b"Automated", response.data)
        self.assertNotIn(b">Source<", response.data)
        self.assertIn(b'class="event-card event-red"', response.data)
        self.assertIn(b'<span class="event-creator">Manager</span>', response.data)
        self.assertNotIn(b"manager@example.com", response.data)
        self.assertIn(b"Nothing scheduled", response.data)

    def test_public_document_library_renders(self):
        response = self.client.get("/documents")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Class resources", response.data)

    def test_public_frontend_assets_are_served_from_root_paths(self):
        css = self.client.get("/app.css")
        js = self.client.get("/app.js")
        self.assertEqual(css.status_code, 200)
        self.assertIn(b".site-header", css.data)
        self.assertEqual(js.status_code, 200)
        self.assertIn(b"data-upload-form", js.data)
        css.close()
        js.close()

    def test_admin_dashboard_renders_for_admin(self):
        with self.client.session_transaction() as session:
            session["user_id"] = "admin-1"
            session["csrf_token"] = "csrf-test-token"
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Admin dashboard", response.data)
        self.assertIn(b'<span class="event-creator">Manager</span>', response.data)
        self.assertIn(b"Physics exam", response.data)
        self.assertIn(b"Automated", response.data)
        self.assertIn(b'href="/admin/tests/test-confirmed/edit">Edit</a>', response.data)
        self.assertLess(
            response.data.index(b"Class management"),
            response.data.index(b"Tests needing date review"),
        )
        self.assertLess(
            response.data.index(b"Tests needing date review"),
            response.data.index(b"User provisioning"),
        )

    def test_admin_can_edit_tests_from_main_page(self):
        with self.client.session_transaction() as session:
            session["user_id"] = "admin-1"
            session["csrf_token"] = "csrf-test-token"
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'href="/admin/tests/test-confirmed/edit">Edit</a>', response.data)

    def test_class_manager_cannot_edit_tests_from_main_page(self):
        with self.client.session_transaction() as session:
            session["user_id"] = "manager-1"
            session["csrf_token"] = "csrf-test-token"
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'href="/admin/tests/test-confirmed/edit">Edit</a>', response.data)

    def test_admin_can_edit_test(self):
        with self.client.session_transaction() as session:
            session["user_id"] = "admin-1"
            session["csrf_token"] = "csrf-test-token"
        response = self.client.post("/admin/tests/test-confirmed/edit", data={
            "csrf_token": "csrf-test-token",
            "title": "Updated physics exam",
            "subject": "Physics",
            "teacher": "Teacher",
            "assigned_date": "",
            "due_date": application.date.today().isoformat(),
            "description": "",
            "details_text": "Updated details",
            "date_status": "rejected",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(INSERTED[-1][0], "tests")
        self.assertEqual(INSERTED[-1][1]["title"], "Updated physics exam")
        self.assertEqual(INSERTED[-1][1]["details_text"], "Updated details")
        self.assertEqual(INSERTED[-1][1]["date_status"], "rejected")

    def test_class_manager_cannot_edit_test(self):
        with self.client.session_transaction() as session:
            session["user_id"] = "manager-1"
            session["csrf_token"] = "csrf-test-token"
        response = self.client.get("/admin/tests/test-confirmed/edit")
        self.assertEqual(response.status_code, 403)

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

    def test_event_with_previously_blocked_content_is_created(self):
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
        self.assertEqual(INSERTED[0][0], "events")
        self.assertEqual(INSERTED[0][1]["title"], "sh1t")

    def test_admin_dashboard_lists_tests_needing_review(self):
        with self.client.session_transaction() as session:
            session["user_id"] = "admin-1"
            session["csrf_token"] = "csrf-test-token"
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Tests needing date review", response.data)
        self.assertIn(b"Unconfirmed exam", response.data)
        self.assertIn(b'href="/admin/tests/test-review/edit">Edit and review</a>', response.data)

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
