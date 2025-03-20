import http.server
import socketserver
import json
import threading
from urllib.parse import parse_qs, urlparse

class Tracker:
    def __init__(self, host="localhost", port=8000):
        self.host = host
        self.port = port
        self.torrents = {}  # {torrent_hash: {peer_id: {ip, port}}}
        self.lock = threading.Lock()

    def start_server(self):
        server = ThreadingHTTPServer((self.host, self.port), TrackerHandler)
        server.tracker = self
        print(f"Tracker running on {self.host}:{self.port}")
        server.serve_forever()

class TrackerHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        action = parsed.path[1:] or "get_peers"

        if action == "register":
            self.handle_registration(query)
        elif action == "get_peers":
            self.handle_get_peers(query)
        elif action == "status":
            self.handle_status(query)

    def handle_registration(self, query):
        torrent_hash = query["torrent_hash"][0]
        peer_id = query["peer_id"][0]
        ip = self.client_address[0]
        port = int(query["port"][0])
        
        with self.server.tracker.lock:
            if torrent_hash not in self.server.tracker.torrents:
                self.server.tracker.torrents[torrent_hash] = {}
            self.server.tracker.torrents[torrent_hash][peer_id] = {"ip": ip, "port": port}
        
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "registered"}).encode())

    def handle_get_peers(self, query):
        torrent_hash = query["torrent_hash"][0]
        with self.server.tracker.lock:
            peers = self.server.tracker.torrents.get(torrent_hash, {})
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"peers": list(peers.values())}).encode())

    def handle_status(self, query):
        torrent_hash = query["torrent_hash"][0]
        peer_id = query["peer_id"][0]
        status = query["status"][0]
        
        with self.server.tracker.lock:
            if status == "stopped" and torrent_hash in self.server.tracker.torrents:
                self.server.tracker.torrents[torrent_hash].pop(peer_id, None)
        
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": status}).encode())

class ThreadingHTTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

if __name__ == "__main__":
    tracker = Tracker()
    tracker.start_server()