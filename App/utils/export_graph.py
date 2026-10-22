"""Export the compiled LangGraph agent graph as an image + Mermaid source.

Run from App/:  python utils/export_graph.py

Writes (into App/):
  - agent_graph.mmd : Mermaid source (always written, no network needed)
  - agent_graph.png : PNG render (best effort; needs network via mermaid.ink,
                      or a local graphviz/pyppeteer backend)

The module imports agent.py, which builds the graph at import time. Building
the real DynamoDBSaver only needs the table NAME env var (no AWS call happens
until the graph runs), so we set placeholder env vars first.
"""

import os
import sys

# Placeholder env so importing agent.py (module-scope graph build) succeeds
# offline. No AWS/network call is made just to draw the graph.
os.environ.setdefault("CHECKPOINTER_TABLE_NAME", "placeholder-checkpointer")
os.environ.setdefault("USERS_TABLE_NAME", "placeholder-users")
os.environ.setdefault("CHAT_HISTORY_TABLE_NAME", "placeholder-chat")
os.environ.setdefault("MAPPING_TABLE_NAME", "placeholder-mapping")
os.environ.setdefault("ORDER_API_ENDPOINT", "https://example.invalid/order")
os.environ.setdefault("AWS_REGION", "us-east-1")

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(APP_DIR, "HackathonWhatsAppCode"))

import agent  # noqa: E402

MMD_PATH = os.path.join(APP_DIR, "agent_graph.mmd")
PNG_PATH = os.path.join(APP_DIR, "agent_graph.png")


def main():
    graph = agent.compiled_graph.get_graph()

    # 1. Mermaid source — always works, no dependencies.
    mermaid = graph.draw_mermaid()
    with open(MMD_PATH, "w", encoding="utf-8") as fh:
        fh.write(mermaid)
    print(f"wrote {MMD_PATH}")

    # 2. PNG — best effort (mermaid.ink needs network; local backends optional).
    try:
        png_bytes = graph.draw_mermaid_png()
        with open(PNG_PATH, "wb") as fh:
            fh.write(png_bytes)
        print(f"wrote {PNG_PATH}")
    except Exception as exc:  # noqa: BLE001
        print(f"PNG render skipped ({exc.__class__.__name__}: {exc}).")
        print("Mermaid source is in agent_graph.mmd — paste it into "
              "https://mermaid.live to export a PNG, or install a local "
              "renderer.")


if __name__ == "__main__":
    main()
