import argparse

from .server import serve


def main() -> None:
    ap = argparse.ArgumentParser(prog="apkgen", description="Generate WebView APKs with a local web UI.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    args = ap.parse_args()
    serve(args.host, args.port, not args.no_browser)


if __name__ == "__main__":
    main()
