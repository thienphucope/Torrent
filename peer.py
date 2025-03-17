import socket
import threading
import requests
import hashlib
import os
import time
from urllib.parse import urlencode


class Peer:
    def __init__(self, tracker_url="http://localhost:8080", port=6881):
        """Input: tracker_url (str), port (int)
           Output: None"""
        pass

    def start(self):
        """Input: None
           Output: None"""
        pass

    def register_with_tracker(self, torrent_hash, event="started"):
        """Input: torrent_hash (str), event (str)
           Output: list of peers [(peer_id, (ip, port)), ...]"""
        pass

    def get_peers_from_tracker(self, torrent_hash):
        """Input: torrent_hash (str)
           Output: list of peers [(peer_id, (ip, port)), ...]"""
        pass

    def periodic_tracker_announce(self, torrent_hash):
        """Input: torrent_hash (str)
           Output: None"""
        pass

    def download(self, torrent_hash, file_path, piece_count):
        """Input: torrent_hash (str), file_path (str), piece_count (int)
           Output: None"""
        pass

    def seed(self, file_path):
        """Input: file_path (str)
           Output: None"""
        pass

    def split_file(self, file_path):
        """Input: file_path (str)
           Output: list of byte strings"""
        pass

    def handshake(self, sock, torrent_hash):
        """Input: sock (socket), torrent_hash (str)
           Output: None"""
        pass

    def download_from_peer(self, peer, torrent_hash):
        """Input: peer (dict with ip and port), torrent_hash (str)
           Output: None"""
        pass

    def assemble_file(self, torrent_hash):
        """Input: torrent_hash (str)
           Output: None"""
        pass

    def listen_for_peers(self):
        """Input: None
           Output: None"""
        pass

    def handle_peer_request(self, client_sock, addr):
        """Input: client_sock (socket), addr (tuple)
           Output: None"""
        pass

    def status(self):
        """Input: None
           Output: None"""
        pass

    def run_ui(self):
        """Input: None
           Output: None"""
        pass

    def calculate_piece_hash(self, piece):
        """Input: piece (bytes)
           Output: str"""
        pass

    def verify_piece(self, piece, expected_hash):
        """Input: piece (bytes), expected_hash (str)
           Output: bool"""
        pass


if __name__ == "__main__":
    peer = Peer()
    peer.start()