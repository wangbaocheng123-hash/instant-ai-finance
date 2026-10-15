from __future__ import annotations

import getpass

from sunmao_community.auth import create_password_hash


def main() -> None:
    password = getpass.getpass("后台密码（至少 12 个字符）：")
    confirmation = getpass.getpass("再次输入后台密码：")
    if password != confirmation:
        raise SystemExit("两次输入的密码不一致")
    print(create_password_hash(password))


if __name__ == "__main__":
    main()
