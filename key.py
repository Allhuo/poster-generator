
import os
import secrets


# 或者您可以使用更高的位数来生成更安全的密钥
secure_secret_key = secrets.token_urlsafe(64)  # 更安全的选项

print(secure_secret_key)