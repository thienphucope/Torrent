import http.server
import socketserver
import json
import threading
import time
from http.server import BaseHTTPRequestHandler
import hashlib
import socket

class Tracker:
    def __init__(self, port=8000, udp_port=8001):
        # Lấy IP của máy tự động
        self.host = self.get_local_ip()
        self.port = port
        self.torrents = {}  # Keyed by torrent_hash
        self.torrent_name_to_hash = {}  # Maps torrent name to torrent_hash
        self.lock = threading.Lock()
        print(f"Tracker initialized at {self.host}:{self.port}")
        self.udp_port = udp_port
        threading.Thread(target=self.listen_udp_broadcast, daemon=True).start()  # Start UDP listener
    
    def listen_udp_broadcast(self):
        """Listen for UDP broadcast requests from peers."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(('', self.udp_port))
        print(f"[UDP] Listening for broadcast on UDP port {self.udp_port}")

        while True:
            try:
                message, addr = sock.recvfrom(1024)
                decoded = message.decode()
                print(f"[UDP] Received from {addr}: {decoded}")

                if decoded.strip().lower() == "where_are_you":
                    response = json.dumps({
                        "ip": self.host,
                        "port": self.port
                    }).encode()
                    sock.sendto(response, addr)
                    print(f"[UDP] Sent tracker info to {addr}")
            except Exception as e:
                print(f"[UDP Error] {e}")

    def get_local_ip(self):
        """Lấy địa chỉ IP của máy hiện tại."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Kết nối đến một địa chỉ bất kỳ để lấy IP cục bộ
            s.connect(('8.8.8.8', 1))
            ip = s.getsockname()[0]
        except Exception:
            ip = '127.0.0.1'  # Nếu không lấy được IP, dùng localhost làm mặc định
        finally:
            s.close()
        return ip

    def calculate_torrent_hash(self, metadata):
        """Calculate torrent hash from metadata."""
        metadata_str = json.dumps(metadata, sort_keys=True)
        return hashlib.sha256(metadata_str.encode()).hexdigest()

    def handle_share(self, data):
        with self.lock:
            required_fields = ['metadata', 'peer_id', 'port']
            for field in required_fields:
                if field not in data:
                    return {"error": f"Missing required field: {field}"}
            
            metadata = data['metadata']
            peer_id = data['peer_id']
            pieces = metadata.get('pieces', [])
            
            if not isinstance(pieces, list) or not pieces:
                return {"error": "Invalid or empty 'pieces' data"}
            
            try:
                piece_sizes = [p['size'] for p in pieces]
            except (KeyError, TypeError):
                return {"error": "Invalid pieces format: each piece must have 'size'"}

            torrent_hash = self.calculate_torrent_hash(metadata)
            
            if torrent_hash not in self.torrents:
                self.torrents[torrent_hash] = {
                    "metadata": metadata,
                    "peers": {}
                }
                torrent_name = "_".join(f["file_name"] for f in metadata["files"])
                self.torrent_name_to_hash[torrent_name] = torrent_hash
            
            peer_info = {
                "ip": data.get('ip', '0.0.0.0'),
                "port": data['port'],
                "status": "start",
                "last_seen": time.time()
            }
            
            if peer_id in self.torrents[torrent_hash]["peers"]:
                print(f"[Tracker] Peer {peer_id} already exists for {torrent_hash}, updating info")
                self.torrents[torrent_hash]["peers"][peer_id].update(peer_info)
            else:
                self.torrents[torrent_hash]["peers"][peer_id] = peer_info
                print(f"[Tracker] Added new peer {peer_id} for {torrent_hash}")
            
            return {"status": "success", "torrent_hash": torrent_hash}

    def handle_update_status(self, data):
        with self.lock:
            required_fields = ['torrent_hash', 'peer_id', 'status']
            for field in required_fields:
                if field not in data:
                    return {"error": f"Missing required field: {field}"}
            
            torrent_hash = data['torrent_hash']
            peer_id = data['peer_id']
            status = data['status']
            
            if torrent_hash not in self.torrents or peer_id not in self.torrents[torrent_hash]["peers"]:
                return {"error": "Peer or torrent not found"}
            
            if status == "stop":
                del self.torrents[torrent_hash]["peers"][peer_id]
                print(f"[Tracker] Removed peer {peer_id} from {torrent_hash} due to stop status")
                if not self.torrents[torrent_hash]["peers"]:
                    torrent_name = "_".join(f["file_name"] for f in self.torrents[torrent_hash]["metadata"]["files"])
                    del self.torrents[torrent_hash]
                    if torrent_name in self.torrent_name_to_hash:
                        del self.torrent_name_to_hash[torrent_name]
                    print(f"[Tracker] Removed torrent {torrent_hash} as no peers remain")
            else:
                self.torrents[torrent_hash]["peers"][peer_id]["status"] = status
                self.torrents[torrent_hash]["peers"][peer_id]["last_seen"] = time.time()
                print(f"[Tracker] Updated status for peer {peer_id} in {torrent_hash} to {status}")
            
            return {"status": "success"}

    def handle_clear_peer(self, peer_id):
        with self.lock:
            removed = False
            for torrent_hash in list(self.torrents.keys()):
                if peer_id in self.torrents[torrent_hash]["peers"]:
                    del self.torrents[torrent_hash]["peers"][peer_id]
                    print(f"[Tracker] Cleared peer {peer_id} from {torrent_hash}")
                    removed = True
                    if not self.torrents[torrent_hash]["peers"]:
                        torrent_name = "_".join(f["file_name"] for f in self.torrents[torrent_hash]["metadata"]["files"])
                        del self.torrents[torrent_hash]
                        if torrent_name in self.torrent_name_to_hash:
                            del self.torrent_name_to_hash[torrent_name]
                        print(f"[Tracker] Removed torrent {torrent_hash} as no peers remain")
            if removed:
                return {"status": "success"}
            return {"status": "no_change"}

    def handle_get_torrent_hash(self, torrent_name):
        with self.lock:
            torrent_hash = self.torrent_name_to_hash.get(torrent_name)
            if torrent_hash:
                return {"torrent_hash": torrent_hash}
            return {"error": "Torrent name not found"}

    def handle_get_metadata(self, data):
        torrent_hash = data.get("torrent_hash")
        if not torrent_hash:
            return {"error": "Missing torrent_hash"}
        with self.lock:
            if torrent_hash not in self.torrents:
                return {"error": "Torrent not found"}
            return {"status": "success", "metadata": self.torrents[torrent_hash]["metadata"]}

    def handle_get_peers(self, data):
        torrent_hash = data.get("torrent_hash")
        if not torrent_hash:
            return {"error": "Missing torrent_hash"}
        with self.lock:
            if torrent_hash not in self.torrents:
                return {"peers": []}
            active_peers = [
                {"ip": peer["ip"], "port": peer["port"], "peer_id": peer_id, "status": peer["status"]}
                for peer_id, peer in self.torrents[torrent_hash]["peers"].items()
            ]
            return {"peers": active_peers}

    def handle_discover(self):
        with self.lock:
            files = [{
                "torrent_name": "_".join(f["file_name"] for f in info["metadata"]["files"]),
                "hash": torrent_hash,
                "size": sum(f["file_size"] for f in info["metadata"]["files"]),
                "active_peers": len(info["peers"])
            } for torrent_hash, info in self.torrents.items()]
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
            elif data["action"] == "get_torrent_hash":  # Renamed to get_torrent_hash
                response = self.server.tracker.handle_get_torrent_hash(data.get("torrent_name", ""))
            elif data["action"] == "clear_peer":
                response = self.server.tracker.handle_clear_peer(data.get("peer_id", ""))
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