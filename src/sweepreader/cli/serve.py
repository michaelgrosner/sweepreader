import json
import logging
from http.server import SimpleHTTPRequestHandler
import socketserver

from sweepreader.config import load_config
from sweepreader.store.feedback import FeedbackStore

logger = logging.getLogger(__name__)


class FeedbackHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory="docs", **kwargs)

    def do_POST(self):
        if self.path == "/api/feedback":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)
                data = json.loads(body.decode("utf-8"))

                item_id = data.get("item_id")
                signal = data.get("signal")

                if not item_id or signal not in ("up", "down"):
                    self.send_error(400, "Invalid feedback parameters")
                    return

                # Load config to get the current config hash
                config = load_config("config.yaml")
                config_hash = config.config_hash()

                # Record feedback
                store = FeedbackStore()
                store.record(item_id, signal, config_hash)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success"}).encode("utf-8"))
                logger.info("Feedback recorded: item=%s signal=%s", item_id, signal)
            except Exception as e:
                logger.exception("Failed to record feedback")
                self.send_error(500, f"Internal error: {e}")
        else:
            self.send_error(404, "Not found")


def cmd_serve(args) -> int:
    port = args.port
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", port), FeedbackHandler) as httpd:
        print(f"SweepReader serving locally at http://localhost:{port}/")
        print("Upvote/downvote feedback will be written to data/feedback/")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping server...")
    return 0
