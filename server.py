# Bắt buộc gọi monkey.patch_all() đầu tiên khi deploy lên Render sử dụng gevent
from gevent import monkey
monkey.patch_all()

from flask import Flask, request, jsonify
from flask_cors import CORS
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

CORS(app, resources={r"/*": {"origins": "*"}})

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

DATABASE = '/tmp/database.db'

# --- CẤU TRÚC DỮ LIỆU IN-MEMORY (RAM) ---
rooms = {}             # Quản lý phòng chơi
player_to_room = {}    # Ánh xạ: sid -> room_code
online_users = {}      # Ánh xạ: account_id -> sid
sid_to_user = {}       # Ánh xạ: sid -> account_id

MAX_PLAYERS = 4
AVAILABLE_COLORS = ['Red', 'Blue', 'Green', 'Pink']


# --- CƠ SỞ DỮ LIỆU SQLITE ---
def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            account_id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
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
    
    cursor.execute("SELECT name FROM sqlite_sequence WHERE name='users'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('users', 10000000)")
        
    conn.commit()
    conn.close()
    print("[+] Database initialized successfully.")

init_db()


# --- CÁC HÀM TIỆN ÍCH CHO GAME ---
def generate_room_code():
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        if code not in rooms:
            return code

def format_room_data(room_code):
    """Format dữ liệu phòng chuẩn theo cấu trúc HTML Client mong đợi"""
    if room_code not in rooms:
        return None
    r = rooms[room_code]
    players_list = []
    for sid, p in r['players'].items():
        players_list.append({
            'account_id': p['account_id'],
            'username': p['username'],
            'color': p['color'],
            'alive': p['alive']
        })
    return {
        'room_code': room_code,
        'host_id': r['host_id'],
        'status': r['status'],
        'players': players_list
    }

def check_game_over(room_code):
    if room_code not in rooms or rooms[room_code]['status'] != 'playing':
        return

    room = rooms[room_code]
    players = room['players']
    alive_players = {sid: p for sid, p in players.items() if p['alive']}
    total_players = len(players)

    if total_players >= 2:
        if len(alive_players) <= 1:
            room['status'] = 'waiting'
            winner_name = list(alive_players.values())[0]['username'] if len(alive_players) == 1 else "Không ai"
            socketio.emit('game_over', {
                'result': 'finished',
                'winner_name': winner_name
            }, to=room_code)
    elif total_players == 1:
        if len(alive_players) == 0:
            room['status'] = 'waiting'
            socketio.emit('game_over', {
                'result': 'lose',
                'message': 'Game Over!'
            }, to=room_code)


# --- REST API: AUTHENTICATION ---

@app.route('/api/register', methods=['POST', 'OPTIONS'])
def register():
    if request.method == 'OPTIONS':
        return '', 200

    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    
    if not username or not password:
        return jsonify({"status": "error", "message": "Thiếu username hoặc password"}), 400
        
    conn = get_db_connection()
    try:
        user = conn.execute('SELECT account_id FROM users WHERE username = ?', (username,)).fetchone()
        if user:
            return jsonify({"status": "error", "message": "Tên đăng nhập đã tồn tại!"}), 409
            
        hashed_pw = generate_password_hash(password)
        cursor = conn.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)', (username, hashed_pw))
        conn.commit()
        
        new_account_id = cursor.lastrowid
        return jsonify({"status": "success", "message": "Đăng ký thành công!", "account_id": new_account_id}), 201
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/login', methods=['POST', 'OPTIONS'])
def login():
    if request.method == 'OPTIONS':
        return '', 200

    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    conn.close()
    
    if user and check_password_hash(user['password_hash'], password):
        return jsonify({
            "status": "success",
            "message": "Đăng nhập thành công!",
            "account_id": user['account_id'],
            "username": user['username']
        }), 200
    return jsonify({"status": "error", "message": "Tài khoản hoặc mật khẩu không đúng!"}), 401


