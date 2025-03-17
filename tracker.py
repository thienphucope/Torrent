import http.server
import socketserver
import json
from urllib.parse import parse_qs, urlparse
import threading


class Tracker:
    def __init__(self, host="localhost", port=8080):
        """Input: host (str), port (int)
           Output: None"""
        pass

    def start_server(self):
        """Input: None
           Output: None"""
        pass

    def handle_registration(self, torrent_hash, peer_id, ip, port):
        """Input: torrent_hash (str), peer_id (str), ip (str), port (int)
           Output: None"""
        pass

    def handle_file_request(self, torrent_hash):
        """Input: torrent_hash (str)
           Output: list of tuples [(peer_id, (ip, port)), ...] hoặc None"""
        pass

    def handle_status_update(self, torrent_hash, peer_id, event):
        """Input: torrent_hash (str), peer_id (str), event (str)
           Output: None"""
        pass

class TrackerHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        """Input: None
           Output: None"""
        pass


if __name__ == "__main__":
    tracker = Tracker()
    tracker.start_server()