import argparse
import json
import os
from pathlib import Path
import threading

from .config import Config
from .scraper import scrape_website
from .server import start_server
from .store import encode


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Bellhaven ownership reconciliation")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Start the local review app")
    serve.add_argument("--port", type=int)
    sub.add_parser("scan", help="Generate proposals without writing to the CRM")
    scrape = sub.add_parser("scrape", help="Read the real public website without CRM credentials")
    scrape.add_argument("--url", default="https://analyst-assessment-production.up.railway.app/")
    scrape.add_argument("--output", default="data/website.json")
    sub.add_parser("status", help="Show latest run and decision counts")
    args = parser.parse_args()
    try:
        if args.command == "scrape":
            config = Config(website_url=args.url, expected_min=35)
            result = scrape_website(config)
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps({"facilities": result.facilities, "pages": result.pages}, indent=2))
            print(f"Saved {len(result.facilities)} facilities from {len(result.pages)} pages to {output}")
            return
        config = Config.from_env()
        if args.command != "serve":
            config.port = 0
        elif args.port is not None:
            config.port = args.port
        server, service = start_server(config)
        try:
            if args.command == "scan":
                print(encode(service.scan()))
            elif args.command == "status":
                state = service.state()
                counts = {}
                for p in state["proposals"]:
                    counts[p["state"]] = counts.get(p["state"], 0) + 1
                print(json.dumps({"mode": state["mode"], "decisions": counts, "last_run": state["last_run"]}, indent=2))
            else:
                if config.mode == "demo" and service.state()["last_run"] is None:
                    service.scan()
                print(f"Bellhaven review app: http://127.0.0.1:{server.server_port}", flush=True)
                print(f"Mode: {config.mode}. Database: {config.db_path}", flush=True)
                threading.Event().wait()
        finally:
            server.shutdown()
            server.server_close()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
