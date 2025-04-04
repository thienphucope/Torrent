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
import subprocess
import re
class TrackerCommunication:
    def __init__(self, tracker_host="localhost", tracker_port=8000):
        if tracker_host == "localhost" or tracker_host == "auto":
            discovered = self.discover_tracker(broadcast_port=8001)
            if discovered:
                self.tracker_host = discovered["ip"]
                self.tracker_port = discovered["port"]
                print(f"[Peer] Discovered tracker at {self.tracker_host}:{self.tracker_port}")
            else:
                print("[Peer] Failed to discover tracker, using default localhost")
                self.tracker_host = tracker_host
                self.tracker_port = tracker_port
        else:
            self.tracker_host = tracker_host
            self.tracker_port = tracker_port

    def discover_tracker(self, broadcast_port=8001, timeout=2):
        message = "where_are_you"
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(timeout)

        try:
            sock.sendto(message.encode(), ('<broadcast>', broadcast_port))
            data, addr = sock.recvfrom(1024)
            response = json.loads(data)
            print(f"Discovered tracker at {response['ip']}:{response['port']}")
            return response
        except socket.timeout:
            print("Tracker discovery timed out.")
        finally:
            sock.close()

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

    def get_metadata(self, torrent_hash):
        return self.send_to_tracker({"action": "get_metadata", "torrent_hash": torrent_hash})

    def get_peers(self, torrent_hash, my_peer_id):
        resp = self.send_to_tracker({"action": "get_peers", "torrent_hash": torrent_hash})
        if not resp or "peers" not in resp:
            print("[PEER DEBUG] No peers returned from tracker\n")
            return []
        peers = [p for p in resp["peers"] if p["peer_id"] != my_peer_id]
        for p in peers:
            p["latency"] = random.uniform(0.1, 0.5) if "latency" not in p else p["latency"]
        print(f"[PEER DEBUG] Peers received (excluding self): {peers}\n")
        return sorted(peers, key=lambda x: x["latency"])

    def get_torrent_hash_by_name(self, torrent_name):
        resp = self.send_to_tracker({"action": "get_torrent_hash", "torrent_name": torrent_name})
        return resp.get("torrent_hash") if resp else None

    def register_peer(self, torrent_hash, peer_id, port, ip, metadata):
        resp = self.send_to_tracker({
            "action": "share",
            "metadata": metadata,
            "peer_id": peer_id,
            "port": port,
            "ip": ip
        })
        return resp and resp.get("status") == "success"

    def update_status(self, torrent_hash, peer_id, status):
        resp = self.send_to_tracker({
            "action": "update_status",
            "torrent_hash": torrent_hash,
            "peer_id": peer_id,
            "status": status
        })
        return resp and resp.get("status") == "success"

    def discover_files(self):
        return self.send_to_tracker({"action": "discover"})

    def clear_peer(self, peer_id):
        resp = self.send_to_tracker({"action": "clear_peer", "peer_id": peer_id})
        return resp and resp.get("status") == "success"

