"""Create an OAuth client for a Custom GPT (ChatGPT → Configure → Actions → Authentication → OAuth).

Usage:  cd backend && ../.venv/bin/python -m scripts.create_gpt_client "老板的 GPT"
Prints client_id / client_secret once. The GPT's callback URL (shown in the GPT editor after saving) must start with
https://chat.openai.com/aip/ or https://chatgpt.com/aip/ — those prefixes are allowed for this client.
"""
import sys

from app.agent.oauth import create_admin_client
from app.db import Base, SessionLocal, engine

name = sys.argv[1] if len(sys.argv) > 1 else "Custom GPT"
Base.metadata.create_all(engine)
db = SessionLocal()
cid, secret = create_admin_client(db, name, ["https://chat.openai.com/aip/", "https://chatgpt.com/aip/"])
print(f"Client ID:     {cid}\nClient Secret: {secret}\n（Secret 只显示这一次）")
