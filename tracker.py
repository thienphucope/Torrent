import http.server
import socketserver
import json
import threading
import time
from http.server import BaseHTTPRequestHandler

class Tracker:
    def __init__(self, host='localhost', port=8000):
        self.host = host
        self.port = port
        self.torrents = {}
        self.file_name_to_hash = {}
        self.lock = threading.Lock()
        print(f"Tracker initialized at {host}:{port}")

    def handle_share(self, data):
        with self.lock:
            required_fields = ['file_hash', 'file_name', 'pieces', 'peer_id', 'port']
            for field in required_fields:
                if field not in data:
                    return {"error": f"Missing required field: {field}"}
            
            file_hash = data['file_hash']
            peer_id = data['peer_id']
            pieces = data['pieces']
            
            if not isinstance(pieces, list) or not pieces:
                return {"error": "Invalid or empty 'pieces' data"}
            
            try:
                piece_sizes = [p['size'] for p in pieces]
            except (KeyError, TypeError):
                return {"error": "Invalid pieces format: each piece must have 'size'"}

            if file_hash not in self.torrents:
                self.torrents[file_hash] = {
                    "metadata": {
                        "file_name": data['file_name'],
                        "pieces": pieces,
                        "size": sum(piece_sizes)
                    },
                    "peers": {}
                }
                self.file_name_to_hash[data['file_name']] = file_hash
            
            peer_info = {
                "ip": data.get('ip', '0.0.0.0'),
                "port": data['port'],
                "status": "start",
                "last_seen": time.time()
            }
            
            if peer_id in self.torrents[file_hash]["peers"]:
                print(f"[Tracker] Peer {peer_id} already exists for {file_hash}, updating info")
                self.torrents[file_hash]["peers"][peer_id].update(peer_info)
            else:
                self.torrents[file_hash]["peers"][peer_id] = peer_info
                print(f"[Tracker] Added new peer {peer_id} for {file_hash}")
            
            return {"status": "success", "file_hash": file_hash}

    def handle_update_status(self, data):
        with self.lock:
            required_fields = ['file_hash', 'peer_id', 'status']
            for field in required_fields:
                if field not in data:
                    return {"error": f"Missing required field: {field}"}
            
            file_hash = data['file_hash']
            peer_id = data['peer_id']
            status = data['status']
            
            if file_hash not in self.torrents or peer_id not in self.torrents[file_hash]["peers"]:
                return {"error": "Peer or file not found"}
            
            if status == "stop":
                del self.torrents[file_hash]["peers"][peer_id]
                print(f"[Tracker] Removed peer {peer_id} from {file_hash} due to stop status")
                if not self.torrents[file_hash]["peers"]:
                    del self.torrents[file_hash]
                    del self.file_name_to_hash[data['metadata']['file_name']]
                    print(f"[Tracker] Removed torrent {file_hash} as no peers remain")
            else:
                self.torrents[file_hash]["peers"][peer_id]["status"] = status
                self.torrents[file_hash]["peers"][peer_id]["last_seen"] = time.time()
                print(f"[Tracker] Updated status for peer {peer_id} in {file_hash} to {status}")
            
            return {"status": "success"}

    def handle_get_file_hash(self, file_name):
        with self.lock:
            file_hash = self.file_name_to_hash.get(file_name)
            if file_hash:
                return {"file_hash": file_hash}
            return {"error": "File name not found"}

    def handle_get_metadata(self, data):
        file_hash = data.get("file_hash")
        if not file_hash:
            return {"error": "Missing file_hash"}
        with self.lock:
            if file_hash not in self.torrents:
                return {"error": "File not found"}
            return {"status": "success", "metadata": self.torrents[file_hash]["metadata"]}

    def handle_get_peers(self, data):
        file_hash = data.get("file_hash")
        if not file_hash:
            return {"error": "Missing file_hash"}
        with self.lock:
            if file_hash not in self.torrents:
                return {"peers": []}
            active_peers = [
                {"ip": peer["ip"], "port": peer["port"], "peer_id": peer_id, "status": peer["status"]}
                for peer_id, peer in self.torrents[file_hash]["peers"].items()
            ]
            return {"peers": active_peers}

    def handle_discover(self):
        with self.lock:
            files = [{
                "file_name": info["metadata"]["file_name"],
                "hash": file_hash,
                "size": info["metadata"]["size"],
                "active_peers": len(info["peers"])
            } for file_hash, info in self.torrents.items()]
            return {"files": files}

class TrackerHandler(BaseHTTPRequestHandler):
    def _set_headers(self, status_code=200):
        self.send_response(status_code)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

    def do_POST(self):
        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data)
            
            print(f"\n[Tracker] Received request: {data.get('action')} with data: {data}")
            
            if "action" not in data:
                response = {"error": "Missing action"}
            elif data["action"] == "share":
                response = self.server.tracker.handle_share(data)
            elif data["action"] == "update_status":
                response = self.server.tracker.handle_update_status(data)
            elif data["action"] == "get_peers":
                response = self.server.tracker.handle_get_peers(data)
            elif data["action"] == "discover":
                response = self.server.tracker.handle_discover()
            elif data["action"] == "get_metadata":
                response = self.server.tracker.handle_get_metadata(data)
            elif data["action"] == "get_file_hash":
                response = self.server.tracker.handle_get_file_hash(data.get("file_name", ""))
            else:
                response = {"error": "Invalid action"}
            
            self._set_headers(200 if "error" not in response else 400)
            self.wfile.write(json.dumps(response).encode())
            
        except json.JSONDecodeError:
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "Invalid JSON"}).encode())
        except Exception as e:
            print(f"[Tracker Error] {str(e)}")
            self._set_headers(500)
            self.wfile.write(json.dumps({"error": str(e)}).encode())

class ThreadingHTTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

if __name__ == "__main__":
    tracker = Tracker()
    server = ThreadingHTTPServer((tracker.host, tracker.port), TrackerHandler)
    server.tracker = tracker
    print(f"Tracker running on {tracker.host}:{tracker.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nTracker stopped")