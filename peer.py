import socket
import threading
import json
import time
import hashlib
import os
import random
import requests
import queue
from concurrent.futures import ThreadPoolExecutor
import sys

class TrackerCommunication:
    def __init__(self, tracker_host="localhost", tracker_port=8000):
        self.tracker_host = tracker_host
        self.tracker_port = tracker_port

    def send_to_tracker(self, data):
        max_retries = 3
        backoff_factor = 1
        for attempt in range(max_retries):
            try:
                print(f"[TRACKER DEBUG] Sending request: {data}\n")
                response = requests.post(
                    f"http://{self.tracker_host}:{self.tracker_port}",
                    json=data,
                    headers={'Content-Type': 'application/json'},
                    timeout=(3, 5))
                response.raise_for_status()
                resp_json = response.json()
                print(f"[TRACKER DEBUG] Response: {resp_json}\n")
                return resp_json
            except requests.exceptions.RequestException as e:
                error_type = type(e).__name__
                print(f"[TRACKER] Attempt {attempt+1} failed ({error_type}): {str(e)}\n")
                if attempt < max_retries - 1:
                    wait_time = backoff_factor * (attempt + 1)
                    print(f"[TRACKER] Retrying in {wait_time} seconds...\n")
                    time.sleep(wait_time)
        print("[TRACKER] All attempts failed\n")
        return None

    def get_metadata(self, file_hash):
        return self.send_to_tracker({"action": "get_metadata", "file_hash": file_hash})

    def get_peers(self, file_hash):
        resp = self.send_to_tracker({"action": "get_peers", "file_hash": file_hash})
        if not resp or "peers" not in resp:
            print("[PEER DEBUG] No peers returned from tracker\n")
            return []
        peers = resp["peers"]
        for p in peers:
            p["latency"] = random.uniform(0.1, 0.5) if "latency" not in p else p["latency"]
        print(f"[PEER DEBUG] Peers received: {peers}\n")
        return sorted(peers, key=lambda x: x["latency"])

    def get_file_hash_by_name(self, file_name):
        resp = self.send_to_tracker({"action": "get_file_hash", "file_name": file_name})
        return resp.get("file_hash") if resp else None

    def register_peer(self, file_hash, peer_id, port, ip, pieces):
        resp = self.send_to_tracker({
            "action": "share",
            "file_hash": file_hash,
            "file_name": "downloaded_torrent",
            "pieces": pieces,
            "peer_id": peer_id,
            "port": port,
            "ip": ip
        })
        return resp and resp.get("status") == "success"

    def update_status(self, file_hash, peer_id, status):
        resp = self.send_to_tracker({
            "action": "update_status",
            "file_hash": file_hash,
            "peer_id": peer_id,
            "status": status
        })
        return resp and resp.get("status") == "success"

    def discover_files(self):
        return self.send_to_tracker({"action": "discover"})

