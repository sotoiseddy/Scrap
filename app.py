import hmac
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from functools import wraps

import bcrypt
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

load_dotenv()

EVENT_COLORS = ("blue", "red", "green", "yellow", "purple")
COLOR_LABELS = {
    "blue": "General",
    "red": "Urgent",
    "green": "Assignment",
    "yellow": "Quiz / Lab",
    "purple": "Announcement",
}
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
BUCKET_NAME = os.getenv("SUPABASE_STORAGE_BUCKET", "class-documents")
PROFANE_TERMS = (
    "fuck", "fck", "fvck", "fuk", "fucker", "fucking", "shit", "sh1t", "bitch",
    "bastard", "dick", "dickhead", "pussy", "asshole", "a$$hole", "ass",
    "motherfucker", "whore", "slut", "cunt", "cock", "jerkoff", "madarchod",
    "maderchod", "maa chod", "ma chod", "behenchod", "bhenchod", "bhen chhod",
    "chutiya", "chutia", "chutiyapa", "gandu", "gand", "harami", "randi",
    "lund", "lauda", "lawda", "lavda", "lavde", "gaand", "chodu", "chhod",
    "bhadwa", "kamina", "kameena", "suar", "kutti", "kutta", "teri maa di",
    "teri ma di", "bhen de", "bhen da", "pen de", "pen da", "puth", "puttar",
    "chup kar lavde",
)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or os.getenv("AUTH_SECRET")
if not app.secret_key:
    raise RuntimeError("Set FLASK_SECRET_KEY (or AUTH_SECRET) before starting the app.")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("VERCEL") == "1" or os.getenv("FLASK_ENV") == "production",
    MAX_CONTENT_LENGTH=1024 * 1024,
)

_supabase = None


def db():
    global _supabase
    if _supabase is None:
        from supabase import create_client

        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            raise RuntimeError("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.")
        _supabase = create_client(url, key)
    return _supabase


def table(name):
    return db().table(name)


def current_user():
    return getattr(g, "user", None)


def local_today():
    try:
        offset = int(request.cookies.get("tz_offset_minutes", "0"))
    except ValueError:
        offset = 0
    offset = max(-840, min(840, offset))
    return (datetime.now(timezone.utc) - timedelta(minutes=offset)).date()


@app.before_request
def load_user_and_check_csrf():
    user_id = session.get("user_id")
    g.user = None
    if user_id:
        response = table("users").select("*").eq("id", user_id).limit(1).execute().data
        if not response or response[0].get("is_banned"):
            session.clear()
        else:
            g.user = response[0]

    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        submitted = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
        expected = session.get("csrf_token", "")
        if not expected or not hmac.compare_digest(submitted, expected):
            abort(400, description="Invalid or missing CSRF token.")


@app.context_processor
def template_context():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return {
        "user": current_user(),
        "csrf_token": session["csrf_token"],
        "color_labels": COLOR_LABELS,
        "event_colors": EVENT_COLORS,
        "today": local_today().isoformat(),
        "supabase_url": os.getenv("SUPABASE_URL", ""),
        "supabase_anon_key": os.getenv("SUPABASE_ANON_KEY", ""),
    }


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Please sign in to continue.", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if current_user()["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def can_manage_class(class_id):
    user = current_user()
    return bool(user and (user["role"] == "admin" or user.get("assigned_class_id") == class_id))


def form_text(name, *, required=False, maximum=None):
    value = request.form.get(name, "").strip()
    if required and not value:
        raise ValueError(f"{name.replace('_', ' ').capitalize()} is required.")
    if maximum and len(value) > maximum:
        raise ValueError(f"{name.replace('_', ' ').capitalize()} must be at most {maximum} characters.")
    return value


def classes_list():
    return table("classes").select("*").order("name").execute().data


def find_one(name, record_id):
    rows = table(name).select("*").eq("id", record_id).limit(1).execute().data
    return rows[0] if rows else None


def normalize_content(value):
    value = value.lower().replace("’", "'").replace("‘", "'")
    value = re.sub(r"[a-z0-9@$!]", lambda m: {
        "@": "a", "4": "a", "3": "e", "0": "o", "1": "i",
        "!": "i", "5": "s", "$": "s",
    }.get(m.group(0), m.group(0)), value)
    value = re.sub(r"[^a-z0-9]+", "", value)
    value = re.sub(r"(.)\1+", r"\1", value)
    return value.replace("fvck", "fuck")


