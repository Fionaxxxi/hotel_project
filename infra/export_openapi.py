import json
from pathlib import Path

from services.core.main import create_app

target = Path(__file__).resolve().parents[1] / "contracts/openapi/g4.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(
    json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print("OpenAPI exported: contracts/openapi/g4.json")
