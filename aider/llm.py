"""原生 SDK 共用配置。"""
import os

AIDER_SITE_URL = "https://aider.chat"
AIDER_APP_NAME = "Aider"
VERIFY_SSL = True
os.environ["OR_SITE_URL"] = AIDER_SITE_URL
os.environ["OR_APP_NAME"] = AIDER_APP_NAME


def client_options():
    if VERIFY_SSL:
        return {}
    import httpx

    return {"http_client": httpx.Client(verify=False)}