def is_profane(value):
    if not value:
        return False
    normalized = normalize_content(value)
    for word in PROFANE_TERMS:
        term = normalize_content(word)
        if word == "ass":
            if any(normalize_content(token) == term for token in value.split()):
                return True
        elif term and term in normalized:
            return True
    return False


def flag_submission(user, class_id, title, description, color, event_date):
    table("flagged_events").insert({
        "class_id": class_id,
        "title": title[:200],
        "description": description[:2000] if description else None,
        "color": color,
        "event_date": event_date,
        "submitted_by": user["id"],
    }).execute()


def get_schedule(class_id, offset):
    today = local_today()
    anchor = today + timedelta(days=offset)
    end = anchor + timedelta(days=6)
    events = table("events").select("*").eq("class_id", class_id).gte(
        "event_date", anchor.isoformat()
    ).lte("event_date", end.isoformat()).order("created_at", desc=True).execute().data
    by_day = {}
    for event in events:
        by_day.setdefault(event["event_date"], []).append(event)
    days = []
    for index in range(7):
        day = anchor + timedelta(days=index)
        days.append({
            "date": day,
            "date_key": day.isoformat(),
            "label": "Today" if day == today else day.strftime("%A"),
            "events": by_day.get(day.isoformat(), []),
        })
    return anchor, end, days