class FileManager:
    def __init__(self, repository, piece_size=1 * 1024 * 1024):
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

    def calculate_torrent_hash(self, metadata):
        metadata_str = json.dumps(metadata, sort_keys=True)
        return hashlib.sha256(metadata_str.encode()).hexdigest()

    def split_files(self, file_paths):
        metadata = {"files": [], "pieces": []}
        torrent_name = "_".join(os.path.basename(p) for p in file_paths)
        subfolder = os.path.join(self.shared_files_dir, torrent_name)
        os.makedirs(subfolder, exist_ok=True)

        buffer = bytearray()
        piece_index = 0
        total_offset = 0

        for file_path in file_paths:
            file_name = os.path.basename(file_path)
            file_hash = self.calculate_file_hash(file_path)
            file_size = os.path.getsize(file_path)

            # Điều chỉnh piece_size nếu file lớn hơn 20MB
            piece_size = file_size // 20 if file_size > 20 * 1024 * 1024 else self.piece_size

            metadata["files"].append({
                "file_name": file_name,
                "file_hash": file_hash,
                "file_size": file_size,
                "offset": total_offset
            })

            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(piece_size - len(buffer))
                    if not chunk and not buffer:
                        break
                    buffer.extend(chunk)

                    if len(buffer) >= piece_size or (not chunk and buffer):
                        piece_data = bytes(buffer[:piece_size]) if len(buffer) >= piece_size else bytes(buffer)
                        piece_hash = hashlib.sha1(piece_data).hexdigest()
                        piece_path = os.path.join(subfolder, f"piece_{piece_index}")

                        with open(piece_path, "wb") as p:
                            p.write(piece_data)

                        metadata["pieces"].append({
                            "index": piece_index,
                            "hash": piece_hash,
                            "size": len(piece_data)
                        })

                        print(f"[FILE DEBUG] Created piece {piece_path} (size: {len(piece_data)} bytes)\n")

                        piece_index += 1
                        buffer = buffer[piece_size:] if len(buffer) > piece_size else bytearray()

            total_offset += file_size

        if buffer:
            piece_hash = hashlib.sha1(bytes(buffer)).hexdigest()
            piece_path = os.path.join(subfolder, f"piece_{piece_index}")

            with open(piece_path, "wb") as p:
                p.write(bytes(buffer))

            metadata["pieces"].append({
                "index": piece_index,
                "hash": piece_hash,
                "size": len(buffer)
            })

            print(f"[FILE DEBUG] Created final piece {piece_path} (size: {len(buffer)} bytes)\n")

        if not metadata["pieces"]:
            raise ValueError("No pieces created; files may be empty or unreadable")

        torrent_hash = self.calculate_torrent_hash(metadata)
        with open(os.path.join(subfolder, f"{torrent_hash}_metadata.json"), "w") as f:
            json.dump(metadata, f)

        print(f"[FILE DEBUG] Metadata saved: {torrent_hash}_metadata.json\n")
        
        return torrent_hash, metadata["pieces"], metadata

    def save_metadata(self, torrent_hash, metadata):
        torrent_name = "_".join(f["file_name"] for f in metadata["files"])
        subfolder = os.path.join(self.downloads_dir, torrent_name)
        os.makedirs(subfolder, exist_ok=True)
        metadata_path = os.path.join(subfolder, f"{torrent_hash}_metadata.json")
        with open(metadata_path, "w") as f:
            json.dump(metadata, f)
        print(f"[FILE DEBUG] Saved metadata to {metadata_path}\n")

    def get_existing_pieces(self, torrent_hash, torrent_name, is_shared=False):
        folder = self.shared_files_dir if is_shared else self.downloads_dir
        subfolder = os.path.join(folder, torrent_name)
        if not os.path.exists(subfolder):
            return set()
        pieces = set()
        for f in os.listdir(subfolder):
            if f.startswith("piece_"):
                try:
                    piece_idx = int(f.split('_')[1])
                    pieces.add(piece_idx)
                except (IndexError, ValueError):
                    continue
        print(f"[FILE DEBUG] Existing pieces for {torrent_hash} in {subfolder}: {pieces}\n")
        return pieces

    def reconstruct_files(self, torrent_hash, torrent_name):
        subfolder = os.path.join(self.downloads_dir, torrent_name)
        metadata_path = os.path.join(subfolder, f"{torrent_hash}_metadata.json")
        print(f"[FILE DEBUG] Checking metadata at: {metadata_path}\n")
        if not os.path.exists(metadata_path):
            print("[-] Metadata not found\n")
            return False

        with open(metadata_path, "r") as f:
            metadata = json.load(f)

        output_dir = os.path.join(self.repository, torrent_name)
        os.makedirs(output_dir, exist_ok=True)
        temp_files = {}

        try:
            for file_info in metadata["files"]:
                temp_path = os.path.join(output_dir, file_info["file_name"] + ".temp")
                temp_files[file_info["file_name"]] = temp_path
                with open(temp_path, "wb") as out_file:
                    offset = file_info["offset"]
                    file_size = file_info["file_size"]
                    bytes_written = 0
                    piece_idx = offset // self.piece_size
                    piece_offset = offset % self.piece_size

                    while bytes_written < file_size and piece_idx < len(metadata["pieces"]):
                        piece_path = os.path.join(subfolder, f"piece_{piece_idx}")
                        if not os.path.exists(piece_path):
                            print(f"[-] Missing piece {piece_idx} for {file_info['file_name']}\n")
                            for temp in temp_files.values():
                                if os.path.exists(temp):
                                    os.remove(temp)
                            return False
                        with open(piece_path, "rb") as piece_file:
                            piece_data = piece_file.read()
                            start = piece_offset if bytes_written == 0 else 0
                            remaining = file_size - bytes_written
                            end = min(start + remaining, len(piece_data))
                            out_file.write(piece_data[start:end])
                            bytes_written += end - start
                        piece_idx += 1
                        piece_offset = 0

            for file_info in metadata["files"]:
                temp_path = temp_files[file_info["file_name"]]
                if self.calculate_file_hash(temp_path) != file_info["file_hash"]:
                    print(f"[-] Integrity check failed for {file_info['file_name']}\n")
                    for temp in temp_files.values():
                        if os.path.exists(temp):
                            os.remove(temp)
                    return False
                final_path = os.path.join(output_dir, file_info["file_name"])
                os.replace(temp_path, final_path)
                print(f"[+] Reconstructed: {final_path}\n")

            return True
        except Exception as e:
            print(f"[-] Error reconstructing files: {str(e)}\n")
            for temp in temp_files.values():
                if os.path.exists(temp):
                    os.remove(temp)
            return False

    def cleanup_pieces(self, torrent_hash, torrent_name, is_shared=False):
        folder = self.shared_files_dir if is_shared else self.downloads_dir
        subfolder = os.path.join(folder, torrent_name)
        if os.path.exists(subfolder):
            for f in os.listdir(subfolder):
                if f.startswith("piece_") or f == f"{torrent_hash}_metadata.json":
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
                    torrent_hash = f.replace("_metadata.json", "")
                    metadata_path = os.path.join(subfolder_path, f)
                    try:
                        with open(metadata_path, "r") as mf:
                            metadata = json.load(mf)
                        size = sum(f["file_size"] for f in metadata["files"])
                        shared[torrent_hash] = {
                            "torrent_name": subfolder,
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
                    torrent_hash = f.replace("_metadata.json", "")
                    metadata_path = os.path.join(subfolder_path, f)
                    try:
                        with open(metadata_path, "r") as mf:
                            metadata = json.load(f)
                        downloaded = self.get_existing_pieces(torrent_hash, subfolder)
                        downloads[torrent_hash] = {
                            "torrent_name": subfolder,
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
                    torrent_hash = parts[1]
                    try:
                        remote_bitfield = json.loads(parts[2])
                        if peer not in self.remote_bitfields:
                            self.remote_bitfields[peer] = {}
                        self.remote_bitfields[peer][torrent_hash] = remote_bitfield
                        print(f"[PEER DEBUG] Updated bitfield from {peer} for {torrent_hash}: {remote_bitfield}\n")
                    except json.JSONDecodeError as e:
                        print(f"[PEER DEBUG] Invalid bitfield from {peer}: {str(e)}\n")
                local_bitfield = self.bitfields.get(torrent_hash, [])
                response = f"PONG {torrent_hash} {json.dumps(local_bitfield)}\n"
                conn.sendall(response.encode())
                print(f"[PEER DEBUG] Sent to {peer}: {response.strip()}\n")
            elif data.startswith("REQUEST"):
                _, torrent_hash, piece_idx = data.split()
                self.upload_queue.put((conn, torrent_hash, int(piece_idx), peer))
        except Exception as e:
            print(f"[PEER SERVER] Error with {peer}: {str(e)}\n")

    def upload_worker(self):
        while self.is_running:
            try:
                conn, torrent_hash, piece_idx, peer = self.upload_queue.get(timeout=1)
                torrent_name = EnhancedPeer.instance.active_downloads.get(torrent_hash, {}).get("torrent_name",
                            EnhancedPeer.instance.shared_files.get(torrent_hash, {}).get("torrent_name", ""))
                if not torrent_name:
                    self.log(f"[UPLOAD] Torrent name not found for {torrent_hash} to serve {peer}\n")
                    conn.sendall(b"PIECE_NOT_FOUND")
                    conn.close()
                    self.upload_queue.task_done()
                    continue

                folder = self.file_manager.shared_files_dir if torrent_hash in EnhancedPeer.instance.shared_files else self.file_manager.downloads_dir
                piece_path = os.path.join(folder, torrent_name, f"piece_{piece_idx}")
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
                item = download_queue.get(timeout=1)
                if item is None:  # Sentinel value to exit
                    break
                torrent_hash, piece_idx = item
                if self.download_piece(torrent_hash, piece_idx, peer_ip, peer_port):
                    torrent_name = EnhancedPeer.instance.active_downloads[torrent_hash]["torrent_name"]
                    metadata_path = os.path.join(self.file_manager.downloads_dir, torrent_name, f"{torrent_hash}_metadata.json")
                    if os.path.exists(metadata_path):
                        with open(metadata_path, "r") as f:
                            metadata = json.load(f)
                        EnhancedPeer.instance.tracker.update_status(torrent_hash, self.peer_id, "downloading")
                download_queue.task_done()
            except queue.Empty:
                continue

    def download_piece(self, torrent_hash, piece_idx, peer_ip, peer_port):
        peer = f"{peer_ip}:{peer_port}"
        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(f"[PEER DEBUG] Connecting to {peer} for piece {piece_idx}\n")
                with socket.create_connection((peer_ip, peer_port), timeout=10) as s:
                    s.settimeout(30)
                    request = f"REQUEST {torrent_hash} {piece_idx}\n"
                    print(f"[PEER DEBUG] Sending request to {peer}: {request.strip()}\n")
                    if torrent_hash not in self.current_downloads:
                        self.current_downloads[torrent_hash] = {}
                    self.current_downloads[torrent_hash][piece_idx] = peer
                    s.sendall(request.encode())
                    piece_data = b""
                    while True:
                        chunk = s.recv(16384)
                        if not chunk:
                            break
                        piece_data += chunk

                    if piece_data == b"PIECE_NOT_FOUND":
                        self.log(f"[DOWNLOAD] Piece {piece_idx} not found on {peer}\n")
                        if torrent_hash in self.current_downloads:
                            self.current_downloads[torrent_hash].pop(piece_idx, None)
                        return False

                    torrent_name = EnhancedPeer.instance.active_downloads.get(torrent_hash, {}).get("torrent_name", "")
                    if not torrent_name:
                        self.log(f"[DOWNLOAD] Torrent name not found for {torrent_hash} during download\n")
                        return False

                    piece_path = os.path.join(self.file_manager.downloads_dir, torrent_name, f"piece_{piece_idx}")
                    os.makedirs(os.path.dirname(piece_path), exist_ok=True)
                    print(f"[DOWNLOAD DEBUG] Saving piece to: {piece_path}\n")
                    with open(piece_path, "wb") as f:
                        f.write(piece_data)
                    bytes_received = len(piece_data)
                    self.log(f"[DOWNLOAD] Downloaded piece {piece_idx} ({bytes_received} bytes) from {peer} to {piece_path}\n")

                    if torrent_hash in self.bitfields:
                        self.bitfields[torrent_hash][piece_idx] = 1
                    self.download_stats[peer] = self.download_stats.get(peer, 0) + bytes_received
                    if torrent_hash in self.requested_pieces:
                        self.requested_pieces[torrent_hash].discard(piece_idx)
                    if torrent_hash in self.current_downloads:
                        self.current_downloads[torrent_hash].pop(piece_idx, None)
                    return True
            except Exception as e:
                print(f"[DOWNLOAD] Error for piece {piece_idx} on attempt {attempt+1} from {peer}: {str(e)}\n")
                time.sleep(1)
        self.log(f"[DOWNLOAD] Failed to download piece {piece_idx} from {peer} after {max_retries} attempts\n")
        if torrent_hash in self.requested_pieces:
            self.requested_pieces[torrent_hash].discard(piece_idx)
        if torrent_hash in self.current_downloads:
            self.current_downloads[torrent_hash].pop(piece_idx, None)
        return False

    def check_peer_connection(self, ip, port, torrent_hash, my_peer_id):
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
                local_bitfield = self.bitfields.get(torrent_hash, [])
                message = f"PING {torrent_hash} {json.dumps(local_bitfield)}\n"
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
                            self.remote_bitfields[peer][torrent_hash] = remote_bitfield
                            print(f"[PEER DEBUG] Updated bitfield from {peer} for {torrent_hash}: {remote_bitfield}\n")
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
        self.last_ui_update = 0  # For debouncing UI updates

        self.shared_files = self.file_manager.scan_shared_files()
        self.active_downloads = self.file_manager.scan_downloads()
        self.peer_comm.requested_pieces = {}
        for torrent_hash, info in self.active_downloads.items():
            self.peer_comm.bitfields[torrent_hash] = [1 if i in info["downloaded"] else 0 for i in range(info["total_pieces"])]
        for torrent_hash in self.shared_files:
            self.peer_comm.bitfields[torrent_hash] = [1] * len(self.shared_files[torrent_hash]["pieces"])

        EnhancedPeer.instance = self
        self.setup_ui()
        self.sync_with_tracker()

        self.log_to_ui(f"Peer ID: {self.peer_id} - {self.ip}:{peer_port}\n")
        self.log_to_ui(f"Using repository: {self.repository}\n")
        self.root.after(100, self.process_log_queue)

    def log_to_ui(self, message):
        if self.log_queue.qsize() < 100:  # Limit queue size to prevent overload
            self.log_queue.put(message)

    def process_log_queue(self):
        try:
            while not self.log_queue.empty():
                message = self.log_queue.get_nowait()
                self.log_text.config(state=tk.NORMAL)
                self.log_text.insert(tk.END, message)
                self.log_text.see(tk.END)
                self.log_text.config(state=tk.DISABLED)
                self.log_queue.task_done()
        except Exception as e:
            print(f"[UI ERROR] Log processing failed: {str(e)}\n")
        if self.is_running:
            self.root.after(200, self.process_log_queue)  # Increased interval to 200ms

    def setup_ui(self):
        self.root.title(f"Peer-to-Peer Client (Port: {self.peer_comm.peer_port})")
        self.root.geometry("800x600")
        self.root.protocol("WM_DELETE_WINDOW", self.graceful_exit)

        title_frame = ttk.Frame(self.root, padding="10")
        title_frame.pack(fill=tk.X)
        title_label = ttk.Label(title_frame, text="STA", font=("Helvetica", 24, "bold"))
        title_label.pack(expand=True)
        ttk.Button(title_frame, text="Exit", command=self.graceful_exit).pack(side=tk.RIGHT, padx=5)

        share_frame = ttk.Frame(self.root, padding="10")
        share_frame.pack(fill=tk.X)
        ttk.Label(share_frame, text="Share Files (comma-separated paths):").pack(side=tk.LEFT)
        self.share_entry = ttk.Entry(share_frame, width=40)
        self.share_entry.pack(side=tk.LEFT, padx=5)
        ttk.Button(share_frame, text="Share", command=lambda: threading.Thread(target=self.share_files, args=(self.share_entry.get(),), daemon=True).start()).pack(side=tk.LEFT)

        self.shared_files_frame = ttk.LabelFrame(self.root, text="Shared Torrents", padding="10")
        self.shared_files_frame.pack(fill=tk.X, padx=10, pady=5)
        self.update_shared_files_ui()

        self.discover_frame = ttk.LabelFrame(self.root, text="Discovered Torrents", padding="10")
        self.discover_frame.pack(fill=tk.X, padx=10, pady=5)
        discover_button = ttk.Button(self.discover_frame, text="Discover", command=lambda: threading.Thread(target=self.handle_discover, daemon=True).start())
        discover_button.pack(side=tk.TOP, pady=5)
        self.update_discover_ui([])

        download_frame = ttk.Frame(self.root, padding="10")
        download_frame.pack(fill=tk.X)
        ttk.Label(download_frame, text="Download (Torrent Hash/Path):").pack(side=tk.LEFT)
        self.download_entry = ttk.Entry(download_frame, width=40)
        self.download_entry.pack(side=tk.LEFT, padx=5)
        ttk.Button(download_frame, text="Download", command=lambda: threading.Thread(target=self.download_torrent, args=(self.download_entry.get(),), daemon=True).start()).pack(side=tk.LEFT)

        self.status_frame = ttk.LabelFrame(self.root, text="Downloads", padding="10")
        self.status_frame.pack(fill=tk.X, padx=10, pady=5)

        self.log_text = scrolledtext.ScrolledText(self.root, height=15, width=90, state=tk.DISABLED)
        self.log_text.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)

        self.root.after(1000, self.update_status_ui)

    def update_shared_files_ui(self):
        for widget in self.shared_files_frame.winfo_children():
            widget.destroy()
        self.shared_files_widgets.clear()

        with self.lock:
            if not self.shared_files:
                ttk.Label(self.shared_files_frame, text="No torrents currently shared").pack()
            else:
                for torrent_hash, info in self.shared_files.items():
                    frame = ttk.Frame(self.shared_files_frame)
                    frame.pack(fill=tk.X, pady=2)
                    label = ttk.Label(frame, text=f"{info['torrent_name']} (Hash: {torrent_hash[:8]}...)")
                    label.pack(side=tk.LEFT)
                    stop_button = ttk.Button(frame, text="Stop Sharing",
                                            command=lambda th=torrent_hash: threading.Thread(target=self.stop_sharing, args=(th,), daemon=True).start())
                    stop_button.pack(side=tk.RIGHT)
                    self.shared_files_widgets[torrent_hash] = (frame, label, stop_button)

    def update_discover_ui(self, discovered_files):
        for widget in self.discover_frame.winfo_children():
            if widget != self.discover_frame.winfo_children()[0]:  # Keep the Discover button
                widget.destroy()
        self.discover_widgets.clear()

        if not discovered_files:
            ttk.Label(self.discover_frame, text="No torrents discovered yet").pack(pady=5)
        else:
            for file in discovered_files:
                torrent_hash = file["hash"]
                frame = ttk.Frame(self.discover_frame)
                frame.pack(fill=tk.X, pady=2)
                peers_info = self.tracker.get_peers(torrent_hash, self.peer_id)
                peers_str = ", ".join([f"{p['peer_id'][:8]}... ({p['ip']}:{p['port']})" for p in peers_info]) if peers_info else "No peers"
                label = ttk.Label(frame, text=f"{file['torrent_name']} (Hash: {torrent_hash[:8]}..., Peers: {file.get('active_peers', 0)})")
                label.pack(side=tk.LEFT)
                download_button = ttk.Button(frame, text="Download",
                                            command=lambda th=torrent_hash: threading.Thread(target=self.download_torrent, args=(th,), daemon=True).start())
                download_button.pack(side=tk.RIGHT)
                self.discover_widgets[torrent_hash] = (frame, label, download_button)
                self.log_to_ui(f"[DISCOVER] {file['torrent_name']} - Hash: {torrent_hash}, Peers: {peers_str}\n")

    def update_status_ui(self):
        if not self.is_running:
            return
        current_time = time.time()
        if current_time - self.last_ui_update < 0.2:  # Debounce to 500ms
            self.root.after(200, self.update_status_ui)
            return
        self.last_ui_update = current_time

        try:
            with self.lock:
                for torrent_hash in list(self.status_widgets.keys()):
                    if torrent_hash not in self.active_downloads and torrent_hash not in self.shared_files:
                        frame = self.status_widgets.pop(torrent_hash)[0]
                        frame.destroy()

                for torrent_hash, status in self.active_downloads.items():
                    total = status["total_pieces"]
                    downloaded = status["downloaded"]
                    is_complete = len(downloaded) == total and os.path.exists(os.path.join(self.repository, status["torrent_name"]))

                    if torrent_hash not in self.status_widgets:
                        frame = ttk.Frame(self.status_frame)
                        frame.pack(fill=tk.X, pady=2)
                        canvas = tk.Canvas(frame, height=20, width=300)
                        canvas.pack(side=tk.LEFT, padx=5)
                        label = ttk.Label(frame, text="")
                        label.pack(side=tk.LEFT)
                        if not is_complete:
                            resume_btn = ttk.Button(frame, text="Resume",
                                                    command=lambda th=torrent_hash: threading.Thread(target=self.resume_download, args=(th,), daemon=True).start())
                            resume_btn.pack(side=tk.RIGHT, padx=2)
                            pause_btn = ttk.Button(frame, text="Pause",
                                                command=lambda th=torrent_hash: threading.Thread(target=self.pause_download, args=(th,), daemon=True).start())
                            pause_btn.pack(side=tk.RIGHT, padx=2)
                            self.status_widgets[torrent_hash] = (frame, canvas, label, resume_btn, pause_btn)
                        else:
                            completed_label = ttk.Label(frame, text="Completed")
                            completed_label.pack(side=tk.RIGHT, padx=5)
                            open_btn = ttk.Button(frame, text="Open",
                                                command=lambda th=torrent_hash: self.open_folder(status["torrent_name"]))
                            open_btn.pack(side=tk.RIGHT, padx=2)
                            self.status_widgets[torrent_hash] = (frame, canvas, label, completed_label, open_btn)

                    frame, canvas, label, *controls = self.status_widgets[torrent_hash]
                    canvas.delete("all")
                    piece_width = 300 / total
                    for i in range(total):
                        color = "green" if i in downloaded else "grey"
                        canvas.create_rectangle(i * piece_width, 0, (i + 1) * piece_width, 20, fill=color, outline="black")

                    downloading_info = ""
                    if torrent_hash in self.peer_comm.current_downloads and not is_complete:
                        for piece_idx, peer in self.peer_comm.current_downloads[torrent_hash].items():
                            downloading_info += f"Piece {piece_idx} from {peer}, "
                    label_text = f"{status['torrent_name']} ({torrent_hash[:8]}...): {len(downloaded)}/{total}"
                    if downloading_info and not is_complete:
                        label_text += f" {downloading_info.rstrip(', ')}"
                    label.config(text=label_text)

                    if not is_complete:
                        resume_btn, pause_btn = controls
                        resume_btn.config(state="normal" if status["status"] == "paused" else "disabled")
                        pause_btn.config(state="normal" if status["status"] == "downloading" else "disabled")
                    elif torrent_hash in self.active_downloads and is_complete:
                        for widget in frame.winfo_children():
                            if widget not in (canvas, label):
                                widget.destroy()
                        completed_label = ttk.Label(frame, text="Completed")
                        completed_label.pack(side=tk.RIGHT, padx=5)
                        open_btn = ttk.Button(frame, text="Open",
                                            command=lambda th=torrent_hash: self.open_folder(status["torrent_name"]))
                        open_btn.pack(side=tk.RIGHT, padx=2)
                        self.status_widgets[torrent_hash] = (frame, canvas, label, completed_label, open_btn)

        except Exception as e:
            self.log_to_ui(f"[UI ERROR] Failed to update status UI: {str(e)}\n")
        if self.is_running:
            self.root.after(200, self.update_status_ui)

    def open_folder(self, torrent_name):
        folder_path = os.path.abspath(os.path.join(self.repository, torrent_name))
        try:
            if sys.platform == "win32":
                os.startfile(folder_path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder_path])
            else:
                subprocess.Popen(["xdg-open", folder_path])
            self.log_to_ui(f"[+] Opened folder: {folder_path}\n")
        except Exception as e:
            self.log_to_ui(f"[ERROR] Failed to open folder {folder_path}: {str(e)}\n")

    def start(self):
        threading.Thread(target=self.peer_comm.run_server, daemon=True).start()
        self.log_to_ui(f"\nPeer server running on port {self.peer_comm.peer_port}\n")
        self.root.mainloop()

    def sync_with_tracker(self):
        self.tracker.clear_peer(self.peer_id)
        ip = self.get_local_ip()
        with self.lock:
            for torrent_hash, info in self.shared_files.items():
                metadata_path = os.path.join(self.file_manager.shared_files_dir, info["torrent_name"], f"{torrent_hash}_metadata.json")
                with open(metadata_path, "r") as f:
                    metadata = json.load(f)
                if not self.tracker.register_peer(torrent_hash, self.peer_id, self.peer_comm.peer_port, ip, metadata):
                    self.log_to_ui(f"[-] Failed to sync shared torrent {torrent_hash} with tracker\n")
                else:
                    self.log_to_ui(f"[+] Synced shared torrent {torrent_hash} with tracker\n")
            for torrent_hash, info in self.active_downloads.items():
                if torrent_hash not in self.shared_files:
                    metadata_path = os.path.join(self.file_manager.downloads_dir, info["torrent_name"], f"{torrent_hash}_metadata.json")
                    with open(metadata_path, "r") as f:
                        metadata = json.load(f)
                    if not self.tracker.register_peer(torrent_hash, self.peer_id, self.peer_comm.peer_port, ip, metadata):
                        self.log_to_ui(f"[-] Failed to sync download {torrent_hash} with tracker\n")
                    else:
                        self.log_to_ui(f"[+] Synced download {torrent_hash} with tracker\n")

    def share_files(self, file_paths_str):
        try:
            file_paths = [os.path.abspath(p.strip()) for p in file_paths_str.split(',')]
            for path in file_paths:
                if not os.path.isfile(path):
                    self.log_to_ui(f"[-] File not found: {path}\n")
                    return False

            torrent_hash, pieces, metadata = self.file_manager.split_files(file_paths)
            if not pieces:
                self.log_to_ui(f"[-] Failed to split files or files are empty\n")
                return False

            torrent_name = "_".join(f["file_name"] for f in metadata["files"])
            if not self.tracker.register_peer(torrent_hash, self.peer_id, self.peer_comm.peer_port, self.get_local_ip(), metadata):
                self.log_to_ui(f"[-] Failed to share torrent with tracker\n")
                return False

            self.log_to_ui(f"[+] Shared successfully! Torrent hash: {torrent_hash}\n")
            with self.lock:
                self.shared_files[torrent_hash] = {
                    "torrent_name": torrent_name,
                    "pieces": pieces,
                    "size": sum(f["file_size"] for f in metadata["files"])
                }
                self.peer_comm.bitfields[torrent_hash] = [1] * len(pieces)
            self.root.after(0, self.update_shared_files_ui)
            return True
        except Exception as e:
            self.log_to_ui(f"[ERROR] Share files failed: {str(e)}\n")
            return False

    def stop_sharing(self, torrent_hash):
        try:
            with self.lock:
                if torrent_hash not in self.shared_files:
                    self.log_to_ui(f"[-] Torrent {torrent_hash[:8]}... not found in shared files\n")
                    return
                self.tracker.update_status(torrent_hash, self.peer_id, "stop")
                self.file_manager.cleanup_pieces(torrent_hash, self.shared_files[torrent_hash]["torrent_name"], is_shared=True)
                if torrent_hash in self.peer_comm.bitfields:
                    del self.peer_comm.bitfields[torrent_hash]
                del self.shared_files[torrent_hash]
                self.log_to_ui(f"[+] Stopped sharing {torrent_hash[:8]}... and cleaned up pieces\n")
            self.root.after(0, self.update_shared_files_ui)
        except Exception as e:
            self.log_to_ui(f"[ERROR] Stop sharing failed: {str(e)}\n")

    def download_torrent(self, identifier):
        try:
            if os.path.exists(identifier):
                with open(identifier, "r") as f:
                    metadata = json.load(f)
                torrent_hash = self.file_manager.calculate_torrent_hash(metadata)
            else:
                # Nếu identifier là magnet link thì trích xuất hash
                if identifier.startswith("magnet:?"):
                    match = re.search(r'btih:([a-fA-F0-9]+)', identifier)
                    if match:
                        torrent_hash = match.group(1)
                    else:
                        self.log_to_ui("[-] Invalid magnet link format.\n")
                        return False
                else:
                    torrent_hash = identifier

                metadata_resp = self.tracker.get_metadata(torrent_hash)
                if not metadata_resp or "metadata" not in metadata_resp:
                    self.log_to_ui(f"[-] Failed to get metadata: {metadata_resp.get('error', 'Unknown error')}\n")
                    return False
                metadata = metadata_resp["metadata"]

            self.file_manager.save_metadata(torrent_hash, metadata)
            torrent_name = "_".join(f["file_name"] for f in metadata["files"])

            with self.lock:
                if torrent_hash not in self.active_downloads:
                    self.active_downloads[torrent_hash] = {
                        "torrent_name": torrent_name,
                        "total_pieces": len(metadata["pieces"]),
                        "downloaded": set(),
                        "status": "downloading"
                    }
                if torrent_hash not in self.peer_comm.bitfields:
                    self.peer_comm.bitfields[torrent_hash] = [0] * len(metadata["pieces"])
                existing_pieces = self.file_manager.get_existing_pieces(torrent_hash, torrent_name)
                self.active_downloads[torrent_hash]["downloaded"] = existing_pieces
                for idx in existing_pieces:
                    self.peer_comm.bitfields[torrent_hash][idx] = 1

            peers = self.tracker.get_peers(torrent_hash, self.peer_id)
            if not peers:
                self.log_to_ui("[-] No peers available from tracker\n")
                return False

            self.tracker.register_peer(torrent_hash, self.peer_id, self.peer_comm.peer_port, self.get_local_ip(), metadata)

            needed_pieces = [p["index"] for p in metadata["pieces"] if p["index"] not in self.active_downloads[torrent_hash]["downloaded"]]
            if not needed_pieces:
                self.log_to_ui("[+] All pieces already downloaded\n")
                success = self.file_manager.reconstruct_files(torrent_hash, torrent_name)
                if success:
                    with self.lock:
                        if torrent_hash in self.shared_files:
                            self.file_manager.cleanup_pieces(torrent_hash, torrent_name)
                            del self.active_downloads[torrent_hash]
                        else:
                            self.shared_files[torrent_hash] = {
                                "torrent_name": torrent_name,
                                "pieces": metadata["pieces"],
                                "size": sum(f["file_size"] for f in metadata["files"])
                            }
                            old_folder = os.path.join(self.file_manager.downloads_dir, torrent_name)
                            new_folder = os.path.join(self.file_manager.shared_files_dir, torrent_name)
                            if os.path.exists(old_folder):
                                os.rename(old_folder, new_folder)
                    self.root.after(0, self.update_shared_files_ui)
                    self.root.after(0, self.update_status_ui)
                    self.log_to_ui(f"[+] Download completed, now seeding {torrent_hash}\n")
                return True

            self.log_to_ui(f"[+] Starting download of {torrent_hash[:8]}... with {len(needed_pieces)} pieces from {len(peers)} peers\n")
            threading.Thread(target=self.parallel_download, args=(torrent_hash, needed_pieces, peers), daemon=True).start()
            return True
        except Exception as e:
            self.log_to_ui(f"[ERROR] Download failed: {str(e)}\n")
            return False

    def resume_download(self, torrent_hash):
        try:
            with self.lock:
                if torrent_hash not in self.active_downloads:
                    self.log_to_ui(f"[-] Cannot resume {torrent_hash[:8]}...: not found\n")
                    return
                if self.active_downloads[torrent_hash]["status"] == "downloading":
                    self.log_to_ui(f"[-] {torrent_hash[:8]}... is already downloading\n")
                    return
                self.active_downloads[torrent_hash]["status"] = "downloading"

            metadata_path = os.path.join(self.file_manager.downloads_dir, self.active_downloads[torrent_hash]["torrent_name"], f"{torrent_hash}_metadata.json")
            with open(metadata_path, "r") as f:
                metadata = json.load(f)

            peers = self.tracker.get_peers(torrent_hash, self.peer_id)
            if not peers:
                self.log_to_ui("[-] No peers available from tracker\n")
                return

            self.tracker.register_peer(torrent_hash, self.peer_id, self.peer_comm.peer_port, self.get_local_ip(), metadata)

            needed_pieces = [p["index"] for p in metadata["pieces"] if p["index"] not in self.active_downloads[torrent_hash]["downloaded"]]
            if not needed_pieces:
                self.log_to_ui("[+] All pieces already downloaded\n")
                success = self.file_manager.reconstruct_files(torrent_hash, self.active_downloads[torrent_hash]["torrent_name"])
                if success:
                    with self.lock:
                        if torrent_hash in self.shared_files:
                            self.file_manager.cleanup_pieces(torrent_hash, self.active_downloads[torrent_hash]["torrent_name"])
                            del self.active_downloads[torrent_hash]
                        else:
                            self.shared_files[torrent_hash] = {
                                "torrent_name": self.active_downloads[torrent_hash]["torrent_name"],
                                "pieces": metadata["pieces"],
                                "size": sum(f["file_size"] for f in metadata["files"])
                            }
                            old_folder = os.path.join(self.file_manager.downloads_dir, self.active_downloads[torrent_hash]["torrent_name"])
                            new_folder = os.path.join(self.file_manager.shared_files_dir, self.active_downloads[torrent_hash]["torrent_name"])
                            if os.path.exists(old_folder):
                                os.rename(old_folder, new_folder)
                    self.root.after(0, self.update_shared_files_ui)
                    self.root.after(0, self.update_status_ui)
                    self.log_to_ui(f"[+] Download completed, now seeding {torrent_hash}\n")
                return

            self.log_to_ui(f"[+] Resuming download of {torrent_hash[:8]}... with {len(needed_pieces)} pieces from {len(peers)} peers\n")
            threading.Thread(target=self.parallel_download, args=(torrent_hash, needed_pieces, peers), daemon=True).start()
        except Exception as e:
            self.log_to_ui(f"[ERROR] Resume failed: {str(e)}\n")

    def pause_download(self, torrent_hash):
        try:
            with self.lock:
                if torrent_hash not in self.active_downloads:
                    self.log_to_ui(f"[-] Cannot pause {torrent_hash[:8]}...: not found\n")
                    return
                if self.active_downloads[torrent_hash]["status"] == "paused":
                    self.log_to_ui(f"[-] {torrent_hash[:8]}... is already paused\n")
                    return
                self.active_downloads[torrent_hash]["status"] = "paused"
                if torrent_hash in self.peer_comm.current_downloads:
                    self.peer_comm.current_downloads[torrent_hash].clear()
            self.log_to_ui(f"[+] Paused download of {torrent_hash[:8]}...\n")
        except Exception as e:
            self.log_to_ui(f"[ERROR] Pause failed: {str(e)}\n")

    def parallel_download(self, torrent_hash, needed_pieces, peers):
        print(f"[DOWNLOAD DEBUG] Needed pieces: {needed_pieces}\n")
        print(f"[DOWNLOAD DEBUG] Initial peers: {peers}\n")

        if torrent_hash not in self.peer_comm.requested_pieces:
            self.peer_comm.requested_pieces[torrent_hash] = set()

        remaining_pieces = needed_pieces[:]
        current_peers = peers

        active_threads = []
        try:
            while remaining_pieces and self.is_running and self.active_downloads[torrent_hash]["status"] == "downloading":
                active_peers = []
                for peer in current_peers[:4]:
                    if self.peer_comm.check_peer_connection(peer["ip"], peer["port"], torrent_hash, self.peer_id):
                        active_peers.append(peer)
                        peer_str = f"{peer['ip']}:{peer['port']}"
                        if peer_str not in self.peer_comm.download_queues:
                            self.peer_comm.download_queues[peer_str] = queue.Queue()
                            t = threading.Thread(target=self.peer_comm.download_worker, args=(peer["ip"], peer["port"]), daemon=True)
                            t.start()
                            active_threads.append(t)
                            self.peer_comm.peer_threads.append(t)
                            print(f"[DOWNLOAD DEBUG] Started worker for {peer_str}\n")

                if not active_peers:
                    self.log_to_ui("[DOWNLOAD] No active peers available\n")
                    break

                piece_rarity = {}
                for idx in remaining_pieces:
                    count = sum(1 for p in active_peers if f"{p['ip']}:{p['port']}" in self.peer_comm.remote_bitfields and
                                torrent_hash in self.peer_comm.remote_bitfields[f"{p['ip']}:{p['port']}"] and
                                len(self.peer_comm.remote_bitfields[f"{p['ip']}:{p['port']}"][torrent_hash]) > idx and
                                self.peer_comm.remote_bitfields[f"{p['ip']}:{p['port']}"][torrent_hash][idx] == 1)
                    piece_rarity[idx] = count if count > 0 else float('inf')

                assigned_pieces = set()
                for peer in active_peers:
                    peer_str = f"{peer['ip']}:{peer['port']}"
                    available_pieces = [
                        idx for idx in remaining_pieces
                        if peer_str in self.peer_comm.remote_bitfields and
                        torrent_hash in self.peer_comm.remote_bitfields[peer_str] and
                        len(self.peer_comm.remote_bitfields[peer_str][torrent_hash]) > idx and
                        self.peer_comm.remote_bitfields[peer_str][torrent_hash][idx] == 1 and
                        idx not in self.peer_comm.requested_pieces[torrent_hash] and
                        idx not in assigned_pieces
                    ]
                    if available_pieces:
                        min_rarity = min(piece_rarity[idx] for idx in available_pieces)
                        rarest_pieces = [idx for idx in available_pieces if piece_rarity[idx] == min_rarity]
                        piece_idx = random.choice(rarest_pieces)
                        self.peer_comm.requested_pieces[torrent_hash].add(piece_idx)
                        self.peer_comm.download_queues[peer_str].put((torrent_hash, piece_idx))
                        assigned_pieces.add(piece_idx)
                        print(f"[DOWNLOAD DEBUG] Assigned piece {piece_idx} (rarity: {piece_rarity[piece_idx]}) to {peer_str}\n")

                for peer in active_peers:
                    peer_str = f"{peer['ip']}:{peer['port']}"
                    if peer_str in self.peer_comm.download_queues:
                        self.peer_comm.download_queues[peer_str].join()

                with self.lock:
                    for piece_idx in needed_pieces:
                        piece_path = os.path.join(self.file_manager.downloads_dir, self.active_downloads[torrent_hash]["torrent_name"], f"piece_{piece_idx}")
                        if os.path.exists(piece_path) and piece_idx in remaining_pieces:
                            self.active_downloads[torrent_hash]["downloaded"].add(piece_idx)
                            remaining_pieces.remove(piece_idx)

                if not remaining_pieces:
                    self.log_to_ui("[+] All pieces downloaded\n")
                    success = self.file_manager.reconstruct_files(torrent_hash, self.active_downloads[torrent_hash]["torrent_name"])
                    if success:
                        with self.lock:
                            torrent_name = self.active_downloads[torrent_hash]["torrent_name"]
                            metadata_path = os.path.join(self.file_manager.downloads_dir, torrent_name, f"{torrent_hash}_metadata.json")
                            with open(metadata_path, "r") as f:
                                metadata = json.load(f)
                            if torrent_hash in self.shared_files:
                                self.file_manager.cleanup_pieces(torrent_hash, torrent_name)
                                del self.active_downloads[torrent_hash]
                            else:
                                self.shared_files[torrent_hash] = {
                                    "torrent_name": torrent_name,
                                    "pieces": metadata["pieces"],
                                    "size": sum(f["file_size"] for f in metadata["files"])
                                }
                                old_folder = os.path.join(self.file_manager.downloads_dir, torrent_name)
                                new_folder = os.path.join(self.file_manager.shared_files_dir, torrent_name)
                                if os.path.exists(old_folder):
                                    os.rename(old_folder, new_folder)
                        self.root.after(100, self.update_shared_files_ui)
                        self.root.after(0, self.update_status_ui)
                        self.log_to_ui(f"[+] Download completed, now seeding {torrent_hash}\n")

                current_peers = self.tracker.get_peers(torrent_hash, self.peer_id)
                print(f"[DOWNLOAD DEBUG] Updated peers: {current_peers}\n")

        finally:
            # Cleanup download threads
            for peer in active_peers:
                peer_str = f"{peer['ip']}:{peer['port']}"
                if peer_str in self.peer_comm.download_queues:
                    self.peer_comm.download_queues[peer_str].put(None)  # Signal thread to exit
            for t in active_threads:
                t.join(timeout=2)  # Wait for threads to finish with a timeout
            # Clear download queues and threads for this torrent
            with self.lock:
                self.peer_comm.peer_threads = [t for t in self.peer_comm.peer_threads if t not in active_threads]
                for peer in active_peers:
                    peer_str = f"{peer['ip']}:{peer['port']}"
                    if peer_str in self.peer_comm.download_queues:
                        del self.peer_comm.download_queues[peer_str]
                if torrent_hash in self.peer_comm.requested_pieces:
                    del self.peer_comm.requested_pieces[torrent_hash]
            print(f"[DOWNLOAD DEBUG] Cleaned up download threads for {torrent_hash}\n")

    def handle_discover(self):
        try:
            response = self.tracker.discover_files()
            if response and "files" in response:
                self.root.after(0, lambda: self.update_discover_ui(response["files"]))
            else:
                self.log_to_ui("[-] Failed to discover torrents\n")
                self.root.after(0, lambda: self.update_discover_ui([]))
        except Exception as e:
            self.log_to_ui(f"[ERROR] Discover failed: {str(e)}\n")
            self.root.after(0, lambda: self.update_discover_ui([]))

    def graceful_exit(self):
        self.log_to_ui("\n[+] Shutting down...\n")
        self.is_running = False
        self.peer_comm.is_running = False
        # Signal all download threads to exit
        for peer in self.peer_comm.download_queues:
            self.peer_comm.download_queues[peer].put(None)
        self.peer_comm.upload_queue.put(None)

        self.tracker.clear_peer(self.peer_id)
        self.executor.shutdown(wait=False)
        # Wait for download threads to finish
        for t in self.peer_comm.peer_threads:
            t.join(timeout=2)
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