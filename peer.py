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
import tkinter as tk
from tkinter import scrolledtext, ttk

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

    def get_peers(self, file_hash, my_peer_id):
        resp = self.send_to_tracker({"action": "get_peers", "file_hash": file_hash})
        if not resp or "peers" not in resp:
            print("[PEER DEBUG] No peers returned from tracker\n")
            return []
        peers = [p for p in resp["peers"] if p["peer_id"] != my_peer_id]
        for p in peers:
            p["latency"] = random.uniform(0.1, 0.5) if "latency" not in p else p["latency"]
        print(f"[PEER DEBUG] Peers received (excluding self): {peers}\n")
        return sorted(peers, key=lambda x: x["latency"])

    def get_file_hash_by_name(self, file_name):
        resp = self.send_to_tracker({"action": "get_file_hash", "file_name": file_name})
        return resp.get("file_hash") if resp else None

    def register_peer(self, file_hash, peer_id, port, ip, pieces, file_name):
        resp = self.send_to_tracker({
            "action": "share",
            "file_hash": file_hash,
            "file_name": file_name,
            "pieces": pieces,
            "peer_id": peer_id,
            "port": port,
            "ip": ip
        })
        return resp and resp.get("status") == "success"

    def update_status(self, file_hash, peer_id, status, pieces=None):
        data = {
            "action": "update_status",
            "file_hash": file_hash,
            "peer_id": peer_id,
            "status": status
        }
        if pieces is not None:
            data["pieces"] = pieces
        resp = self.send_to_tracker(data)
        return resp and resp.get("status") == "success"

    def discover_files(self):
        return self.send_to_tracker({"action": "discover"})

    def clear_peer(self, peer_id):
        resp = self.send_to_tracker({"action": "clear_peer", "peer_id": peer_id})
        return resp and resp.get("status") == "success"

class FileManager:
    def __init__(self, repository, piece_size=512 * 1024):
        self.repository = repository
        self.shared_files_dir = os.path.join(repository, "shared_files")
        self.downloads_dir = os.path.join(repository, "downloads")
        self.piece_size = piece_size
        os.makedirs(self.shared_files_dir, exist_ok=True)
        os.makedirs(self.downloads_dir, exist_ok=True)

    def calculate_file_hash(self, file_path):
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def split_file(self, file_path, file_hash):
        pieces = []
        file_name = os.path.basename(file_path)
        subfolder = os.path.join(self.shared_files_dir, file_name)
        os.makedirs(subfolder, exist_ok=True)
        try:
            with open(file_path, "rb") as f:
                i = 0
                while True:
                    piece_data = f.read(self.piece_size)
                    if not piece_data:
                        break
                    piece_hash = hashlib.sha1(piece_data).hexdigest()
                    piece_path = os.path.join(subfolder, f"{file_hash}_piece_{i}")
                    with open(piece_path, "wb") as p:
                        p.write(piece_data)
                    pieces.append({"index": i, "hash": piece_hash, "size": len(piece_data)})
                    i += 1
            if not pieces:
                raise ValueError("File is empty or unreadable")

            metadata = {
                "file_name": file_name,
                "file_size": os.path.getsize(file_path),
                "piece_size": self.piece_size,
                "pieces": pieces,
                "file_hash": file_hash
            }
            with open(os.path.join(subfolder, f"{file_hash}_metadata.json"), "w") as f:
                json.dump(metadata, f)
            return pieces
        except Exception as e:
            print(f"[-] Error splitting file: {str(e)}\n")
            return []

    def save_metadata(self, file_hash, metadata):
        subfolder = os.path.join(self.downloads_dir, metadata["file_name"])
        os.makedirs(subfolder, exist_ok=True)
        metadata_path = os.path.join(subfolder, f"{file_hash}_metadata.json")
        with open(metadata_path, "w") as f:
            json.dump(metadata, f)
        print(f"[FILE DEBUG] Saved metadata to {metadata_path}\n")

    def get_existing_pieces(self, file_hash, file_name, is_shared=False):
        folder = self.shared_files_dir if is_shared else self.downloads_dir
        subfolder = os.path.join(folder, file_name)
        if not os.path.exists(subfolder):
            return set()
        pieces = set()
        for f in os.listdir(subfolder):
            if f.startswith(f"{file_hash}_piece_"):
                try:
                    piece_idx = int(f.split('_')[-1])
                    pieces.add(piece_idx)
                except ValueError:
                    continue
        print(f"[FILE DEBUG] Existing pieces for {file_hash} in {subfolder}: {pieces}\n")
        return pieces

    def reconstruct_file(self, file_hash, file_name):
        subfolder = os.path.join(self.downloads_dir, file_name)
        metadata_path = os.path.join(subfolder, f"{file_hash}_metadata.json")
        print(f"[FILE DEBUG] Checking metadata at: {metadata_path}\n")
        if not os.path.exists(metadata_path):
            print("[-] Metadata not found\n")
            return False

        with open(metadata_path, "r") as f:
            metadata = json.load(f)

        output_path = os.path.join(self.repository, file_name)
        temp_path = output_path + ".temp"
        try:
            with open(temp_path, "wb") as out_file:
                for piece in metadata["pieces"]:
                    piece_path = os.path.join(subfolder, f"{file_hash}_piece_{piece['index']}")
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
        return True

    def cleanup_pieces(self, file_hash, file_name, is_shared=False):
        folder = self.shared_files_dir if is_shared else self.downloads_dir
        subfolder = os.path.join(folder, file_name)
        if os.path.exists(subfolder):
            for f in os.listdir(subfolder):
                if f.startswith(f"{file_hash}_piece_") or f == f"{file_hash}_metadata.json":
                    try:
                        os.remove(os.path.join(subfolder, f))
                    except:
                        pass
            if not os.listdir(subfolder):
                os.rmdir(subfolder)

    def scan_shared_files(self):
        shared = {}
        for subfolder in os.listdir(self.shared_files_dir):
            subfolder_path = os.path.join(self.shared_files_dir, subfolder)
            if not os.path.isdir(subfolder_path):
                continue
            for f in os.listdir(subfolder_path):
                if f.endswith("_metadata.json"):
                    file_hash = f.replace("_metadata.json", "")
                    metadata_path = os.path.join(subfolder_path, f)
                    try:
                        with open(metadata_path, "r") as mf:
                            metadata = json.load(mf)
                        size = metadata.get("file_size", sum(p["size"] for p in metadata["pieces"]))
                        shared[file_hash] = {
                            "file_name": subfolder,
                            "pieces": metadata["pieces"],
                            "size": size
                        }
                    except Exception as e:
                        print(f"[FILE DEBUG] Error loading shared metadata {metadata_path}: {str(e)}\n")
        return shared

    def scan_downloads(self):
        downloads = {}
        for subfolder in os.listdir(self.downloads_dir):
            subfolder_path = os.path.join(self.downloads_dir, subfolder)
            if not os.path.isdir(subfolder_path):
                continue
            for f in os.listdir(subfolder_path):
                if f.endswith("_metadata.json"):
                    file_hash = f.replace("_metadata.json", "")
                    metadata_path = os.path.join(subfolder_path, f)
                    try:
                        with open(metadata_path, "r") as mf:
                            metadata = json.load(mf)
                        downloaded = self.get_existing_pieces(file_hash, subfolder)
                        downloads[file_hash] = {
                            "file_name": subfolder,
                            "total_pieces": len(metadata["pieces"]),
                            "downloaded": downloaded,
                            "status": "paused"
                        }
                    except Exception as e:
                        print(f"[FILE DEBUG] Error loading download metadata {metadata_path}: {str(e)}\n")
        return downloads

