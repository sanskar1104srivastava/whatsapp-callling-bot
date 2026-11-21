"""Write the current use cases + routing settings into the voice agent's bundled snapshot.
Run from ai-calling/backend: python export_agent_data.py   (then redeploy the agent)."""
import json
from pathlib import Path

from app.ai_calling import store

out = Path(__file__).resolve().parents[2] / "inbound-agent" / "src" / "usecases.json"
out.write_text(json.dumps({"usecases": store.list_usecases(), "settings": store.get_settings()}, ensure_ascii=False, indent=2), encoding="utf-8")
print("wrote", out)
