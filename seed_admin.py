import os
import re
import sys

import bcrypt
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()


def main():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("ADMIN_PASSWORD", "")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required.")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("ADMIN_EMAIL must be a valid email.")
    if len(password) < 8:
        raise ValueError("ADMIN_PASSWORD must be at least 8 characters.")

    client = create_client(url, key)
    existing = client.table("users").select("*").eq("email", email).limit(1).execute().data
    if existing:
        client.table("users").update({
            "role": "admin",
            "name": existing[0].get("name") or "Administrator",
        }).eq("id", existing[0]["id"]).execute()
        print(f"Admin account {email} is ready.")
        return
    client.table("users").insert({
        "email": email,
        "name": "Administrator",
        "password_hash": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
        "role": "admin",
    }).execute()
    print(f"Created admin account {email}.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Failed to seed admin: {error}", file=sys.stderr)
        sys.exit(1)