class FileManager:
    def __init__(self, repository, piece_size=512 * 1024):
        self.repository = repository
        self.piece_size = piece_size
        os.makedirs(self.repository, exist_ok=True)

    def calculate_file_hash(self, file_path):
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def split_file(self, file_path, file_hash):
        pieces = []
        try:
            with open(file_path, "rb") as f:
                i = 0
                while True:
                    piece_data = f.read(self.piece_size)
                    if not piece_data:
                        break
                    piece_hash = hashlib.sha1(piece_data).hexdigest()
                    piece_path = os.path.join(self.repository, f"{file_hash}_piece_{i}")
                    with open(piece_path, "wb") as p:
                        p.write(piece_data)
                    pieces.append({"index": i, "hash": piece_hash, "size": len(piece_data)})
                    i += 1
            if not pieces:
                raise ValueError("File is empty or unreadable")
            
            metadata = {
                "file_name": os.path.basename(file_path),
                "file_size": os.path.getsize(file_path),
                "piece_size": self.piece_size,
                "pieces": pieces,
                "file_hash": file_hash,
                "files": [{"path": os.path.basename(file_path), "size": os.path.getsize(file_path), "pieces": pieces}]
            }
            with open(os.path.join(self.repository, f"{file_hash}_metadata.json"), "w") as f:
                json.dump(metadata, f)
            return pieces
        except Exception as e:
            print(f"[-] Error splitting file: {str(e)}\n")
            return []

    def save_metadata(self, file_hash, metadata):
        metadata_path = os.path.join(self.repository, f"{file_hash}_metadata.json")
        with open(metadata_path, "w") as f:
            json.dump(metadata, f)
        print(f"[FILE DEBUG] Saved metadata to {metadata_path}\n")

    def get_existing_pieces(self, file_hash):
        pieces = set()
        for f in os.listdir(self.repository):
            if f.startswith(f"{file_hash}_piece_"):
                try:
                    piece_idx = int(f.split('_')[-1])
                    pieces.add(piece_idx)
                except ValueError:
                    continue
        print(f"[FILE DEBUG] Existing pieces for {file_hash}: {pieces}\n")
        return pieces

    def reconstruct_file(self, file_hash):
        metadata_path = os.path.join(self.repository, f"{file_hash}_metadata.json")
        print(f"[FILE DEBUG] Checking metadata at: {metadata_path}\n")
        if not os.path.exists(metadata_path):
            print("[-] Metadata not found\n")
            return False

        with open(metadata_path, "r") as f:
            metadata = json.load(f)
        
        if "files" in metadata:
            for file_info in metadata["files"]:
                output_path = os.path.join(self.repository, file_info["path"])
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                temp_path = output_path + ".temp"
                try:
                    with open(temp_path, "wb") as out_file:
                        for piece in file_info["pieces"]:
                            piece_path = os.path.join(self.repository, f"{file_hash}_piece_{piece['index']}")
                            if not os.path.exists(piece_path):
                                print(f"[-] Missing piece {piece['index']} for {file_info['path']}\n")
                                os.remove(temp_path)
                                return False
                            with open(piece_path, "rb") as piece_file:
                                out_file.write(piece_file.read())
                    os.replace(temp_path, output_path)
                    print(f"[+] Reconstructed file: {output_path}\n")
                except Exception as e:
                    print(f"[-] Error reconstructing {file_info['path']}: {str(e)}\n")
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    return False
        else:
            output_path = os.path.join(self.repository, metadata["file_name"])
            temp_path = output_path + ".temp"
            try:
                with open(temp_path, "wb") as out_file:
                    for piece in metadata["pieces"]:
                        piece_path = os.path.join(self.repository, f"{file_hash}_piece_{piece['index']}")
                        if not os.path.exists(piece_path):
                            print(f"[-] Missing piece {piece['index']}\n")
                            os.remove(temp_path)
                            return False
                        with open(piece_path, "rb") as piece_file:
                            out_file.write(piece_file.read())
                if self.calculate_file_hash(temp_path) == file_hash:
                    os.replace(temp_path, output_path)
                    print(f"[+] File reconstructed: {output_path}\n")
                else:
                    print("[-] File integrity check failed\n")
                    os.remove(temp_path)
                    return False
            except Exception as e:
                print(f"[-] Error reconstructing file: {str(e)}\n")
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                return False
        
        self.cleanup_pieces(file_hash)
        return True

    def cleanup_pieces(self, file_hash):
        for f in os.listdir(self.repository):
            if f.startswith(f"{file_hash}_piece_") or f == f"{file_hash}_metadata.json":
                try:
                    os.remove(os.path.join(self.repository, f))
                except:
                    pass

    def save_state(self, active_downloads, shared_files, requested_pieces):
        state = {
            "active_downloads": active_downloads,
            "shared_files": shared_files,
            "requested_pieces": {k: list(v) for k, v in requested_pieces.items()}
        }
        with open(os.path.join(self.repository, "download_state.json"), "w") as f:
            json.dump(state, f)

    def load_state(self):
        state_path = os.path.join(self.repository, "download_state.json")
        if os.path.exists(state_path):
            with open(state_path, "r") as f:
                state = json.load(f)
                state["requested_pieces"] = {k: set(v) for k, v in state["requested_pieces"].items()}
                return state
        return {"active_downloads": {}, "shared_files": {}, "requested_pieces": {}}

