# Bắt buộc gọi monkey_patch() đầu tiên khi deploy lên Render sử dụng eventlet
import eventlet
eventlet.monkey_patch()

from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import random
import string
import time
import os

# --- CẤU HÌNH FLASK & SOCKETIO ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'flappy_bird_secret_key_2026_senior_dev'
app.config['JSON_AS_ASCII'] = False  # Hỗ trợ hiển thị tiếng Việt chuẩn UTF-8

# Khởi tạo SocketIO với eventlet, cho phép mọi nguồn kết nối (CORS)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

DATABASE = 'database.db'

# --- CẤU TRÚC DỮ LIỆU IN-MEMORY (RAM) ---
rooms = {}             # Quản lý phòng chơi: { "ROOM_ID": { "status", "pipe_seed", "players": { sid: {...} } } }
player_to_room = {}    # Ánh xạ: sid -> room_id (Tìm nhanh phòng khi user disconnect)
online_users = {}      # Ánh xạ: account_id -> sid (Để gửi thông báo kết bạn realtime)
sid_to_user = {}       # Ánh xạ: sid -> account_id

MAX_PLAYERS = 4
AVAILABLE_COLORS = ['Red', 'Blue', 'Green', 'Pink']


# --- CƠ SỞ DỮ LIỆU SQLITE ---
def get_db_connection():
    """Tạo kết nối tới SQLite và trả về dữ liệu dạng Dictionary"""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Khởi tạo các bảng dữ liệu nếu chưa tồn tại"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Tạo bảng users
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            account_id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Tạo bảng friends
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS friends (
            user_id_1 INTEGER,
            user_id_2 INTEGER,
            status TEXT CHECK( status IN ('pending', 'accepted') ) NOT NULL DEFAULT 'pending',
            PRIMARY KEY (user_id_1, user_id_2),
            FOREIGN KEY (user_id_1) REFERENCES users(account_id),
            FOREIGN KEY (user_id_2) REFERENCES users(account_id)
        )
    ''')
    
    # Ép ID bắt đầu từ 10000001 (Nếu bảng sqlite_sequence chưa có dữ liệu của bảng users)
    cursor.execute("SELECT name FROM sqlite_sequence WHERE name='users'")
    if not cursor.fetchone():
        # ID bắt đầu từ 10000000, bản ghi đầu tiên insert vào sẽ được +1 thành 10000001
        cursor.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('users', 10000000)")
        
    conn.commit()
    conn.close()
    print("[+] Database initialized successfully.")

# Gọi hàm khởi tạo DB ngay khi chạy app
init_db()


# --- CÁC HÀM TIỆN ÍCH CHO GAME ---
def generate_room_code():
    """Tạo mã phòng ngẫu nhiên 4 ký tự (Chữ in hoa và Số)"""
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        if code not in rooms:
            return code

def get_available_color(room_id):
    """Tìm màu chim chưa được sử dụng trong phòng"""
    if room_id not in rooms:
        return AVAILABLE_COLORS[0]
    used_colors = [player['color'] for player in rooms[room_id]['players'].values()]
    for color in AVAILABLE_COLORS:
        if color not in used_colors:
            return color
    return AVAILABLE_COLORS[0]

def check_game_over(room_id):
    """Trọng tài: Kiểm tra điều kiện thắng/thua của phòng (Battle Royale)"""
    if room_id not in rooms or rooms[room_id]['status'] != 'playing':
        return

    room = rooms[room_id]
    players = room['players']
    alive_players = {sid: p for sid, p in players.items() if p['alive']}
    total_players = len(players)

    if total_players >= 2:
        if len(alive_players) == 1:
            # Còn 1 người duy nhất sống sót -> Thắng
            winner_sid = list(alive_players.keys())[0]
            winner_name = alive_players[winner_sid]['username']
            room['status'] = 'waiting'
            socketio.emit('game_over', {
                'result': 'winner',
                'winner_name': winner_name,
                'message': f'Chúc mừng {winner_name} đã sống sót cuối cùng!'
            }, to=room_id)
            
        elif len(alive_players) == 0:
            # Tất cả cùng chết -> Hòa
            room['status'] = 'waiting'
            socketio.emit('game_over', {
                'result': 'tie',
                'message': 'Tất cả đều đã chết. Kết quả HÒA!'
            }, to=room_id)
            
    elif total_players == 1:
        if len(alive_players) == 0:
            room['status'] = 'waiting'
            socketio.emit('game_over', {
                'result': 'lose',
                'message': 'Game Over!'
            }, to=room_id)


# --- REST API: AUTHENTICATION ---

@app.route('/api/register', methods=['POST'])
def register():
    """API Đăng ký tài khoản"""
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    
    if not username or not password:
        return jsonify({"error": "Thiếu username hoặc password"}), 400
        
    conn = get_db_connection()
    try:
        # Kiểm tra username tồn tại
        user = conn.execute('SELECT account_id FROM users WHERE username = ?', (username,)).fetchone()
        if user:
            return jsonify({"error": "Tên đăng nhập đã tồn tại!"}), 409
            
        # Hash password và lưu vào DB (ID tự động tăng từ 10000001)
        hashed_pw = generate_password_hash(password)
        cursor = conn.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)', (username, hashed_pw))
        conn.commit()
        
        new_account_id = cursor.lastrowid
        return jsonify({"message": "Đăng ký thành công!", "account_id": new_account_id}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/login', methods=['POST'])
def login():
    """API Đăng nhập"""
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    conn.close()
    
    if user and check_password_hash(user['password_hash'], password):
        return jsonify({
            "message": "Đăng nhập thành công!",
            "account_id": user['account_id'],
            "username": user['username']
        }), 200
    return jsonify({"error": "Tài khoản hoặc mật khẩu không đúng!"}), 401


# --- SOCKETIO: QUẢN LÝ KẾT NỐI & BẠN BÈ ---

@socketio.on('authenticate')
def handle_authenticate(data):
    """Client gửi Account ID lên sau khi đăng nhập thành công để nhận thông báo Real-time"""
    account_id = data.get('account_id')
    if account_id:
        sid = request.sid
        online_users[account_id] = sid
        sid_to_user[sid] = account_id
        print(f"[AUTH] User {account_id} connected with sid: {sid}")

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    print(f"[-] Client disconnected: {sid}")
    
    # Xóa khỏi danh sách online
    if sid in sid_to_user:
        account_id = sid_to_user.pop(sid)
        if account_id in online_users:
            del online_users[account_id]
            
    # Xử lý nếu người dùng đang trong phòng game
    if sid in player_to_room:
        room_id = player_to_room[sid]
        if room_id in rooms:
            player_name = rooms[room_id]['players'][sid]['username']
            rooms[room_id]['players'][sid]['alive'] = False 
            del rooms[room_id]['players'][sid]
            leave_room(room_id)
            del player_to_room[sid]
            
            emit('player_left', {'sid': sid, 'username': player_name}, to=room_id)
            
            if len(rooms[room_id]['players']) == 0:
                print(f"[!] Room {room_id} deleted (Empty).")
                del rooms[room_id]
            else:
                # Chuyển quyền host nếu host thoát
                if not any(p['is_host'] for p in rooms[room_id]['players'].values()):
                    new_host_sid = list(rooms[room_id]['players'].keys())[0]
                    rooms[room_id]['players'][new_host_sid]['is_host'] = True
                    emit('room_update', {'players': rooms[room_id]['players']}, to=room_id)
                check_game_over(room_id)


@socketio.on('search_user')
def handle_search_user(data):
    """Tìm kiếm người dùng theo ID (vd: 10000001)"""
    target_id = data.get('account_id')
    conn = get_db_connection()
    user = conn.execute('SELECT account_id, username FROM users WHERE account_id = ?', (target_id,)).fetchone()
    conn.close()
    
    if user:
        emit('search_result', {'status': 'success', 'account_id': user['account_id'], 'username': user['username']})
    else:
        emit('search_result', {'status': 'error', 'message': 'Không tìm thấy ID người chơi này!'})

@socketio.on('send_friend_request')
def handle_send_friend_request(data):
    """Gửi lời mời kết bạn"""
    sender_sid = request.sid
    sender_id = sid_to_user.get(sender_sid)
    target_id = data.get('target_id')
    
    if not sender_id or not target_id or sender_id == target_id:
        emit('friend_request_status', {'status': 'error', 'message': 'Yêu cầu không hợp lệ.'})
        return
        
    conn = get_db_connection()
    # Kiểm tra xem đã kết bạn hoặc đã gửi lời mời chưa (Cả 2 chiều)
    existing = conn.execute('''
        SELECT status FROM friends 
        WHERE (user_id_1 = ? AND user_id_2 = ?) OR (user_id_1 = ? AND user_id_2 = ?)
    ''', (sender_id, target_id, target_id, sender_id)).fetchone()
    
    if existing:
        conn.close()
        msg = "Đã là bạn bè!" if existing['status'] == 'accepted' else "Đã gửi lời mời trước đó!"
        emit('friend_request_status', {'status': 'error', 'message': msg})
        return
        
    # Ghi vào DB
    conn.execute('INSERT INTO friends (user_id_1, user_id_2, status) VALUES (?, ?, ?)', (sender_id, target_id, 'pending'))
    
    # Lấy thông tin người gửi để báo cho người nhận
    sender = conn.execute('SELECT username FROM users WHERE account_id = ?', (sender_id,)).fetchone()
    conn.commit()
    conn.close()
    
    emit('friend_request_status', {'status': 'success', 'message': 'Đã gửi lời mời kết bạn!'})
    
    # Real-time notification cho người nhận nếu họ đang Online
    if target_id in online_users:
        target_sid = online_users[target_id]
        emit('new_friend_request', {'sender_id': sender_id, 'sender_name': sender['username']}, to=target_sid)


@socketio.on('accept_friend_request')
def handle_accept_friend_request(data):
    """Chấp nhận lời mời kết bạn"""
    receiver_sid = request.sid
    receiver_id = sid_to_user.get(receiver_sid)
    sender_id = data.get('sender_id')
    
    if not receiver_id or not sender_id:
        return
        
    conn = get_db_connection()
    conn.execute('''
        UPDATE friends SET status = 'accepted' 
        WHERE user_id_1 = ? AND user_id_2 = ? AND status = 'pending'
    ''', (sender_id, receiver_id))
    
    receiver = conn.execute('SELECT username FROM users WHERE account_id = ?', (receiver_id,)).fetchone()
    conn.commit()
    conn.close()
    
    emit('friend_accept_status', {'status': 'success', 'message': 'Đã thêm bạn thành công!'})
    
    # Báo cho người gửi biết lời mời đã được chấp nhận (nếu online)
    if sender_id in online_users:
        emit('friend_request_accepted', {'accepter_id': receiver_id, 'accepter_name': receiver['username']}, to=online_users[sender_id])


@socketio.on('get_friends_list')
def handle_get_friends_list():
    """Lấy danh sách bạn bè đã kết bạn thành công"""
    sid = request.sid
    user_id = sid_to_user.get(sid)
    if not user_id:
        return
        
    conn = get_db_connection()
    # Tìm tất cả bạn bè ở trạng thái 'accepted' (Bất kể ai là user_id_1 hay user_id_2)
    friends = conn.execute('''
        SELECT u.account_id, u.username 
        FROM users u 
        JOIN friends f ON (u.account_id = f.user_id_1 OR u.account_id = f.user_id_2)
        WHERE (f.user_id_1 = ? OR f.user_id_2 = ?) 
        AND u.account_id != ? AND f.status = 'accepted'
    ''', (user_id, user_id, user_id)).fetchall()
    conn.close()
    
    friends_list = []
    for f in friends:
        # Kiểm tra online status
        is_online = f['account_id'] in online_users
        friends_list.append({
            'account_id': f['account_id'],
            'username': f['username'],
            'online': is_online
        })
        
    emit('friends_list_data', {'friends': friends_list})


# --- SOCKETIO: QUẢN LÝ PHÒNG & GAMEPLAY ---

@socketio.on('create_room')
def on_create_room(data):
    sid = request.sid
    account_id = sid_to_user.get(sid, 'Guest')
    username = data.get('username', 'Player')
    
    room_id = generate_room_code()
    color = AVAILABLE_COLORS[0]
    
    rooms[room_id] = {
        'status': 'waiting',
        'pipe_seed': None,
        'players': {
            sid: {'account_id': account_id, 'username': username, 'color': color, 'alive': False, 'is_host': True}
        }
    }
    
    player_to_room[sid] = room_id
    join_room(room_id)
    print(f"[*] Room created: {room_id} by {username}")
    
    emit('room_created', {'room_id': room_id, 'sid': sid, 'color': color, 'is_host': True, 'players': rooms[room_id]['players']})

@socketio.on('join_room')
def on_join_room(data):
    sid = request.sid
    room_id = data.get('room_id', '').upper().strip()
    username = data.get('username', 'Player')
    account_id = sid_to_user.get(sid, 'Guest')
    
    if room_id not in rooms:
        emit('error', {'message': 'Không tìm thấy phòng chơi này!'})
        return
    room = rooms[room_id]
    if len(room['players']) >= MAX_PLAYERS:
        emit('error', {'message': 'Phòng đã đầy (Tối đa 4 người)!'})
        return
    if room['status'] == 'playing':
        emit('error', {'message': 'Trận đấu đang diễn ra, không thể vào lúc này!'})
        return
        
    color = get_available_color(room_id)
    room['players'][sid] = {
        'account_id': account_id, 'username': username, 'color': color, 'alive': False, 'is_host': False
    }
    player_to_room[sid] = room_id
    join_room(room_id)
    
    print(f"[*] {username} joined room: {room_id}")
    emit('room_joined', {'room_id': room_id, 'sid': sid, 'color': color, 'is_host': False, 'players': room['players']})
    emit('room_update', {'players': room['players']}, to=room_id)

@socketio.on('invite_friend')
def on_invite_friend(data):
    """Mời bạn bè vào phòng hiện tại"""
    sid = request.sid
    target_id = data.get('target_id')
    room_id = player_to_room.get(sid)
    
    if room_id and target_id in online_users:
        sender_name = rooms[room_id]['players'][sid]['username']
        target_sid = online_users[target_id]
        emit('room_invite_received', {'room_id': room_id, 'sender_name': sender_name}, to=target_sid)


@socketio.on('start_game')
def on_start_game():
    sid = request.sid
    room_id = player_to_room.get(sid)
    
    if not room_id or room_id not in rooms: return
    room = rooms[room_id]
    if not room['players'][sid]['is_host']: return
        
    # Tạo mã seed chung để sinh ống nước đồng bộ trên tất cả máy tính
    pipe_seed = random.randint(10000, 99999)
    room['pipe_seed'] = pipe_seed
    room['status'] = 'playing'
    
    for player_sid in room['players']:
        room['players'][player_sid]['alive'] = True
        
    emit('game_started', {'pipe_seed': pipe_seed, 'players': room['players']}, to=room_id)

@socketio.on('update_position')
def on_update_position(data):
    """Phát tọa độ (y, góc xoay) liên tục cho những người cùng phòng"""
    sid = request.sid
    room_id = player_to_room.get(sid)
    if room_id and room_id in rooms and rooms[room_id]['status'] == 'playing':
        emit('sync_position', {
            'sid': sid,
            'y': data.get('y', 0),
            'rotation': data.get('rotation', 0)
        }, to=room_id, include_self=False)

@socketio.on('player_died')
def on_player_died():
    """Ghi nhận người chơi chết và check điều kiện thắng"""
    sid = request.sid
    room_id = player_to_room.get(sid)
    if room_id and room_id in rooms and rooms[room_id]['status'] == 'playing':
        rooms[room_id]['players'][sid]['alive'] = False
        emit('sync_player_died', {'sid': sid}, to=room_id, include_self=False)
        check_game_over(room_id)

# API Test Server
@app.route('/')
def index():
    return jsonify({"status": "Flappy Bird Backend is Online", "active_rooms": len(rooms), "online_users": len(online_users)})

if __name__ == '__main__':
    # Chạy trên Cổng 5000 - Render sẽ sử dụng Gunicorn với tham số -k eventlet trong config thực tế.
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