class PeerCommunication:
    def __init__(self, peer_port, peer_id, file_manager, log_callback):
        self.peer_port = peer_port
        self.peer_id = peer_id
        self.file_manager = file_manager
        self.is_running = True
        self.download_queues = {}
        self.peer_threads = []
        self.bitfields = {}
        self.remote_bitfields = {}
        self.requested_pieces = {}
        self.upload_stats = {}
        self.download_stats = {}
        self.log_callback = log_callback
        self.current_downloads = {}
        self.upload_queue = queue.Queue()

    def log(self, message):
        print(message, end='')
        if self.log_callback:
            self.log_callback(message)

    def run_server(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('0.0.0.0', self.peer_port))
            s.listen(10)
            self.log(f"[PEER SERVER] Listening on 0.0.0.0:{self.peer_port}\n")
            threading.Thread(target=self.upload_worker, daemon=True).start()
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
                        self.log(f"[PEER SERVER] Error: {str(e)}\n")

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
                self.upload_queue.put((conn, file_hash, int(piece_idx), peer))
        except Exception as e:
            print(f"[PEER SERVER] Error with {peer}: {str(e)}\n")

    def upload_worker(self):
        while self.is_running:
            try:
                conn, file_hash, piece_idx, peer = self.upload_queue.get(timeout=1)
                file_name = EnhancedPeer.instance.active_downloads.get(file_hash, {}).get("file_name",
                            EnhancedPeer.instance.shared_files.get(file_hash, {}).get("file_name", ""))
                if not file_name:
                    self.log(f"[UPLOAD] File name not found for {file_hash} to serve {peer}\n")
                    conn.sendall(b"PIECE_NOT_FOUND")
                    conn.close()
                    self.upload_queue.task_done()
                    continue

                folder = self.file_manager.shared_files_dir if file_hash in EnhancedPeer.instance.shared_files else self.file_manager.downloads_dir
                piece_path = os.path.join(folder, file_name, f"{file_hash}_piece_{piece_idx}")
                print(f"[UPLOAD DEBUG] Looking for piece at: {piece_path}\n")
                if os.path.exists(piece_path):
                    with open(piece_path, "rb") as f:
                        piece_data = f.read()
                    conn.sendall(piece_data)
                    bytes_sent = len(piece_data)
                    self.log(f"[UPLOAD] Sent piece {piece_idx} ({bytes_sent} bytes) to {peer} from {piece_path}\n")
                    self.upload_stats[peer] = self.upload_stats.get(peer, 0) + bytes_sent
                else:
                    self.log(f"[UPLOAD] Piece {piece_idx} not found at {piece_path} for {peer}\n")
                    conn.sendall(b"PIECE_NOT_FOUND")
                conn.close()
                self.upload_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                self.log(f"[UPLOAD] Error serving {peer}: {str(e)}\n")
                conn.close()
                self.upload_queue.task_done()

    def download_worker(self, peer_ip, peer_port):
        peer = f"{peer_ip}:{peer_port}"
        if peer not in self.download_queues:
            self.download_queues[peer] = queue.Queue()
        download_queue = self.download_queues[peer]
        while self.is_running:
            try:
                file_hash, piece_idx = download_queue.get(timeout=1)
                if self.download_piece(file_hash, piece_idx, peer_ip, peer_port):
                    file_name = EnhancedPeer.instance.active_downloads[file_hash]["file_name"]
                    metadata_path = os.path.join(self.file_manager.downloads_dir, file_name, f"{file_hash}_metadata.json")
                    if os.path.exists(metadata_path):
                        with open(metadata_path, "r") as f:
                            metadata = json.load(f)
                        pieces = [{"index": i, "hash": "", "size": 0} for i in range(len(metadata["pieces"])) if self.bitfields[file_hash][i] == 1]
                        EnhancedPeer.instance.tracker.update_status(file_hash, self.peer_id, "downloading", pieces)
                download_queue.task_done()
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
                    if file_hash not in self.current_downloads:
                        self.current_downloads[file_hash] = {}
                    self.current_downloads[file_hash][piece_idx] = peer
                    s.sendall(request.encode())
                    piece_data = b""
                    while True:
                        chunk = s.recv(16384)
                        if not chunk:
                            break
                        piece_data += chunk

                    if piece_data == b"PIECE_NOT_FOUND":
                        self.log(f"[DOWNLOAD] Piece {piece_idx} not found on {peer}\n")
                        if file_hash in self.current_downloads:
                            self.current_downloads[file_hash].pop(piece_idx, None)
                        return False

                    file_name = EnhancedPeer.instance.active_downloads.get(file_hash, {}).get("file_name", "")
                    if not file_name:
                        self.log(f"[DOWNLOAD] File name not found for {file_hash} during download\n")
                        return False

                    piece_path = os.path.join(self.file_manager.downloads_dir, file_name, f"{file_hash}_piece_{piece_idx}")
                    os.makedirs(os.path.dirname(piece_path), exist_ok=True)
                    print(f"[DOWNLOAD DEBUG] Saving piece to: {piece_path}\n")
                    with open(piece_path, "wb") as f:
                        f.write(piece_data)
                    bytes_received = len(piece_data)
                    self.log(f"[DOWNLOAD] Downloaded piece {piece_idx} ({bytes_received} bytes) from {peer} to {piece_path}\n")

                    if file_hash in self.bitfields:
                        self.bitfields[file_hash][piece_idx] = 1
                    self.download_stats[peer] = self.download_stats.get(peer, 0) + bytes_received
                    if file_hash in self.requested_pieces:
                        self.requested_pieces[file_hash].discard(piece_idx)
                    if file_hash in self.current_downloads:
                        self.current_downloads[file_hash].pop(piece_idx, None)
                    return True
            except Exception as e:
                print(f"[DOWNLOAD] Error for piece {piece_idx} on attempt {attempt+1} from {peer}: {str(e)}\n")
                time.sleep(1)
        self.log(f"[DOWNLOAD] Failed to download piece {piece_idx} from {peer} after {max_retries} attempts\n")
        if file_hash in self.requested_pieces:
            self.requested_pieces[file_hash].discard(piece_idx)
        if file_hash in self.current_downloads:
            self.current_downloads[file_hash].pop(piece_idx, None)
        return False

    def check_peer_connection(self, ip, port, file_hash, my_peer_id):
        peer = f"{ip}:{port}"
        peer_id = None
        for p in self.remote_bitfields:
            if p.startswith(f"{ip}:"):
                peer_id = p.split(":", 1)[1]
                break
        if peer_id == my_peer_id:
            print(f"[PEER DEBUG] Skipping connection check to self: {peer}\n")
            return False
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

