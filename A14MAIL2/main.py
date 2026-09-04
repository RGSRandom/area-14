from werkzeug.security import generate_password_hash, check_password_hash
import json
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(BASE_DIR, "users.json")

DEFAULT_USERS = [
    {
        "email": "super_admin@SCiP.net",
        "password": "1234",
        "name": "Super Admin",
        "role": "super_admin"
    },
    {
        "email": "admin@SCiP.net",
        "password": "1234",
        "name": "Admin",
        "role": "admin"
    },
    {
        "email": "raisa@SCiP.net",
        "password": "1234",
        "name": "RAISA Officer",
        "role": "raisa"
    },
    {
        "email": "B1ExecutiveCommand@SCiP.net",
        "password": "scrypt:32768:8:1$GgccqAqgpfGJYzXz$ab32be3b45bcd405251495d3206f43a35efb3dea4a1923532f01165414c7bd039552df1c064703ff72bf94d1b3b937dcf7b67785d7ae0f5002eed67c91b53762",
        "name": "Anton",
        "role": "user"
    },
    {
        "email": "A1ExecutiveCommand@SCiP.net",
        "password": "scrypt:32768:8:1$Sk6Z30TanZo85u7i$e9f59ada34654f5744c2628e38585358ea18b7b959a4498207d67cf88c0e2dbba3cfb0cc7420e33682ed46102c8376025e7bb0ab0abb8f7d85bbf3b3e19a09fc",
        "name": "Anton",
        "role": "user"
    }
]

def is_hashed(password: str) -> bool:
    return password.startswith(("scrypt:", "pbkdf2:", "argon2:"))

def load_or_create_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r") as f:
            users = json.load(f)
        print("[*] Loaded existing users.json")
    else:
        print("[*] users.json not found — creating default users")
        users = DEFAULT_USERS

    updated = False
    final_users = []

    for user in users:
        password = user.get("password", "")

        if not is_hashed(password):
            # Hash plain text password
            user["password"] = generate_password_hash(password)
            updated = True
            print(f"  → Hashed password for: {user['email']}")
        else:
            print(f"  → Already hashed: {user['email']}")

        # Make sure required fields exist
        user.setdefault("name", user["email"])
        user.setdefault("role", "user")

        final_users.append(user)

    if updated or not os.path.exists(USERS_FILE):
        with open(USERS_FILE, "w") as f:
            json.dump(final_users, f, indent=4)
        print("\n[+] users.json saved")
    else:
        print("\n[+] No changes needed")

    return final_users

def main():
    print("=================================")
    print("       A14MAIL Starting (Enhanced)")
    print("=================================\n")

    users = load_or_create_users()

    print("\nCurrent users:")
    for u in users:
        role = u.get('role', 'user')
        print(f"  - {u['email']:40} ({role})")

    print("\nAvailable roles:")
    print("  • user        - Regular user (can send/receive emails)")
    print("  • admin       - Can manage users, change user passwords, view all emails, set clearances")
    print("  • super_admin - Can manage admins, users, change all passwords, view all emails")
    print("  • raisa       - Can change email clearance levels (cl0-cl4)")

    if not os.path.exists("emails.json"):
        with open("emails.json", "w") as f:
            json.dump([], f)
        print("\n[+] Created empty emails.json")

    print("\n[*] Starting server → http://127.0.0.1:5000\n")
    subprocess.run([sys.executable, "app.py"])

if __name__ == "__main__":
    main()