class PeerCommunication:
    def __init__(self, peer_port, peer_id, file_manager):
        self.peer_port = peer_port
        self.peer_id = peer_id
        self.file_manager = file_manager
        self.is_running = True
        self.download_queue = queue.Queue()
        self.peer_threads = []
        self.bitfields = {}  # {file_hash: local_bitfield}
        self.remote_bitfields = {}  # {peer_id: {file_hash: bitfield}}
        self.requested_pieces = {}
        self.upload_stats = {}
        self.download_stats = {}

    def run_server(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('0.0.0.0', self.peer_port))
            s.listen(10)
            print(f"[PEER SERVER] Listening on 0.0.0.0:{self.peer_port}\n")
            while self.is_running:
                try:
                    conn, addr = s.accept()
                    threading.Thread(
                        target=self.handle_peer_connection,
                        args=(conn, addr),
                        daemon=True
                    ).start()
                except Exception as e:
                    if self.is_running:
                        print(f"[PEER SERVER] Error: {str(e)}\n")

    def handle_peer_connection(self, conn, addr):
        peer = f"{addr[0]}:{addr[1]}"
        try:
            data = conn.recv(1024).decode().strip()
            print(f"[PEER DEBUG] Received from {peer}: {data}\n")
            if data.startswith("PING"):
                parts = data.split(" ", 2)
                if len(parts) > 2:
                    file_hash = parts[1]
                    try:
                        remote_bitfield = json.loads(parts[2])
                        if peer not in self.remote_bitfields:
                            self.remote_bitfields[peer] = {}
                        self.remote_bitfields[peer][file_hash] = remote_bitfield
                        print(f"[PEER DEBUG] Updated bitfield from {peer} for {file_hash}: {remote_bitfield}\n")
                    except json.JSONDecodeError as e:
                        print(f"[PEER DEBUG] Invalid bitfield from {peer}: {str(e)}\n")
                
                local_bitfield = self.bitfields.get(file_hash, [])
                response = f"PONG {file_hash} {json.dumps(local_bitfield)}\n"
                conn.sendall(response.encode())
                print(f"[PEER DEBUG] Sent to {peer}: {response.strip()}\n")
            elif data.startswith("REQUEST"):
                _, file_hash, piece_idx = data.split()
                self.send_piece(conn, file_hash, int(piece_idx), peer)
        except Exception as e:
            print(f"[PEER SERVER] Error with {peer}: {str(e)}\n")
        finally:
            conn.close()

    def send_piece(self, conn, file_hash, piece_idx, peer):
        piece_path = os.path.join(self.file_manager.repository, f"{file_hash}_piece_{piece_idx}")
        if os.path.exists(piece_path):
            with open(piece_path, "rb") as f:
                piece_data = f.read()
                conn.sendall(piece_data)
            bytes_sent = len(piece_data)
            print(f"[UPLOAD] Sent piece {piece_idx} ({bytes_sent} bytes) to {peer}\n")
            self.upload_stats[peer] = self.upload_stats.get(peer, 0) + bytes_sent
        else:
            conn.sendall(b"PIECE_NOT_FOUND")
            print(f"[UPLOAD] Piece {piece_idx} not found for {peer}\n")

    def download_worker(self):
        while self.is_running:
            try:
                task = self.download_queue.get(timeout=1)
                if task:
                    file_hash, piece_idx, peer_ip, peer_port = task
                    self.download_piece(file_hash, piece_idx, peer_ip, peer_port)
                self.download_queue.task_done()
            except queue.Empty:
                continue

    def download_piece(self, file_hash, piece_idx, peer_ip, peer_port):
        peer = f"{peer_ip}:{peer_port}"
        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(f"[PEER DEBUG] Connecting to {peer} for piece {piece_idx}\n")
                with socket.create_connection((peer_ip, peer_port), timeout=10) as s:
                    s.settimeout(30)
                    request = f"REQUEST {file_hash} {piece_idx}\n"
                    print(f"[PEER DEBUG] Sending request to {peer}: {request.strip()}\n")
                    s.sendall(request.encode())
                    piece_data = b""
                    while True:
                        chunk = s.recv(16384)
                        if not chunk:
                            break
                        piece_data += chunk
                    
                    if piece_data == b"PIECE_NOT_FOUND":
                        print(f"[DOWNLOAD] Piece {piece_idx} not found on {peer}\n")
                        return False
                    
                    piece_path = os.path.join(self.file_manager.repository, f"{file_hash}_piece_{piece_idx}")
                    with open(piece_path, "wb") as f:
                        f.write(piece_data)
                    bytes_received = len(piece_data)
                    print(f"[DOWNLOAD] Downloaded piece {piece_idx} ({bytes_received} bytes) from {peer}\n")
                    
                    if file_hash in self.bitfields:
                        self.bitfields[file_hash][piece_idx] = 1
                    self.download_stats[peer] = self.download_stats.get(peer, 0) + bytes_received
                    if file_hash in self.requested_pieces:
                        self.requested_pieces[file_hash].discard(piece_idx)
                    return True
            except Exception as e:
                print(f"[DOWNLOAD] Error for piece {piece_idx} on attempt {attempt+1} from {peer}: {str(e)}\n")
                time.sleep(1)
        print(f"[DOWNLOAD] Failed to download piece {piece_idx} from {peer} after {max_retries} attempts\n")
        if file_hash in self.requested_pieces:
            self.requested_pieces[file_hash].discard(piece_idx)
        return False

    def check_peer_connection(self, ip, port, file_hash):
        peer = f"{ip}:{port}"
        try:
            print(f"[PEER DEBUG] Checking connection to {peer}\n")
            with socket.create_connection((ip, port), timeout=5) as s:
                local_bitfield = self.bitfields.get(file_hash, [])
                message = f"PING {file_hash} {json.dumps(local_bitfield)}\n"
                s.sendall(message.encode())
                print(f"[PEER DEBUG] Sent to {peer}: {message.strip()}\n")
                response = s.recv(1024).decode().strip()
                print(f"[PEER DEBUG] Received response from {peer}: {response}\n")
                
                if response.startswith("PONG"):
                    parts = response.split(" ", 2)
                    if len(parts) > 2:
                        try:
                            remote_bitfield = json.loads(parts[2])
                            if peer not in self.remote_bitfields:
                                self.remote_bitfields[peer] = {}
                            self.remote_bitfields[peer][file_hash] = remote_bitfield
                            print(f"[PEER DEBUG] Updated bitfield from {peer} for {file_hash}: {remote_bitfield}\n")
                        except json.JSONDecodeError as e:
                            print(f"[PEER DEBUG] Invalid bitfield from {peer}: {str(e)}\n")
                    return True
                return False
        except Exception as e:
            print(f"[CONNECTION] Failed to connect to {peer}: {str(e)}\n")
            return False