# --- SOCKETIO: QUẢN LÝ KẾT NỐI & BẠN BÈ ---

@socketio.on('authenticate')
def handle_authenticate(data):
    account_id = data.get('account_id')
    if account_id:
        account_id = int(account_id)
        sid = request.sid
        online_users[account_id] = sid
        sid_to_user[sid] = account_id
        print(f"[AUTH] User {account_id} connected with sid: {sid}")

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    print(f"[-] Client disconnected: {sid}")
    
    if sid in sid_to_user:
        account_id = sid_to_user.pop(sid)
        if account_id in online_users:
            del online_users[account_id]
            
    if sid in player_to_room:
        room_code = player_to_room[sid]
        if room_code in rooms:
            room = rooms[room_code]
            if sid in room['players']:
                del room['players'][sid]
            leave_room(room_code)
            del player_to_room[sid]
            
            if len(room['players']) == 0:
                print(f"[!] Room {room_code} deleted (Empty).")
                del rooms[room_code]
            else:
                # Cập nhật lại Host nếu chủ phòng thoát
                if room['host_id'] == account_id:
                    next_player = list(room['players'].values())[0]
                    room['host_id'] = next_player['account_id']
                
                socketio.emit('room_update', {'room': format_room_data(room_code)}, to=room_code)
                check_game_over(room_code)


@socketio.on('search_user')
def handle_search_user(data):
    """SỬA LỖI: Ưu tiên đọc target_id / search_id thay vì account_id người dùng"""
    target_id = data.get('target_id') or data.get('search_id')
    
    if not target_id:
        emit('search_result', {'status': 'error', 'message': 'Vui lòng cung cấp ID cần tìm!'})
        return

    try:
        target_id = int(target_id)
    except ValueError:
        emit('search_result', {'status': 'error', 'message': 'ID phải là định dạng số!'})
        return

    conn = get_db_connection()
    user = conn.execute('SELECT account_id, username FROM users WHERE account_id = ?', (target_id,)).fetchone()
    conn.close()
    
    if user:
        emit('search_result', {
            'status': 'success',
            'account_id': user['account_id'],
            'username': user['username']
        })
    else:
        emit('search_result', {
            'status': 'error',
            'message': f'Không tìm thấy ID người chơi: {target_id}'
        })

@socketio.on('send_friend_request')
def handle_send_friend_request(data):
    sender_sid = request.sid
    sender_id = sid_to_user.get(sender_sid) or data.get('account_id')
    target_id = data.get('target_id')
    
    if not sender_id or not target_id or int(sender_id) == int(target_id):
        emit('friend_request_status', {'status': 'error', 'message': 'Yêu cầu không hợp lệ.'})
        return

    sender_id = int(sender_id)
    target_id = int(target_id)
        
    conn = get_db_connection()
    existing = conn.execute('''
        SELECT status FROM friends 
        WHERE (user_id_1 = ? AND user_id_2 = ?) OR (user_id_1 = ? AND user_id_2 = ?)
    ''', (sender_id, target_id, target_id, sender_id)).fetchone()
    
    if existing:
        conn.close()
        msg = "Đã là bạn bè!" if existing['status'] == 'accepted' else "Đã gửi lời mời trước đó!"
        emit('friend_request_status', {'status': 'error', 'message': msg})
        return
        
    conn.execute('INSERT INTO friends (user_id_1, user_id_2, status) VALUES (?, ?, ?)', (sender_id, target_id, 'pending'))
    sender = conn.execute('SELECT username FROM users WHERE account_id = ?', (sender_id,)).fetchone()
    conn.commit()
    conn.close()
    
    emit('friend_request_status', {'status': 'success', 'message': 'Đã gửi lời mời kết bạn!'})
    
    if target_id in online_users:
        target_sid = online_users[target_id]
        emit('new_friend_request', {'from_account_id': sender_id, 'from_username': sender['username']}, to=target_sid)


