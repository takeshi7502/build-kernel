"""Small, testable helpers for the interactive Nginx/HTTPS setup."""

import argparse
import ipaddress
import os
import re
import shutil
import socket
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT_DIR = Path(__file__).resolve().parent.parent
ACME_ROOT = "/var/www/gki-bot-acme"


def normalize_domain(value: str) -> str:
    value = value.strip()
    if not value or any(character.isspace() for character in value):
        raise ValueError("Nhập domain hợp lệ, ví dụ kernel.takeshi.dev.")
    parsed = urlsplit(value if "://" in value else "https://" + value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username
        or parsed.password
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Chỉ nhập domain hoặc URL http(s), không kèm cổng/đường dẫn.")
    domain = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
    labels = domain.split(".")
    if (
        len(domain) > 253
        or len(labels) < 2
        or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
    ):
        raise ValueError("Domain không hợp lệ.")
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        return domain
    raise ValueError("Nhập tên miền thay vì địa chỉ IP để cấu hình HTTPS.")


def render_nginx(domain: str, port: int, secure: bool = False, ipv6: bool = False) -> str:
    domain = normalize_domain(domain)
    if not 1 <= port <= 65535:
        raise ValueError("WEB_PORT phải nằm trong 1..65535.")
    challenge = f"""    location ^~ /.well-known/acme-challenge/ {{
        root {ACME_ROOT};
        default_type text/plain;
        try_files $uri =404;
    }}
"""
    proxy = f"""    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }}
"""
    config = "# Managed by build-kernel scripts/configure_web.sh\nserver {\n    listen 80;\n"
    if ipv6:
        config += "    listen [::]:80;\n"
    config += f"    server_name {domain};\n\n" + challenge
    config += (
        f"    location / {{ return 301 https://{domain}$request_uri; }}\n" if secure else proxy
    ) + "}\n"
    if secure:
        config += "\nserver {\n    listen 443 ssl;\n"
        if ipv6:
            config += "    listen [::]:443 ssl;\n"
        config += (
            f"""    server_name {domain};
    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:GKIBotSSL:10m;
    ssl_session_timeout 1d;

"""
            + proxy
            + "}\n"
        )
    return config


def update_env(path: Path, domain: str, email: str = ""):
    """Preserve unrelated settings and make one atomic replacement with backup."""
    from dotenv import set_key

    domain = normalize_domain(domain)
    if not path.is_file():
        raise ValueError("Thiếu .env; hãy chạy setup bot trước.")
    descriptor, temporary = tempfile.mkstemp(prefix=".web-config-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(path.read_text(encoding="utf-8"))
        values = {"WEB_HOST": "127.0.0.1", "DASHBOARD_URL": f"https://{domain}"}
        if email:
            values["CERTBOT_EMAIL"] = email
        for key, value in values.items():
            set_key(temporary, key, value, quote_mode="always")
        os.chmod(temporary, 0o600)
        backup = Path(str(path) + ".bak")
        shutil.copyfile(path, backup)
        os.chmod(backup, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def restore_env(path: Path):
    descriptor, temporary = tempfile.mkstemp(prefix=".web-config-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(Path(str(path) + ".bak").read_bytes())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["domain", "dns", "nginx", "env", "restore-env", "email", "port"]
    )
    parser.add_argument("value", nargs="?", default="")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--https", action="store_true", dest="secure")
    parser.add_argument("--ipv6", action="store_true")
    parser.add_argument("--email", default="")
    args = parser.parse_args()
    try:
        if args.command == "domain":
            print(normalize_domain(args.value))
        elif args.command == "dns":
            domain = normalize_domain(args.value)
            print(
                "\n".join(
                    sorted(
                        {
                            item[4][0]
                            for item in socket.getaddrinfo(domain, 80, type=socket.SOCK_STREAM)
                        }
                    )
                )
            )
        elif args.command == "nginx":
            print(render_nginx(args.value, args.port, args.secure, args.ipv6), end="")
        elif args.command in {"email", "port"}:
            from dotenv import dotenv_values

            values = dotenv_values(ROOT_DIR / ".env")
            if args.command == "email":
                print(values.get("CERTBOT_EMAIL") or "")
            else:
                port = int(os.environ.get("WEB_PORT") or values.get("WEB_PORT") or 5000)
                if not 1 <= port <= 65535:
                    raise ValueError("WEB_PORT phải nằm trong 1..65535.")
                print(port)
        elif args.command == "env":
            update_env(ROOT_DIR / ".env", args.value, args.email)
        elif args.command == "restore-env":
            restore_env(ROOT_DIR / ".env")
    except (OSError, ValueError, UnicodeError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