@app.get("/")
def home():
    all_classes = classes_list()
    assigned = current_user().get("assigned_class_id") if current_user() else None
    ids = {item["id"] for item in all_classes}
    selected_id = request.args.get("class_id")
    if selected_id not in ids:
        selected_id = assigned if assigned in ids else (all_classes[0]["id"] if all_classes else None)
    try:
        offset = int(request.args.get("offset", "0"))
    except ValueError:
        abort(400, description="offset must be a number of days.")
    offset = max(-3650, min(3650, (offset // 7) * 7))
    selected = next((item for item in all_classes if item["id"] == selected_id), None)
    anchor, end, days = get_schedule(selected_id, offset) if selected else (None, None, [])
    return render_template(
        "index.html", classes=all_classes, selected=selected, days=days,
        anchor=anchor, end=end, offset=offset,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("home"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        rows = table("users").select("*").eq("email", email).limit(1).execute().data
        if rows and bcrypt.checkpw(password.encode(), rows[0]["password_hash"].encode()):
            if rows[0].get("is_banned"):
                flash("This account has been banned.", "error")
            else:
                session.clear()
                session["user_id"] = rows[0]["id"]
                session["csrf_token"] = secrets.token_urlsafe(32)
                flash("Signed in.", "success")
                target = request.args.get("next", "")
                if target.startswith("/") and not target.startswith("//"):
                    return redirect(target)
                return redirect(url_for("home"))
        else:
            flash("Email or password is incorrect.", "error")
    return render_template("login.html")


@app.post("/logout")
@login_required
def logout():
    session.clear()
    flash("Signed out.", "success")
    return redirect(url_for("home"))


@app.post("/events/create")
@login_required
def create_event():
    class_id = request.form.get("class_id", "")
    if not can_manage_class(class_id):
        abort(403)
    try:
        title = form_text("title", required=True, maximum=200)
        description = form_text("description", maximum=2000) or None
        event_date = date.fromisoformat(form_text("event_date", required=True))
        color = request.form.get("color", "blue")
        if color not in EVENT_COLORS:
            raise ValueError("Choose a valid event color.")
        if is_profane(title) or is_profane(description):
            flag_submission(current_user(), class_id, title, description, color, event_date.isoformat())
            flash("That event was blocked and sent to the admin moderation queue.", "error")
            return redirect(url_for("home", class_id=class_id))
        table("events").insert({
            "class_id": class_id, "title": title, "description": description,
            "color": color, "event_date": event_date.isoformat(),
            "created_by": current_user()["id"],
        }).execute()
        flash("Event added.", "success")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(url_for("home", class_id=class_id))


@app.route("/events/<event_id>/edit", methods=["GET", "POST"])
@login_required
def edit_event(event_id):
    event = find_one("events", event_id)
    if not event:
        abort(404)
    if not can_manage_class(event["class_id"]):
        abort(403)
    if request.method == "POST":
        try:
            title = form_text("title", required=True, maximum=200)
            description = form_text("description", maximum=2000) or None
            event_date = date.fromisoformat(form_text("event_date", required=True))
            color = request.form.get("color", "")
            if color not in EVENT_COLORS:
                raise ValueError("Choose a valid event color.")
            if is_profane(title) or is_profane(description):
                flag_submission(current_user(), event["class_id"], title, description, color, event_date.isoformat())
                flash("That change was blocked and sent to the admin moderation queue.", "error")
            else:
                table("events").update({
                    "title": title, "description": description, "event_date": event_date.isoformat(),
                    "color": color, "updated_by": current_user()["id"],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", event_id).execute()
                flash("Event updated.", "success")
        except ValueError as error:
            flash(str(error), "error")
        return redirect(url_for("home", class_id=event["class_id"]))
    return render_template("event_form.html", event=event, class_id=event["class_id"])


@app.post("/events/<event_id>/delete")
@login_required
def delete_event(event_id):
    event = find_one("events", event_id)
    if not event:
        abort(404)
    if not can_manage_class(event["class_id"]):
        abort(403)
    table("events").delete().eq("id", event_id).execute()
    flash("Event deleted.", "success")
    return redirect(url_for("home", class_id=event["class_id"]))


@app.get("/documents")
def documents():
    all_classes = classes_list()
    assigned = current_user().get("assigned_class_id") if current_user() else None
    ids = {item["id"] for item in all_classes}
    selected_id = request.args.get("class_id")
    if selected_id not in ids:
        selected_id = assigned if assigned in ids else (all_classes[0]["id"] if all_classes else None)
    selected = next((item for item in all_classes if item["id"] == selected_id), None)
    items = table("documents").select("*").eq("class_id", selected_id).order(
        "created_at", desc=True
    ).execute().data if selected else []
    return render_template("documents.html", classes=all_classes, selected=selected, documents=items)


@app.post("/documents/upload-sign")
@login_required
def sign_document_upload():
    class_id = request.form.get("class_id", "")
    if not can_manage_class(class_id):
        abort(403)
    file_name = secure_filename(request.form.get("file_name", "").strip())
    mime = request.form.get("mime_type", "")
    try:
        size_bytes = int(request.form.get("size_bytes", "0"))
    except ValueError:
        abort(400, description="The file size must be a number.")
    if not file_name:
        abort(400, description="A valid file name is required.")
    if mime not in ALLOWED_MIME_TYPES:
        abort(400, description="Unsupported file type.")
    if size_bytes < 1 or size_bytes > MAX_DOCUMENT_BYTES:
        abort(400, description="Files must be between 1 byte and 10 MB.")
    path = f"{class_id}/{secrets.token_hex(16)}-{file_name}"
    signed = db().storage.from_(BUCKET_NAME).create_signed_upload_url(path)
    data = signed if isinstance(signed, dict) else signed.model_dump()
    signed_url = data.get("signed_url") or data.get("signedURL") or data.get("signedUrl") or data.get("url")
    token = data.get("token")
    if not signed_url or not token:
        app.logger.error("Supabase did not return a usable signed upload URL.")
        abort(502, description="Could not prepare a secure document upload.")
    return {
        "path": path,
        "token": token,
        "signed_url": signed_url,
        "class_id": class_id,
        "file_name": file_name,
        "mime_type": mime,
        "size_bytes": size_bytes,
    }


@app.post("/documents/upload-finish")
@login_required
def finish_document_upload():
    data = request.get_json(silent=False)
    if not isinstance(data, dict):
        abort(400, description="Upload metadata is required.")
    class_id = str(data.get("class_id", ""))
    path = str(data.get("path", ""))
    if not can_manage_class(class_id):
        abort(403)
    if not path.startswith(f"{class_id}/"):
        abort(400, description="Invalid uploaded file path.")
    file_name = secure_filename(str(data.get("file_name", "")))
    mime = str(data.get("mime_type", ""))
    try:
        size_bytes = int(data.get("size_bytes", 0))
    except (TypeError, ValueError):
        abort(400, description="Invalid file size.")
    if not file_name or mime not in ALLOWED_MIME_TYPES or not (1 <= size_bytes <= MAX_DOCUMENT_BYTES):
        abort(400, description="Invalid document metadata.")
    title = os.path.splitext(file_name)[0].replace("_", " ").replace("-", " ").strip() or file_name
    try:
        table("documents").insert({
            "class_id": class_id, "title": title,
            "file_name": file_name, "mime_type": mime,
            "size_bytes": size_bytes, "storage_path": path,
            "uploaded_by": current_user()["id"],
        }).execute()
    except Exception:
        app.logger.exception("Could not save metadata for uploaded document.")
        try:
            db().storage.from_(BUCKET_NAME).remove([path])
        except Exception:
            app.logger.exception("Could not clean up unregistered uploaded object %s", path)
        abort(502, description="The upload finished, but could not be registered.")
    return {"ok": True}


@app.get("/documents/<document_id>/file")
def serve_document(document_id):
    document = find_one("documents", document_id)
    if not document:
        abort(404)
    try:
        signed = db().storage.from_(BUCKET_NAME).create_signed_url(
            document["storage_path"], 3600
        )
    except Exception:
        app.logger.exception("Document download failed for %s", document_id)
        abort(502, description="Document storage could not return this file.")
    data = signed if isinstance(signed, dict) else signed.model_dump()
    signed_url = data.get("signedURL") or data.get("signedUrl")
    if not signed_url:
        app.logger.error("Supabase did not return a usable signed download URL.")
        abort(502, description="Could not prepare a secure document link.")
    return redirect(signed_url, code=302)


@app.post("/documents/<document_id>/delete")
@login_required
def delete_document(document_id):
    document = find_one("documents", document_id)
    if not document:
        abort(404)
    if not can_manage_class(document["class_id"]):
        abort(403)
    try:
        db().storage.from_(BUCKET_NAME).remove([document["storage_path"]])
        table("documents").delete().eq("id", document_id).execute()
    except Exception:
        app.logger.exception("Document delete failed for %s", document_id)
        abort(502, description="Could not delete the document from storage.")
    flash("Document deleted.", "success")
    return redirect(url_for("documents", class_id=document["class_id"]))


@app.get("/admin")
@admin_required
def admin():
    all_classes = classes_list()
    users = table("users").select("id,email,name,role,assigned_class_id,is_banned,created_at").order(
        "created_at", desc=True
    ).execute().data
    flagged = table("flagged_events").select("*").order("submitted_at", desc=True).execute().data
    class_id = request.args.get("class_id")
    if class_id not in {item["id"] for item in all_classes}:
        class_id = all_classes[0]["id"] if all_classes else None
    try:
        offset = max(-3650, min(3650, (int(request.args.get("offset", "0")) // 7) * 7))
    except ValueError:
        abort(400, description="offset must be a number of days.")
    selected = next((item for item in all_classes if item["id"] == class_id), None)
    anchor, end, days = get_schedule(class_id, offset) if selected else (None, None, [])
    return render_template(
        "admin.html", classes=all_classes, users=users, flagged=flagged,
        selected=selected, days=days, anchor=anchor, end=end, offset=offset,
        class_names={item["id"]: item["name"] for item in all_classes},
    )


@app.post("/admin/classes/create")
@admin_required
def create_class():
    try:
        name = form_text("name", required=True, maximum=100)
        description = form_text("description", maximum=1000) or None
        if table("classes").select("id").ilike("name", name).execute().data:
            raise ValueError("A class with that name already exists.")
        table("classes").insert({"name": name, "description": description}).execute()
        flash("Class created.", "success")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(url_for("admin"))


@app.route("/admin/classes/<class_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_class(class_id):
    item = find_one("classes", class_id)
    if not item:
        abort(404)
    if request.method == "POST":
        try:
            name = form_text("name", required=True, maximum=100)
            description = form_text("description", maximum=1000) or None
            duplicate = table("classes").select("id").ilike("name", name).neq("id", class_id).execute().data
            if duplicate:
                raise ValueError("A class with that name already exists.")
            table("classes").update({"name": name, "description": description}).eq("id", class_id).execute()
            flash("Class updated.", "success")
            return redirect(url_for("admin"))
        except ValueError as error:
            flash(str(error), "error")
    return render_template("class_form.html", class_item=item)


@app.post("/admin/classes/<class_id>/delete")
@admin_required
def delete_class(class_id):
    if not find_one("classes", class_id):
        abort(404)
    docs = table("documents").select("storage_path").eq("class_id", class_id).execute().data
    try:
        if docs:
            db().storage.from_(BUCKET_NAME).remove([doc["storage_path"] for doc in docs])
        table("classes").delete().eq("id", class_id).execute()
    except Exception:
        app.logger.exception("Class deletion failed for %s", class_id)
        abort(502, description="Could not delete class and its uploaded documents.")
    flash("Class and its events/documents deleted.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/users/create", methods=["POST"])
@admin_required
def create_user():
    try:
        name = form_text("name", required=True, maximum=100)
        email = form_text("email", required=True, maximum=254).lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "")
        assigned_class_id = request.form.get("assigned_class_id") or None
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Enter a valid email address.")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        if role not in {"admin", "class_manager"}:
            raise ValueError("Choose a valid role.")
        if role != "class_manager":
            assigned_class_id = None
        if assigned_class_id and not find_one("classes", assigned_class_id):
            raise ValueError("Choose a valid assigned class.")
        if table("users").select("id").eq("email", email).execute().data:
            raise ValueError("An account with that email already exists.")
        table("users").insert({
            "email": email, "name": name,
            "password_hash": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
            "role": role, "assigned_class_id": assigned_class_id,
        }).execute()
        flash("User created.", "success")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(url_for("admin"))


@app.route("/admin/users/<user_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_user(user_id):
    item = find_one("users", user_id)
    if not item:
        abort(404)
    if request.method == "POST":
        try:
            name = form_text("name", required=True, maximum=100)
            role = request.form.get("role", "")
            assigned_class_id = request.form.get("assigned_class_id") or None
            password = request.form.get("password", "")
            if role not in {"admin", "class_manager"}:
                raise ValueError("Choose a valid role.")
            if role != "class_manager":
                assigned_class_id = None
            if assigned_class_id and not find_one("classes", assigned_class_id):
                raise ValueError("Choose a valid assigned class.")
            changes = {"name": name, "role": role, "assigned_class_id": assigned_class_id}
            if password:
                if len(password) < 8:
                    raise ValueError("Password must be at least 8 characters.")
                changes["password_hash"] = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            table("users").update(changes).eq("id", user_id).execute()
            flash("User updated.", "success")
            return redirect(url_for("admin"))
        except ValueError as error:
            flash(str(error), "error")
    return render_template("user_form.html", user_item=item, classes=classes_list())


@app.post("/admin/users/<user_id>/delete")
@admin_required
def delete_user(user_id):
    if user_id == current_user()["id"]:
        flash("You cannot delete your own account.", "error")
    elif find_one("users", user_id):
        table("users").delete().eq("id", user_id).execute()
        flash("User deleted.", "success")
    return redirect(url_for("admin"))


@app.post("/admin/users/<user_id>/ban")
@admin_required
def ban_user(user_id):
    if user_id == current_user()["id"]:
        flash("You cannot ban your own account.", "error")
    elif not find_one("users", user_id):
        abort(404)
    else:
        table("users").update({"is_banned": True}).eq("id", user_id).execute()
        flash("User banned.", "success")
    return redirect(url_for("admin"))


@app.post("/admin/users/<user_id>/unban")
@admin_required
def unban_user(user_id):
    if not find_one("users", user_id):
        abort(404)
    table("users").update({"is_banned": False}).eq("id", user_id).execute()
    flash("User unbanned.", "success")
    return redirect(url_for("admin"))


@app.post("/admin/flagged/<flag_id>/review")
@admin_required
def review_flag(flag_id):
    if not find_one("flagged_events", flag_id):
        abort(404)
    table("flagged_events").update({"is_reviewed": True}).eq("id", flag_id).execute()
    flash("Submission marked reviewed.", "success")
    return redirect(url_for("admin"))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.errorhandler(403)
def forbidden(_error):
    if request.path.startswith("/documents/upload-"):
        return jsonify(error="forbidden", message="You do not have permission to do that."), 403
    return render_template("error.html", code=403, message="You do not have permission to do that."), 403


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="The page or record was not found."), 404


@app.errorhandler(400)
def bad_request(error):
    if request.path.startswith("/documents/upload-"):
        return jsonify(error="bad_request", message=getattr(error, "description", "Bad request.")), 400
    return render_template("error.html", code=400, message=getattr(error, "description", "Bad request.")), 400


@app.errorhandler(413)
def too_large(_error):
    return render_template("error.html", code=413, message="The request is too large."), 413


@app.errorhandler(502)
def upstream_error(error):
    if request.path.startswith("/documents/upload-"):
        return jsonify(error="storage_error", message=getattr(error, "description", "The storage service failed.")), 502
    return render_template("error.html", code=502, message=getattr(error, "description", "The storage service failed.")), 502


@app.errorhandler(500)
def internal_error(_error):
    app.logger.exception("Unhandled application error")
    if request.path.startswith("/documents/upload-"):
        return jsonify(error="internal_error", message="The upload could not be completed."), 500
    return render_template("error.html", code=500, message="An unexpected server error occurred."), 500


@app.template_filter("short_date")
def short_date(value):
    return f"{value.strftime('%b')} {value.day}"


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1")