@socketio.on('accept_friend_request')
def handle_accept_friend_request(data):
    receiver_sid = request.sid
    receiver_id = sid_to_user.get(receiver_sid) or data.get('account_id')
    sender_id = data.get('target_id') or data.get('sender_id')
    
    if not receiver_id or not sender_id:
        return

    receiver_id = int(receiver_id)
    sender_id = int(sender_id)
        
    conn = get_db_connection()
    conn.execute('''
        UPDATE friends SET status = 'accepted' 
        WHERE user_id_1 = ? AND user_id_2 = ? AND status = 'pending'
    ''', (sender_id, receiver_id))
    
    receiver = conn.execute('SELECT username FROM users WHERE account_id = ?', (receiver_id,)).fetchone()
    conn.commit()
    conn.close()
    
    emit('friend_request_status', {'status': 'success', 'message': 'Đã chấp nhận lời mời kết bạn!'})
    
    # Reload lại danh sách bạn bè cho cả 2 người
    socketio.emit('friends_list_data', get_friends_data_for_user(receiver_id), to=receiver_sid)
    if sender_id in online_users:
        socketio.emit('friends_list_data', get_friends_data_for_user(sender_id), to=online_users[sender_id])

def get_friends_data_for_user(user_id):
    conn = get_db_connection()
    # Tìm bạn bè chính thức
    friends_db = conn.execute('''
        SELECT u.account_id, u.username 
        FROM users u 
        JOIN friends f ON (u.account_id = f.user_id_1 OR u.account_id = f.user_id_2)
        WHERE (f.user_id_1 = ? OR f.user_id_2 = ?) 
        AND u.account_id != ? AND f.status = 'accepted'
    ''', (user_id, user_id, user_id)).fetchall()

    # Tìm lời mời đang chờ
    requests_db = conn.execute('''
        SELECT u.account_id, u.username 
        FROM users u 
        JOIN friends f ON u.account_id = f.user_id_1
        WHERE f.user_id_2 = ? AND f.status = 'pending'
    ''', (user_id,)).fetchall()
    
    conn.close()

    friends_list = []
    for f in friends_db:
        friends_list.append({
            'account_id': f['account_id'],
            'username': f['username'],
            'online': f['account_id'] in online_users
        })

    requests_list = []
    for r in requests_db:
        requests_list.append({
            'account_id': r['account_id'],
            'username': r['username']
        })

    return {'friends': friends_list, 'requests': requests_list}

@socketio.on('get_friends_list')
def handle_get_friends_list(data=None):
    sid = request.sid
    user_id = sid_to_user.get(sid)
    if not user_id and data:
        user_id = data.get('account_id')
    
    if not user_id:
        return
        
    emit('friends_list_data', get_friends_data_for_user(int(user_id)))


# --- SOCKETIO: QUẢN LÝ PHÒNG & GAMEPLAY ---

@socketio.on('create_room')
def on_create_room(data):
    sid = request.sid
    account_id = data.get('account_id') or sid_to_user.get(sid, 9999)
    username = data.get('username', 'Player')
    account_id = int(account_id)

    room_code = generate_room_code()
    
    rooms[room_code] = {
        'host_id': account_id,
        'status': 'waiting',
        'pipe_seed': None,
        'players': {
            sid: {'account_id': account_id, 'username': username, 'color': AVAILABLE_COLORS[0], 'alive': False}
        }
    }
    
    player_to_room[sid] = room_code
    join_room(room_code)
    print(f"[*] Room created: #{room_code} by {username} ({account_id})")
    
    emit('room_created', {'room': format_room_data(room_code)})

