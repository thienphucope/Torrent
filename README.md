# Small BitTorrent

1. Install missing packets
2. Remove all files in downloads{port} folder and seed
3. Run tracker.py
4. Run peer.py in another terminal

# Code này đang test nên chỉ chạy trên 1 máy diễn ra như sau

1. Seeder (6881) tạo piece ở trong folder seed 
2. Leecher 6882 download về downloads6882 (leecher là peer đang tải)
3. Leecher 6883 download về downloads6883

# Missing 

1. DHT 
2. UI (seed, download, cancel, status, ...) dùng thread để không làm gián đoạn mainflow
3. Download strategy "rarest first" + "tit for tat"
4. Chưa có cơ chế theo dõi piece + bitfield (dùng cho khi test 2 leecher tải cùng lúc https://youtu.be/6PWUCFmOQwQ?t=120 )
5. torrent_hash (file_hash) chỉ mới tạo từ tên file, cần tạo từ info
    info: Một dictionary chứa thông tin về file hoặc các file trong torrent, bao gồm:
        name: Tên của file hoặc thư mục.
        piece length: Kích thước của mỗi piece (thường là 256 KB, 512 KB, hoặc 1 MB).
        pieces: Danh sách các giá trị băm SHA-1 của từng piece.
        length: Tổng kích thước của file (đối với torrent đơn file).
        files: Danh sách các file (đối với torrent đa file).

# Xem sequence diagram để hiểu 