class UserInterface:
    def __init__(self, enhanced_peer):
        self.peer = enhanced_peer

    def command_interface(self):
        print("Commands: discover | share <file> | download <hash> | list | status | exit\n")
        while self.peer.is_running:
            try:
                cmd = input("Peer> ").strip().split()
                if not cmd:
                    continue
                if cmd[0] == "discover":
                    self.peer.handle_discover()
                elif cmd[0] == "share" and len(cmd) > 1:
                    threading.Thread(target=self.peer.share_file, args=(cmd[1],), daemon=True).start()
                elif cmd[0] == "download" and len(cmd) > 1:
                    resume = len(cmd) > 2 and cmd[2] == "--resume"
                    threading.Thread(
                        target=self.peer.download_file,
                        args=(cmd[1], resume),
                        daemon=True
                    ).start()
                elif cmd[0] == "list":
                    self.peer.list_shared_files()
                elif cmd[0] == "status":
                    self.peer.check_download_status()
                elif cmd[0] == "exit":
                    self.peer.graceful_exit()
                    break
                else:
                    print("Unknown command. Available: discover | share <file> | download <hash> | list | status | exit\n")
            except Exception as e:
                print(f"Command error: {str(e)}\n")

class EnhancedPeer:
    instance = None

    def __init__(self, tracker_host="localhost", tracker_port=8000, peer_port=8001):
        self.peer_id = hashlib.sha256(str(time.time()).encode()).hexdigest()[:20]
        self.is_running = True
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=10)
        self.repository = f"./repository_{peer_port}"
        
        self.tracker = TrackerCommunication(tracker_host, tracker_port)
        self.file_manager = FileManager(self.repository)
        self.peer_comm = PeerCommunication(peer_port, self.peer_id, self.file_manager)
        self.ui = UserInterface(self)
        
        state = self.file_manager.load_state()
        self.active_downloads = state["active_downloads"]
        self.shared_files = state["shared_files"]
        self.peer_comm.requested_pieces = state["requested_pieces"]
        
        EnhancedPeer.instance = self
        
        print(f"Peer ID: {self.peer_id}\n")
        print(f"Using repository: {self.repository}\n")

    def start(self):
        threading.Thread(target=self.peer_comm.run_server, daemon=True).start()
        for _ in range(5):
            t = threading.Thread(target=self.peer_comm.download_worker, daemon=True)
            t.start()
            self.peer_comm.peer_threads.append(t)
        print(f"\nPeer server running on port {self.peer_comm.peer_port}\n")
        self.ui.command_interface()

    def share_file(self, file_path):
        full_path = os.path.abspath(file_path)
        if not os.path.isfile(full_path):
            print(f"[-] File not found: {full_path}\n")
            return False
        
        file_hash = self.file_manager.calculate_file_hash(full_path)
        pieces = self.file_manager.split_file(full_path, file_hash)
        if not pieces:
            print(f"[-] Failed to split file or file is empty: {full_path}\n")
            return False
        
        response = self.tracker.send_to_tracker({
            "action": "share",
            "file_hash": file_hash,
            "file_name": os.path.basename(full_path),
            "pieces": pieces,
            "peer_id": self.peer_id,
            "port": self.peer_comm.peer_port,
            "ip": self.get_local_ip()
        })
        
        if response and response.get("status") == "success":
            print(f"[+] Shared successfully! File hash: {file_hash}\n")
            with self.lock:
                self.shared_files[file_hash] = {
                    "file_name": os.path.basename(full_path),
                    "pieces": pieces,
                    "size": os.path.getsize(full_path)
                }
                self.peer_comm.bitfields[file_hash] = [1] * len(pieces)
            return True
        print(f"[-] Failed to share file: {response.get('error', 'Unknown error')}\n")
        return False

    def download_file(self, identifier, resume=False):
        file_hash = identifier if len(identifier) > 8 else self.tracker.get_file_hash_by_name(identifier)
        if not file_hash:
            print(f"[-] File not found: {identifier}\n")
            return False
        
        metadata_resp = self.tracker.get_metadata(file_hash)
        if not metadata_resp or "metadata" not in metadata_resp:
            print(f"[-] Failed to get metadata: {metadata_resp.get('error', 'Unknown error')}\n")
            return False
        metadata = metadata_resp["metadata"]
        print(f"[DOWNLOAD DEBUG] Metadata: {metadata}\n")
        
        self.file_manager.save_metadata(file_hash, metadata)
        
        with self.lock:
            if file_hash not in self.active_downloads:
                self.active_downloads[file_hash] = {
                    "file_name": metadata["file_name"],
                    "total_pieces": len(metadata["pieces"]),
                    "downloaded": set(),
                    "status": "downloading"
                }
                if file_hash not in self.peer_comm.bitfields:
                    self.peer_comm.bitfields[file_hash] = [0] * len(metadata["pieces"])
        
        if resume:
            existing_pieces = self.file_manager.get_existing_pieces(file_hash)
            with self.lock:
                self.active_downloads[file_hash]["downloaded"].update(existing_pieces)
                for idx in existing_pieces:
                    self.peer_comm.bitfields[file_hash][idx] = 1
            print(f"[+] Resuming, found {len(existing_pieces)} pieces\n")
        
        peers = self.tracker.get_peers(file_hash)
        if not peers:
            print("[-] No peers available from tracker\n")
            return False
        
        self.tracker.register_peer(file_hash, self.peer_id, self.peer_comm.peer_port, self.get_local_ip(), metadata["pieces"])
        
        needed_pieces = [p["index"] for p in metadata["pieces"] if p["index"] not in self.active_downloads[file_hash]["downloaded"]]
        if not needed_pieces:
            print("[+] All pieces already downloaded\n")
            success = self.file_manager.reconstruct_file(file_hash)
            if success:
                with self.lock:
                    self.shared_files[file_hash] = {
                        "file_name": metadata["file_name"],
                        "pieces": metadata["pieces"],
                        "size": sum(p["size"] for p in metadata["pieces"])
                    }
                    print(f"[+] Download completed, now seeding {file_hash}\n")
            return success
        
        print(f"[+] Downloading {len(needed_pieces)} pieces from {len(peers)} peers\n")
        success = self.parallel_download(file_hash, needed_pieces, peers)
        
        if success:
            print("[+] Reconstructing file...\n")
            reconstruct_success = self.file_manager.reconstruct_file(file_hash)
            if reconstruct_success:
                with self.lock:
                    self.shared_files[file_hash] = {
                        "file_name": metadata["file_name"],
                        "pieces": metadata["pieces"],
                        "size": sum(p["size"] for p in metadata["pieces"])
                    }
                    print(f"[+] Download completed, now seeding {file_hash}\n")
            return reconstruct_success
        print("[-] Download failed\n")
        return False

    def parallel_download(self, file_hash, needed_pieces, peers):
        print(f"[DOWNLOAD DEBUG] Needed pieces: {needed_pieces}\n")
        print(f"[DOWNLOAD DEBUG] Initial peers: {peers}\n")
        
        if file_hash not in self.peer_comm.requested_pieces:
            self.peer_comm.requested_pieces[file_hash] = set()
        
        remaining_pieces = needed_pieces[:]
        current_peers = peers
        
        while remaining_pieces and self.is_running:
            # Kiểm tra kết nối và cập nhật bitfield cho tất cả peer
            active_peers = []
            for peer in current_peers:
                if self.peer_comm.check_peer_connection(peer["ip"], peer["port"], file_hash):
                    active_peers.append(peer)
            
            if not active_peers:
                print("[DOWNLOAD] No active peers available\n")
                return False
            
            pieces_to_download = [
                idx for idx in remaining_pieces 
                if idx not in self.peer_comm.requested_pieces[file_hash]
            ][:5]
            
            if not pieces_to_download:
                break
            
            for piece_idx in pieces_to_download:
                peer = self.select_optimal_peer(piece_idx, active_peers, file_hash)
                if not peer:
                    print(f"[DOWNLOAD] No peer has piece {piece_idx}\n")
                    continue
                print(f"[DOWNLOAD DEBUG] Selected peer for piece {piece_idx}: {peer['ip']}:{peer['port']}\n")
                self.peer_comm.requested_pieces[file_hash].add(piece_idx)
                self.peer_comm.download_queue.put((file_hash, piece_idx, peer["ip"], peer["port"]))
            
            self.peer_comm.download_queue.join()
            
            with self.lock:
                for piece_idx in pieces_to_download:
                    piece_path = os.path.join(self.file_manager.repository, f"{file_hash}_piece_{piece_idx}")
                    if os.path.exists(piece_path):
                        self.active_downloads[file_hash]["downloaded"].add(piece_idx)
                        remaining_pieces.remove(piece_idx)
            
            current_peers = self.tracker.get_peers(file_hash)
            print(f"[DOWNLOAD DEBUG] Updated peers: {current_peers}\n")
        
        return self.check_complete(file_hash)

    def select_optimal_peer(self, piece_idx, peers, file_hash):
        piece_counts = {}
        for peer in peers:
            peer_str = f"{peer['ip']}:{peer['port']}"
            if (peer_str in self.peer_comm.remote_bitfields and 
                file_hash in self.peer_comm.remote_bitfields[peer_str] and 
                len(self.peer_comm.remote_bitfields[peer_str][file_hash]) > piece_idx and 
                self.peer_comm.remote_bitfields[peer_str][file_hash][piece_idx] == 1):
                piece_counts[peer["peer_id"]] = piece_counts.get(peer["peer_id"], 0) + 1
        
        candidates = [
            p for p in peers
            if p["peer_id"] != self.peer_id and p["peer_id"] in piece_counts
        ]
        if not candidates:
            print(f"[PEER DEBUG] No candidates for piece {piece_idx}\n")
            return None
        
        min_latency = min(p["latency"] for p in candidates)
        total_weight = 0
        weights = []
        for peer in candidates:
            weight = int(min_latency / peer["latency"] * 10)
            weights.append(weight)
            total_weight += weight
        
        if total_weight > 0:
            selected_idx = random.choices(range(len(candidates)), weights=weights, k=1)[0]
            selected = candidates[selected_idx]
            print(f"[PEER DEBUG] Selected peer for piece {piece_idx}: {selected}, weight: {weights[selected_idx]}\n")
            return selected
        else:
            selected = random.choice(candidates)
            print(f"[PEER DEBUG] Randomly selected peer for piece {piece_idx}: {selected}\n")
            return selected

    def check_complete(self, file_hash):
        with self.lock:
            if file_hash not in self.active_downloads:
                return False
            complete = len(self.active_downloads[file_hash]["downloaded"]) == self.active_downloads[file_hash]["total_pieces"]
            print(f"[DOWNLOAD DEBUG] Download complete check: {len(self.active_downloads[file_hash]['downloaded'])}/{self.active_downloads[file_hash]['total_pieces']}\n")
            return complete

    def handle_discover(self):
        response = self.tracker.discover_files()
        if response and "files" in response:
            print("\nAvailable files:\n")
            for file in response["files"]:
                print(f"- {file['file_name']} (Hash: {file['hash'][:8]}...) [Peers: {file.get('active_peers', 0)}]\n")
        else:
            print("[-] Failed to discover files\n")

    def list_shared_files(self):
        print("\n=== Local shared files ===\n")
        if not self.shared_files:
            print("No local files shared\n")
        else:
            for file_hash, info in self.shared_files.items():
                print(f"- {info['file_name']} (Hash: {file_hash[:8]}...)\n")
        
        response = self.tracker.discover_files()
        if response and "files" in response:
            print("\n=== Tracker files ===\n")
            for file in response["files"]:
                print(f"- {file['file_name']} (Hash: {file['hash'][:8]}...) [Peers: {file.get('active_peers', 0)}]\n")

    def check_download_status(self):
        if not self.active_downloads:
            print("No active downloads\n")
            return
        print("\nDownload status:\n")
        for file_hash, status in self.active_downloads.items():
            print(f"- {file_hash[:8]}...: {len(status['downloaded'])}/{status['total_pieces']} pieces\n")

    def graceful_exit(self):
        print("\n[+] Shutting down...\n")
        self.is_running = False
        self.peer_comm.is_running = False
        for _ in range(len(self.peer_comm.peer_threads)):
            self.peer_comm.download_queue.put(None)
        
        with self.lock:
            for file_hash in self.shared_files:
                self.tracker.update_status(file_hash, self.peer_id, "stop")
                print(f"[+] Sent stop status to tracker for shared file {file_hash}\n")
            for file_hash in self.active_downloads:
                if file_hash not in self.shared_files:
                    self.tracker.update_status(file_hash, self.peer_id, "stop")
                    print(f"[+] Sent stop status to tracker for downloaded file {file_hash}\n")
        
        self.file_manager.save_state(self.active_downloads, self.shared_files, self.peer_comm.requested_pieces)
        self.executor.shutdown()
        print("[+] Peer stopped\n")

    def get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"

if __name__ == "__main__":
    try:
        peer_port = 8001
        if len(sys.argv) > 1:
            try:
                peer_port = int(sys.argv[1])
                if not (1024 <= peer_port <= 65535):
                    raise ValueError("Port must be between 1024 and 65535")
            except ValueError as e:
                print(f"Invalid port: {str(e)}. Using default port 8001.\n")
                peer_port = 8001
        
        peer = EnhancedPeer(peer_port=peer_port)
        peer.start()
    except KeyboardInterrupt:
        print("\n[!] Peer stopped by user\n")
    except Exception as e:
        print(f"[!] Fatal error: {str(e)}\n")