@socketio.on('join_room')
def on_join_room(data):
    sid = request.sid
    room_code = data.get('room_code', '').upper().strip()
    username = data.get('username', 'Player')
    account_id = data.get('account_id') or sid_to_user.get(sid, 9999)
    account_id = int(account_id)
    
    if room_code not in rooms:
        emit('search_result', {'status': 'error', 'message': 'Không tìm thấy phòng chơi này!'})
        return
    room = rooms[room_code]
    if len(room['players']) >= MAX_PLAYERS:
        emit('search_result', {'status': 'error', 'message': 'Phòng đã đầy (Tối đa 4 người)!'})
        return
    if room['status'] == 'playing':
        emit('search_result', {'status': 'error', 'message': 'Trận đấu đang diễn ra!'})
        return
        
    used_colors = [p['color'] for p in room['players'].values()]
    assigned_color = next((c for c in AVAILABLE_COLORS if c not in used_colors), AVAILABLE_COLORS[0])

    room['players'][sid] = {
        'account_id': account_id, 'username': username, 'color': assigned_color, 'alive': False
    }
    player_to_room[sid] = room_code
    join_room(room_code)
    
    print(f"[*] {username} joined room: #{room_code}")
    emit('room_joined', {'room': format_room_data(room_code)})
    emit('room_update', {'room': format_room_data(room_code)}, to=room_code)

@socketio.on('invite_to_room')
def on_invite_to_room(data):
    sid = request.sid
    room_code = data.get('room_code')
    target_id = data.get('target_id')
    from_id = data.get('from_account_id') or sid_to_user.get(sid)
    
    if target_id and int(target_id) in online_users:
        conn = get_db_connection()
        sender = conn.execute('SELECT username FROM users WHERE account_id = ?', (from_id,)).fetchone()
        conn.close()
        
        sender_name = sender['username'] if sender else "Bạn bè"
        target_sid = online_users[int(target_id)]
        
        emit('room_invite_received', {
            'room_code': room_code, 
            'from_username': sender_name
        }, to=target_sid)

@socketio.on('leave_room')
def on_leave_room(data):
    sid = request.sid
    if sid in player_to_room:
        room_code = player_to_room[sid]
        leave_room(room_code)
        del player_to_room[sid]
        if room_code in rooms:
            if sid in rooms[room_code]['players']:
                del rooms[room_code]['players'][sid]
            if len(rooms[room_code]['players']) == 0:
                del rooms[room_code]
            else:
                emit('room_update', {'room': format_room_data(room_code)}, to=room_code)

@socketio.on('start_game')
def on_start_game(data):
    sid = request.sid
    room_code = data.get('room_code') or player_to_room.get(sid)
    
    if not room_code or room_code not in rooms: return
    room = rooms[room_code]
        
    pipe_seed = random.uniform(0.1, 0.99)
    room['pipe_seed'] = pipe_seed
    room['status'] = 'playing'
    
    for player_sid in room['players']:
        room['players'][player_sid]['alive'] = True
        
    emit('game_started', {'pipe_seed': pipe_seed}, to=room_code)

@socketio.on('update_position')
def on_update_position(data):
    sid = request.sid
    room_code = data.get('room_code') or player_to_room.get(sid)
    if room_code and room_code in rooms:
        emit('sync_position', {
            'account_id': data.get('account_id'),
            'x': data.get('x'),
            'y': data.get('y'),
            'angle': data.get('angle'),
            'isDead': data.get('isDead', False)
        }, to=room_code, include_self=False)

@socketio.on('player_died')
def on_player_died(data):
    sid = request.sid
    room_code = data.get('room_code') or player_to_room.get(sid)
    account_id = data.get('account_id')

    if room_code and room_code in rooms:
        if sid in rooms[room_code]['players']:
            rooms[room_code]['players'][sid]['alive'] = False
        emit('sync_player_died', {'account_id': account_id}, to=room_code, include_self=False)
        check_game_over(room_code)

# API Test Server
@app.route('/')
def index():
    return jsonify({"status": "Flappy Bird Modded by Phuc Trong is Online", "active_rooms": len(rooms), "online_users": len(online_users)})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)