class EnhancedPeer:
    instance = None

    def __init__(self, tracker_host="localhost", tracker_port=8000, peer_port=8001):
        self.ip = self.get_local_ip()
        self.peer_id = hashlib.sha256(f"{self.ip}:{peer_port}".encode()).hexdigest()[:20]
        self.is_running = True
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=10)
        self.repository = f"./repository_{peer_port}"
        self.tracker = TrackerCommunication(tracker_host, tracker_port)
        self.file_manager = FileManager(self.repository)
        self.root = tk.Tk()
        self.peer_comm = PeerCommunication(peer_port, self.peer_id, self.file_manager, self.log_to_ui)
        self.shared_files_frame = None
        self.discover_frame = None
        self.shared_files_widgets = {}
        self.discover_widgets = {}
        self.status_widgets = {}
        self.log_queue = queue.Queue()

        # Load from disk and sync with tracker
        self.shared_files = self.file_manager.scan_shared_files()
        self.active_downloads = self.file_manager.scan_downloads()
        self.peer_comm.requested_pieces = {}
        for file_hash, info in self.active_downloads.items():
            self.peer_comm.bitfields[file_hash] = [1 if i in info["downloaded"] else 0 for i in range(info["total_pieces"])]
        for file_hash in self.shared_files:
            self.peer_comm.bitfields[file_hash] = [1] * len(self.shared_files[file_hash]["pieces"])

        EnhancedPeer.instance = self
        self.setup_ui()
        self.sync_with_tracker()

        self.log_to_ui(f"Peer ID: {self.peer_id}\n")
        self.log_to_ui(f"Using repository: {self.repository}\n")
        self.root.after(100, self.process_log_queue)

    def log_to_ui(self, message):
        self.log_queue.put(message)

    def process_log_queue(self):
        try:
            while not self.log_queue.empty():
                message = self.log_queue.get_nowait()
                self.log_text.config(state=tk.NORMAL)
                self.log_text.insert(tk.END, message)
                self.log_text.see(tk.END)
                self.log_text.config(state=tk.DISABLED)
        except Exception as e:
            print(f"[UI ERROR] Log processing failed: {str(e)}\n")
        if self.is_running:
            self.root.after(100, self.process_log_queue)

    def setup_ui(self):
        self.root.title(f"Peer-to-Peer Client (Port: {self.peer_comm.peer_port})")
        self.root.geometry("800x600")
        self.root.protocol("WM_DELETE_WINDOW", self.graceful_exit)

        button_frame = ttk.Frame(self.root, padding="10")
        button_frame.pack(fill=tk.X)
        ttk.Button(button_frame, text="Discover", command=lambda: threading.Thread(target=self.handle_discover, daemon=True).start()).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Exit", command=self.graceful_exit).pack(side=tk.RIGHT, padx=5)

        share_frame = ttk.Frame(self.root, padding="10")
        share_frame.pack(fill=tk.X)
        ttk.Label(share_frame, text="Share File:").pack(side=tk.LEFT)
        self.share_entry = ttk.Entry(share_frame, width=40)
        self.share_entry.pack(side=tk.LEFT, padx=5)
        ttk.Button(share_frame, text="Share", command=lambda: threading.Thread(target=self.share_file, args=(self.share_entry.get(),), daemon=True).start()).pack(side=tk.LEFT)

        self.shared_files_frame = ttk.LabelFrame(self.root, text="Shared Files", padding="10")
        self.shared_files_frame.pack(fill=tk.X, padx=10, pady=5)
        self.update_shared_files_ui()

        self.discover_frame = ttk.LabelFrame(self.root, text="Discovered Files", padding="10")
        self.discover_frame.pack(fill=tk.X, padx=10, pady=5)
        self.update_discover_ui([])  # Initialize empty

        self.status_frame = ttk.LabelFrame(self.root, text="Downloads", padding="10")
        self.status_frame.pack(fill=tk.X, padx=10, pady=5)

        self.log_text = scrolledtext.ScrolledText(self.root, height=15, width=90, state=tk.DISABLED)
        self.log_text.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)

        self.root.after(1000, self.update_status_ui)

    def update_shared_files_ui(self):
        try:
            for widget in self.shared_files_frame.winfo_children():
                widget.destroy()
            self.shared_files_widgets.clear()

            with self.lock:
                if not self.shared_files:
                    ttk.Label(self.shared_files_frame, text="No files currently shared").pack()
                else:
                    for file_hash, info in self.shared_files.items():
                        frame = ttk.Frame(self.shared_files_frame)
                        frame.pack(fill=tk.X, pady=2)
                        label = ttk.Label(frame, text=f"{info['file_name']} (Hash: {file_hash[:8]}...)")
                        label.pack(side=tk.LEFT)
                        stop_button = ttk.Button(frame, text="Stop Sharing",
                                                command=lambda fh=file_hash: threading.Thread(target=self.stop_sharing, args=(fh,), daemon=True).start())
                        stop_button.pack(side=tk.RIGHT)
                        self.shared_files_widgets[file_hash] = (frame, label, stop_button)
        except Exception as e:
            self.log_to_ui(f"[UI ERROR] Failed to update shared files UI: {str(e)}\n")

    def update_discover_ui(self, discovered_files):
        try:
            for widget in self.discover_frame.winfo_children():
                widget.destroy()
            self.discover_widgets.clear()

            if not discovered_files:
                ttk.Label(self.discover_frame, text="No files discovered yet").pack()
            else:
                for file in discovered_files:
                    file_hash = file["hash"]
                    frame = ttk.Frame(self.discover_frame)
                    frame.pack(fill=tk.X, pady=2)
                    peers_info = self.tracker.get_peers(file_hash, self.peer_id)
                    peers_str = ", ".join([f"{p['peer_id'][:8]}... ({p['ip']}:{p['port']})" for p in peers_info]) if peers_info else "No peers"
                    label = ttk.Label(frame, text=f"{file['file_name']} (Hash: {file_hash[:8]}..., Peers: {file.get('active_peers', 0)})")
                    label.pack(side=tk.LEFT)
                    download_button = ttk.Button(frame, text="Download",
                                                command=lambda fh=file_hash: threading.Thread(target=self.download_file, args=(fh,), daemon=True).start())
                    download_button.pack(side=tk.RIGHT)
                    self.discover_widgets[file_hash] = (frame, label, download_button)
                    self.log_to_ui(f"[DISCOVER] {file['file_name']} - Hash: {file_hash}, Peers: {peers_str}\n")
        except Exception as e:
            self.log_to_ui(f"[UI ERROR] Failed to update discover UI: {str(e)}\n")

    def update_status_ui(self):
        if not self.is_running:
            return
        try:
            for file_hash in list(self.status_widgets.keys()):
                if file_hash not in self.active_downloads:
                    frame, canvas, label, resume_btn, pause_btn = self.status_widgets.pop(file_hash)
                    frame.destroy()

            with self.lock:
                for file_hash, status in self.active_downloads.items():
                    total = status["total_pieces"]
                    downloaded = status["downloaded"]
                    if file_hash not in self.status_widgets:
                        frame = ttk.Frame(self.status_frame)
                        frame.pack(fill=tk.X, pady=2)
                        canvas = tk.Canvas(frame, height=20, width=300)
                        canvas.pack(side=tk.LEFT, padx=5)
                        label = ttk.Label(frame, text="")
                        label.pack(side=tk.LEFT)
                        resume_btn = ttk.Button(frame, text="Resume",
                                                command=lambda fh=file_hash: threading.Thread(target=self.resume_download, args=(fh,), daemon=True).start())
                        resume_btn.pack(side=tk.RIGHT, padx=2)
                        pause_btn = ttk.Button(frame, text="Pause",
                                               command=lambda fh=file_hash: threading.Thread(target=self.pause_download, args=(fh,), daemon=True).start())
                        pause_btn.pack(side=tk.RIGHT, padx=2)
                        self.status_widgets[file_hash] = (frame, canvas, label, resume_btn, pause_btn)

                    frame, canvas, label, resume_btn, pause_btn = self.status_widgets[file_hash]
                    canvas.delete("all")
                    piece_width = 300 / total
                    for i in range(total):
                        color = "green" if i in downloaded else "grey"
                        canvas.create_rectangle(i * piece_width, 0, (i + 1) * piece_width, 20, fill=color, outline="black")

                    downloading_info = ""
                    if file_hash in self.peer_comm.current_downloads:
                        for piece_idx, peer in self.peer_comm.current_downloads[file_hash].items():
                            downloading_info += f"Piece {piece_idx} from {peer}, "
                    label.config(text=f"{status['file_name']} ({file_hash[:8]}...): {len(downloaded)}/{total} {downloading_info.rstrip(', ')}")
                    resume_btn.config(state="normal" if status["status"] == "paused" else "disabled")
                    pause_btn.config(state="normal" if status["status"] == "downloading" else "disabled")
        except Exception as e:
            self.log_to_ui(f"[UI ERROR] Failed to update status UI: {str(e)}\n")
        if self.is_running:
            self.root.after(1000, self.update_status_ui)

    def start(self):
        threading.Thread(target=self.peer_comm.run_server, daemon=True).start()
        self.log_to_ui(f"\nPeer server running on port {self.peer_comm.peer_port}\n")
        self.root.mainloop()

    def sync_with_tracker(self):
        self.tracker.clear_peer(self.peer_id)
        ip = self.get_local_ip()
        with self.lock:
            for file_hash, info in self.shared_files.items():
                if not self.tracker.register_peer(file_hash, self.peer_id, self.peer_comm.peer_port, ip, info["pieces"], info["file_name"]):
                    self.log_to_ui(f"[-] Failed to sync shared file {file_hash} with tracker\n")
                else:
                    self.log_to_ui(f"[+] Synced shared file {file_hash} with tracker\n")
            for file_hash, info in self.active_downloads.items():
                if file_hash not in self.shared_files:  # Only register incomplete downloads
                    pieces = [{"index": i, "hash": "", "size": 0} for i in range(info["total_pieces"]) if i in info["downloaded"]]
                    if not self.tracker.register_peer(file_hash, self.peer_id, self.peer_comm.peer_port, ip, pieces, info["file_name"]):
                        self.log_to_ui(f"[-] Failed to sync download {file_hash} with tracker\n")
                    else:
                        self.log_to_ui(f"[+] Synced download {file_hash} with tracker\n")

    def share_file(self, file_path):
        try:
            full_path = os.path.abspath(file_path)
            if not os.path.isfile(full_path):
                self.log_to_ui(f"[-] File not found: {full_path}\n")
                return False

            file_hash = self.file_manager.calculate_file_hash(full_path)
            pieces = self.file_manager.split_file(full_path, file_hash)
            if not pieces:
                self.log_to_ui(f"[-] Failed to split file or file is empty: {full_path}\n")
                return False

            file_name = os.path.basename(full_path)
            if not self.tracker.register_peer(file_hash, self.peer_id, self.peer_comm.peer_port, self.get_local_ip(), pieces, file_name):
                self.log_to_ui(f"[-] Failed to share file with tracker\n")
                return False

            self.log_to_ui(f"[+] Shared successfully! File hash: {file_hash}\n")
            with self.lock:
                self.shared_files[file_hash] = {
                    "file_name": file_name,
                    "pieces": pieces,
                    "size": os.path.getsize(full_path)
                }
                self.peer_comm.bitfields[file_hash] = [1] * len(pieces)
            self.root.after(0, self.update_shared_files_ui)
            return True
        except Exception as e:
            self.log_to_ui(f"[ERROR] Share file failed: {str(e)}\n")
            return False

    def stop_sharing(self, file_hash):
        try:
            with self.lock:
                if file_hash not in self.shared_files:
                    self.log_to_ui(f"[-] File {file_hash[:8]}... not found in shared files\n")
                    return
                self.tracker.update_status(file_hash, self.peer_id, "stop")
                self.file_manager.cleanup_pieces(file_hash, self.shared_files[file_hash]["file_name"], is_shared=True)
                if file_hash in self.peer_comm.bitfields:
                    del self.peer_comm.bitfields[file_hash]
                del self.shared_files[file_hash]
                self.log_to_ui(f"[+] Stopped sharing {file_hash[:8]}... and cleaned up pieces\n")
            self.root.after(0, self.update_shared_files_ui)
        except Exception as e:
            self.log_to_ui(f"[ERROR] Stop sharing failed: {str(e)}\n")

    def download_file(self, file_hash):
        try:
            metadata_resp = self.tracker.get_metadata(file_hash)
            if not metadata_resp or "metadata" not in metadata_resp:
                self.log_to_ui(f"[-] Failed to get metadata: {metadata_resp.get('error', 'Unknown error')}\n")
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
                existing_pieces = self.file_manager.get_existing_pieces(file_hash, metadata["file_name"])
                self.active_downloads[file_hash]["downloaded"] = existing_pieces
                for idx in existing_pieces:
                    self.peer_comm.bitfields[file_hash][idx] = 1

            # Immediately start downloading
            peers = self.tracker.get_peers(file_hash, self.peer_id)
            if not peers:
                self.log_to_ui("[-] No peers available from tracker\n")
                return False

            self.tracker.register_peer(file_hash, self.peer_id, self.peer_comm.peer_port, self.get_local_ip(), metadata["pieces"], metadata["file_name"])

            needed_pieces = [p["index"] for p in metadata["pieces"] if p["index"] not in self.active_downloads[file_hash]["downloaded"]]
            if not needed_pieces:
                self.log_to_ui("[+] All pieces already downloaded\n")
                success = self.file_manager.reconstruct_file(file_hash, metadata["file_name"])
                if success:
                    with self.lock:
                        self.shared_files[file_hash] = {
                            "file_name": metadata["file_name"],
                            "pieces": metadata["pieces"],
                            "size": sum(p["size"] for p in metadata["pieces"])
                        }
                        old_folder = os.path.join(self.file_manager.downloads_dir, metadata["file_name"])
                        new_folder = os.path.join(self.file_manager.shared_files_dir, metadata["file_name"])
                        if os.path.exists(old_folder):
                            os.rename(old_folder, new_folder)
                    self.log_to_ui(f"[+] Download completed, now seeding {file_hash}\n")
                    self.root.after(0, self.update_shared_files_ui)
                return True

            self.log_to_ui(f"[+] Starting download of {file_hash[:8]}... with {len(needed_pieces)} pieces from {len(peers)} peers\n")
            threading.Thread(target=self.parallel_download, args=(file_hash, needed_pieces, peers), daemon=True).start()
            return True
        except Exception as e:
            self.log_to_ui(f"[ERROR] Download failed: {str(e)}\n")
            return False

    def resume_download(self, file_hash):
        try:
            with self.lock:
                if file_hash not in self.active_downloads:
                    self.log_to_ui(f"[-] Cannot resume {file_hash[:8]}...: not found\n")
                    return
                if self.active_downloads[file_hash]["status"] == "downloading":
                    self.log_to_ui(f"[-] {file_hash[:8]}... is already downloading\n")
                    return
                self.active_downloads[file_hash]["status"] = "downloading"

            metadata_path = os.path.join(self.file_manager.downloads_dir, self.active_downloads[file_hash]["file_name"], f"{file_hash}_metadata.json")
            with open(metadata_path, "r") as f:
                metadata = json.load(f)

            peers = self.tracker.get_peers(file_hash, self.peer_id)
            if not peers:
                self.log_to_ui("[-] No peers available from tracker\n")
                return

            self.tracker.register_peer(file_hash, self.peer_id, self.peer_comm.peer_port, self.get_local_ip(), metadata["pieces"], metadata["file_name"])

            needed_pieces = [p["index"] for p in metadata["pieces"] if p["index"] not in self.active_downloads[file_hash]["downloaded"]]
            if not needed_pieces:
                self.log_to_ui("[+] All pieces already downloaded\n")
                success = self.file_manager.reconstruct_file(file_hash, metadata["file_name"])
                if success:
                    with self.lock:
                        self.shared_files[file_hash] = {
                            "file_name": metadata["file_name"],
                            "pieces": metadata["pieces"],
                            "size": sum(p["size"] for p in metadata["pieces"])
                        }
                        old_folder = os.path.join(self.file_manager.downloads_dir, metadata["file_name"])
                        new_folder = os.path.join(self.file_manager.shared_files_dir, metadata["file_name"])
                        if os.path.exists(old_folder):
                            os.rename(old_folder, new_folder)
                    self.log_to_ui(f"[+] Download completed, now seeding {file_hash}\n")
                    self.root.after(0, self.update_shared_files_ui)
                return

            self.log_to_ui(f"[+] Resuming download of {file_hash[:8]}... with {len(needed_pieces)} pieces from {len(peers)} peers\n")
            threading.Thread(target=self.parallel_download, args=(file_hash, needed_pieces, peers), daemon=True).start()
        except Exception as e:
            self.log_to_ui(f"[ERROR] Resume failed: {str(e)}\n")

    def pause_download(self, file_hash):
        try:
            with self.lock:
                if file_hash not in self.active_downloads:
                    self.log_to_ui(f"[-] Cannot pause {file_hash[:8]}...: not found\n")
                    return
                if self.active_downloads[file_hash]["status"] == "paused":
                    self.log_to_ui(f"[-] {file_hash[:8]}... is already paused\n")
                    return
                self.active_downloads[file_hash]["status"] = "paused"
                if file_hash in self.peer_comm.current_downloads:
                    self.peer_comm.current_downloads[file_hash].clear()
            self.log_to_ui(f"[+] Paused download of {file_hash[:8]}...\n")
        except Exception as e:
            self.log_to_ui(f"[ERROR] Pause failed: {str(e)}\n")

    def parallel_download(self, file_hash, needed_pieces, peers):
        print(f"[DOWNLOAD DEBUG] Needed pieces: {needed_pieces}\n")
        print(f"[DOWNLOAD DEBUG] Initial peers: {peers}\n")

        if file_hash not in self.peer_comm.requested_pieces:
            self.peer_comm.requested_pieces[file_hash] = set()

        remaining_pieces = needed_pieces[:]
        current_peers = peers

        while remaining_pieces and self.is_running and self.active_downloads[file_hash]["status"] == "downloading":
            active_peers = []
            for peer in current_peers[:4]:
                if self.peer_comm.check_peer_connection(peer["ip"], peer["port"], file_hash, self.peer_id):
                    active_peers.append(peer)
                    peer_str = f"{peer['ip']}:{peer['port']}"
                    if peer_str not in self.peer_comm.download_queues:
                        self.peer_comm.download_queues[peer_str] = queue.Queue()
                        t = threading.Thread(target=self.peer_comm.download_worker, args=(peer["ip"], peer["port"]), daemon=True)
                        t.start()
                        self.peer_comm.peer_threads.append(t)
                        print(f"[DOWNLOAD DEBUG] Started worker for {peer_str}\n")

            if not active_peers:
                self.log_to_ui("[DOWNLOAD] No active peers available\n")
                return

            piece_rarity = {}
            for idx in remaining_pieces:
                count = sum(1 for p in active_peers if f"{p['ip']}:{p['port']}" in self.peer_comm.remote_bitfields and
                            file_hash in self.peer_comm.remote_bitfields[f"{p['ip']}:{p['port']}"] and
                            len(self.peer_comm.remote_bitfields[f"{p['ip']}:{p['port']}"][file_hash]) > idx and
                            self.peer_comm.remote_bitfields[f"{p['ip']}:{p['port']}"][file_hash][idx] == 1)
                piece_rarity[idx] = count if count > 0 else float('inf')

            assigned_pieces = set()
            for peer in active_peers:
                peer_str = f"{peer['ip']}:{peer['port']}"
                available_pieces = [
                    idx for idx in remaining_pieces
                    if peer_str in self.peer_comm.remote_bitfields and
                    file_hash in self.peer_comm.remote_bitfields[peer_str] and
                    len(self.peer_comm.remote_bitfields[peer_str][file_hash]) > idx and
                    self.peer_comm.remote_bitfields[peer_str][file_hash][idx] == 1 and
                    idx not in self.peer_comm.requested_pieces[file_hash] and
                    idx not in assigned_pieces
                ]
                if available_pieces:
                    min_rarity = min(piece_rarity[idx] for idx in available_pieces)
                    rarest_pieces = [idx for idx in available_pieces if piece_rarity[idx] == min_rarity]
                    piece_idx = random.choice(rarest_pieces)
                    self.peer_comm.requested_pieces[file_hash].add(piece_idx)
                    self.peer_comm.download_queues[peer_str].put((file_hash, piece_idx))
                    assigned_pieces.add(piece_idx)
                    print(f"[DOWNLOAD DEBUG] Assigned piece {piece_idx} (rarity: {piece_rarity[piece_idx]}) to {peer_str}\n")

            for peer in active_peers:
                peer_str = f"{peer['ip']}:{peer['port']}"
                if peer_str in self.peer_comm.download_queues:
                    self.peer_comm.download_queues[peer_str].join()

            with self.lock:
                for piece_idx in needed_pieces:
                    piece_path = os.path.join(self.file_manager.downloads_dir, self.active_downloads[file_hash]["file_name"], f"{file_hash}_piece_{piece_idx}")
                    if os.path.exists(piece_path) and piece_idx in remaining_pieces:
                        self.active_downloads[file_hash]["downloaded"].add(piece_idx)
                        remaining_pieces.remove(piece_idx)

            if not remaining_pieces:
                self.log_to_ui("[+] All pieces downloaded\n")
                success = self.file_manager.reconstruct_file(file_hash, self.active_downloads[file_hash]["file_name"])
                if success:
                    with self.lock:
                        file_name = self.active_downloads[file_hash]["file_name"]
                        metadata_path = os.path.join(self.file_manager.downloads_dir, file_name, f"{file_hash}_metadata.json")
                        with open(metadata_path, "r") as f:
                            metadata = json.load(f)
                        self.shared_files[file_hash] = {
                            "file_name": file_name,
                            "pieces": metadata["pieces"],
                            "size": sum(p["size"] for p in metadata["pieces"])
                        }
                        old_folder = os.path.join(self.file_manager.downloads_dir, file_name)
                        new_folder = os.path.join(self.file_manager.shared_files_dir, file_name)
                        if os.path.exists(old_folder):
                            os.rename(old_folder, new_folder)
                    self.log_to_ui(f"[+] Download completed, now seeding {file_hash}\n")
                    self.root.after(0, self.update_shared_files_ui)

            current_peers = self.tracker.get_peers(file_hash, self.peer_id)
            print(f"[DOWNLOAD DEBUG] Updated peers: {current_peers}\n")

    def handle_discover(self):
        try:
            response = self.tracker.discover_files()
            if response and "files" in response:
                self.root.after(0, lambda: self.update_discover_ui(response["files"]))
            else:
                self.log_to_ui("[-] Failed to discover files\n")
                self.root.after(0, lambda: self.update_discover_ui([]))
        except Exception as e:
            self.log_to_ui(f"[ERROR] Discover failed: {str(e)}\n")
            self.root.after(0, lambda: self.update_discover_ui([]))

    def graceful_exit(self):
        self.log_to_ui("\n[+] Shutting down...\n")
        self.is_running = False
        self.peer_comm.is_running = False
        for peer in self.peer_comm.download_queues:
            self.peer_comm.download_queues[peer].put(None)
        self.peer_comm.upload_queue.put(None)

        self.tracker.clear_peer(self.peer_id)
        self.executor.shutdown(wait=False)
        self.log_to_ui("[+] Peer stopped\n")
        self.root.quit()

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
    except Exception as e:
        print(f"[!] Fatal error: {str(e)}\n")