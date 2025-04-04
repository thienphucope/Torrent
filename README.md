# 🌊 Simple Torrent-like Application

A lightweight BitTorrent-inspired peer-to-peer file sharing system written in Python. The system enables decentralized file distribution using a central tracker for coordination, and peers that share and download files directly with one another.

---

## 🚀 Quick Start

### 1. Clone this repository

```bash
git clone https://github.com/thienphucope/Torrent.git
cd Torrent
```

### 2. Start the Tracker

```bash
python tracker.py
```

- Listens for UDP broadcast on port `8001`
- Serves HTTP API on port `8000`

> The tracker helps peers find each other but **does not store any file**.

### 3. Start a Peer

```bash
python peer.py port
```

Or run both using VSCode Tasks:

```bash
Ctrl + Shift + P → Run Task → Run All Scripts
```

---

## 🧠 How It Works

- Peers **broadcast** to discover the tracker (no manual IP input)
- You can **share files** (multi-file supported)
- Other peers can **discover** torrents or use **magnet links**
- Download is handled via **parallel piece-based transfer**
- Piece availability is exchanged using **bitfield** messages
- **Rarest-first strategy** used for piece selection
- Peers can **pause/resume**, and will **auto-share** what they download

---

## 🧪 Usage Guide

### 📤 Sharing

- On starting a peer, enter paths to files/folders you want to share (separated by commas)
- Metadata and file pieces will be created and registered with the tracker

### 📥 Downloading

- Use the **"Discover"** button to view available torrents
- Click **Download** or paste a **magnet link** (`magnet:<torrent_hash>`) to start downloading
- Download continues from multiple peers simultaneously (if available)

---

## ⚙️ Features

- ✅ **Multi-file torrents**
- 🔁 **Parallel download & upload**
- 🔗 **Magnet link support**
- 📡 **Tracker discovery via UDP broadcast**
- 📈 **Status bar with segmented progress**
- 📦 **Resume/pause downloads** from where left off
- 🌍 **Dynamic IP/Port support** — peers work across different networks
- 🧠 **Rarest-first strategy** to optimize distribution
- 🧩 **Piece tracking** and syncing with other peers
- 🔄 **Free to join/leave** the swarm anytime
- 🧪 **Discover tracker (tracker grape)** and available torrents
- 👀 **View online peers** and what they’re sharing
- 🧼 **Auto cleanup** of temp files when exiting
- 📊 **Peer selection** based on latency and availability

- 💾 **Persistent piece storage** across restarts

---

## 📂 Project Structure

```
.
├── tracker.py            # Tracker (UDP + HTTP server)
├── peer.py               # 
└── README.md             # You are here!
```

---

## 💡 Example Workflow

1. Peer A shares a file or folder.
2. Peer B starts and discovers the tracker.
3. Peer B fetches torrent metadata and starts downloading.
4. If multiple peers exist, download is parallelized.
5. Peers automatically share what they have downloaded.

---

## 🧼 Functional Checklist

- [x] Peer discovery via broadcast
- [x] File sharing and seeding
- [x] Parallel download from multiple peers
- [x] Download using magnet text
- [x] Multi-file torrent support
- [x] Resume and pause download
- [x] Status bar showing per-piece progress
- [x] Tracker shows online peers and their files
- [x] Piece-level download with storage
- [x] Fetch available torrents (discover)
- [x] Graceful exit with cleanup
- [x] Peer selection based on latency
- [x] Support running across multiple machines/networks
- [x] Automatically share downloaded files (leecher becomes seeder)
- [x] Display peer list per torrent


## 📜 License

MIT License.  
Made by [@thienphucope](https://github.com/thienphucope)

Feel free to fork, extend, or contribute!
