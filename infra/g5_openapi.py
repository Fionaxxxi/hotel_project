import json

from infra.manage import ROOT
from services.core.main import create_app


def main():
    (ROOT / "contracts/openapi/g5.json").write_text(
        json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("G5 OpenAPI exported")


if __name__ == "__main__":
    main()
