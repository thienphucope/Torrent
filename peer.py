import socket
import threading
import os
import hashlib
import requests
import time
from queue import PriorityQueue

class Peer:
    def __init__(self, host="localhost", port=6881, peer_id=None):
        self.host = host
        self.port = port
        self.peer_id = peer_id or os.urandom(20).hex()
        self.files = {}  # {torrent_hash: {"piece_paths": [], "hashes": [], "bitfield": [], "output_name": str}}
        self.running = True
        self.lock = threading.Lock()
        self.piece_queue = PriorityQueue()
        self.addr = f"{self.host}:{self.port}"
        self.is_seeding = {}
        self.connected_peers = set()
        print(f"DEBUG: Initialized peer with port {self.port}")

    def start(self):
        threading.Thread(target=self.listen_for_peers, daemon=True).start()
        print(f"DEBUG: Peer {self.port} started listening")

    def listen_for_peers(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(5)
        print(f"DEBUG: Peer {self.port} listening")
        while self.running:
            client, addr = server.accept()
            peer_addr = f"{addr[0]}:{addr[1]}"
            print(f"DEBUG: Peer {self.port} accepted connection from {peer_addr}")
            threading.Thread(target=self.handle_peer, args=(client, peer_addr), daemon=True).start()

    def handle_peer(self, sock, peer_addr):
        data = sock.recv(1024).decode()
        if data.startswith("HANDSHAKE"):
            torrent_hash = data.split()[1]
            sock.send(f"HANDSHAKE {torrent_hash} {self.peer_id}".encode())
            print(f"DEBUG: Peer {self.port} handshaked with {peer_addr} for {torrent_hash}")
            while self.running:
                msg = sock.recv(1024).decode()
                if not msg:
                    break
                if msg.startswith("BITFIELD"):
                    bitfield = [int(x) for x in msg.split()[1].split(",")]
                    print(f"DEBUG: Peer {self.port} got bitfield from {peer_addr}: {bitfield}")
                    sock.send(f"BITFIELD {','.join(map(str, self.files[torrent_hash]['bitfield']))}".encode())
                    print(f"DEBUG: Peer {self.port} sent bitfield to {peer_addr}: {self.files[torrent_hash]['bitfield']}")
                elif msg.startswith("REQUEST"):
                    piece_idx = int(msg.split()[1])
                    with open(self.files[torrent_hash]["piece_paths"][piece_idx], "rb") as f:
                        sock.send(f.read())
                    print(f"DEBUG: Peer {self.port} sent piece {piece_idx} to {peer_addr}")

    def seed(self, filename):
        torrent_hash = hashlib.sha1(filename.encode()).hexdigest()
        os.makedirs(f"seed/{torrent_hash}", exist_ok=True)
        piece_paths, hashes = self.split_file(filename, torrent_hash, is_seeding=True)
        self.files[torrent_hash] = {
            "piece_paths": piece_paths,
            "hashes": hashes,
            "bitfield": [1] * len(piece_paths)
        }
        self.is_seeding[torrent_hash] = True
        print(f"DEBUG: Peer {self.port} split {filename} into {len(piece_paths)} pieces")
        self.register_with_tracker(torrent_hash)
        print(f"DEBUG: Peer {self.port} seeding {torrent_hash}")

    def split_file(self, filename, torrent_hash, is_seeding=False):
        piece_size = 1460
        piece_paths = []
        hashes = []
        base_dir = f"seed/{torrent_hash}" if is_seeding else f"downloads{self.port}/{torrent_hash}"
        os.makedirs(base_dir, exist_ok=True)
        
        with open(filename, "rb") as f:
            for i in range((os.path.getsize(filename) + piece_size - 1) // piece_size):
                chunk = f.read(piece_size)
                piece_path = f"{base_dir}/piece_{i}"
                with open(piece_path, "wb") as pf:
                    pf.write(chunk)
                piece_paths.append(piece_path)
                hashes.append(hashlib.sha1(chunk).hexdigest())
        return piece_paths, hashes

    def download(self, torrent_hash, output_name, piece_count):
        os.makedirs(f"downloads{self.port}/{torrent_hash}", exist_ok=True)
        piece_paths = [f"downloads{self.port}/{torrent_hash}/piece_{i}" for i in range(piece_count)]
        self.files[torrent_hash] = {
            "piece_paths": piece_paths,
            "hashes": [None] * piece_count,
            "bitfield": [0] * piece_count,
            "output_name": output_name
        }
        self.is_seeding[torrent_hash] = False
        self.connected_peers.clear()
        print(f"DEBUG: Peer {self.port} initialized download {torrent_hash} ({piece_count} pieces)")
        self.register_with_tracker(torrent_hash)
        peers = self.get_peers_from_tracker(torrent_hash)
        print(f"DEBUG: Peer {self.port} got {len(peers)} peers from tracker")

        valid_peers = [p for p in peers if p["port"] != self.port]
        print(f"DEBUG: Peer {self.port} found {len(valid_peers)} valid peers to connect to")

        for peer in valid_peers:
            peer_id = f"{peer['ip']}:{peer['port']}"
            if peer_id not in self.connected_peers:
                self.connected_peers.add(peer_id)
                threading.Thread(target=self.download_from_peer, args=(peer, torrent_hash), daemon=True).start()

        if not self.is_seeding.get(torrent_hash, False):
            threading.Thread(target=self.periodic_peer_lookup, args=(torrent_hash,), daemon=True).start()

    def register_with_tracker(self, torrent_hash):
        url = f"http://localhost:8000/register?torrent_hash={torrent_hash}&peer_id={self.peer_id}&port={self.port}"
        requests.get(url)
        print(f"DEBUG: Peer {self.port} registered with tracker for {torrent_hash}")

    def get_peers_from_tracker(self, torrent_hash):
        url = f"http://localhost:8000/get_peers?torrent_hash={torrent_hash}"
        response = requests.get(url)
        peers = response.json()["peers"]
        unique_peers = []
        seen = set()
        for peer in peers:
            peer_id = f"{peer['ip']}:{peer['port']}"
            if peer_id not in seen:
                seen.add(peer_id)
                unique_peers.append(peer)
        return unique_peers

    def periodic_peer_lookup(self, torrent_hash):
        while self.running:
            peers = self.get_peers_from_tracker(torrent_hash)
            valid_peers = [p for p in peers if p["port"] != self.port]
            print(f"DEBUG: Peer {self.port} lookup found {len(valid_peers)} valid peers")
            for peer in valid_peers:
                peer_id = f"{peer['ip']}:{peer['port']}"
                if peer_id not in self.connected_peers:
                    self.connected_peers.add(peer_id)
                    threading.Thread(target=self.download_from_peer, args=(peer, torrent_hash), daemon=True).start()
            time.sleep(60)

    def download_from_peer(self, peer, torrent_hash):
        peer_addr = f"{peer['ip']}:{peer['port']}"
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect((peer["ip"], peer["port"]))
            sock.send(f"HANDSHAKE {torrent_hash}".encode())
            response = sock.recv(1024).decode()
            if response.startswith("HANDSHAKE"):
                print(f"DEBUG: Peer {self.port} connected to {peer_addr}")
                sock.send(f"BITFIELD {','.join(map(str, self.files[torrent_hash]['bitfield']))}".encode())
                print(f"DEBUG: Peer {self.port} sent bitfield to {peer_addr}: {self.files[torrent_hash]['bitfield']}")
                their_bitfield = [int(x) for x in sock.recv(1024).decode().split()[1].split(",")]
                print(f"DEBUG: Peer {self.port} got bitfield from {peer_addr}: {their_bitfield}")

                piece_counts = {}
                for i in range(len(their_bitfield)):
                    if their_bitfield[i]:
                        piece_counts[i] = piece_counts.get(i, 0) + 1

                for i in sorted(piece_counts.keys(), key=lambda x: piece_counts[x]):
                    if their_bitfield[i] and not self.files[torrent_hash]["bitfield"][i]:
                        sock.send(f"REQUEST {i}".encode())
                        piece_data = sock.recv(1460)
                        if not piece_data:
                            break
                        computed_hash = hashlib.sha1(piece_data).hexdigest()
                        with self.lock:
                            piece_path = self.files[torrent_hash]["piece_paths"][i]
                            with open(piece_path, "wb") as f:
                                f.write(piece_data)
                            self.files[torrent_hash]["hashes"][i] = computed_hash
                            self.files[torrent_hash]["bitfield"][i] = 1
                            print(f"DEBUG: Peer {self.port} downloaded piece {i} from {peer_addr}")
                self.assemble_file(torrent_hash)
        except Exception as e:
            print(f"DEBUG: Peer {self.port} failed to connect to {peer_addr}: {e}")
        finally:
            sock.close()

    def assemble_file(self, torrent_hash):
        if not self.is_seeding.get(torrent_hash, False) and all(self.files[torrent_hash]["bitfield"]):
            output_name = self.files[torrent_hash]["output_name"]
            output_path = f"downloads{self.port}/{output_name}"
            with open(output_path, "wb") as f:
                for i, piece_path in enumerate(self.files[torrent_hash]["piece_paths"]):
                    with open(piece_path, "rb") as pf:
                        piece_data = pf.read()
                    computed_hash = hashlib.sha1(piece_data).hexdigest()
                    if computed_hash == self.files[torrent_hash]["hashes"][i]:
                        f.write(piece_data)
                        print(f"DEBUG: Peer {self.port} verified piece {i}")
                    else:
                        print(f"DEBUG: Peer {self.port} piece {i} hash mismatch")
            print(f"DEBUG: Peer {self.port} assembled {output_name}")
            requests.get(f"http://localhost:8000/status?torrent_hash={torrent_hash}&peer_id={self.peer_id}&status=completed")
            print(f"DEBUG: Peer {self.port} completed {output_name}")

    def exit(self):
        self.running = False
        for torrent_hash in self.files:
            requests.get(f"http://localhost:8000/status?torrent_hash={torrent_hash}&peer_id={self.peer_id}&status=stopped")
            print(f"DEBUG: Peer {self.port} stopped {torrent_hash}")

if __name__ == "__main__":
    peer1 = Peer(port=6881)  # Seeder
    peer2 = Peer(port=6882)  # Leecher 1
    peer3 = Peer(port=6883)  # Leecher 2

    peer1.start()
    peer2.start()
    peer1.seed("example.txt")

    torrent_hash = hashlib.sha1("example.txt".encode()).hexdigest()
    with open("example.txt", "rb") as f:
        file_size = len(f.read())
    piece_count = (file_size + 1459) // 1460

    peer2.download(torrent_hash, "downloaded_example.txt", piece_count)
    time.sleep(2)

    peer3.start()
    peer3.download(torrent_hash, "downloaded_example.txt", piece_count)

    time.sleep(10)
    peer2.exit()
    peer3.exit()