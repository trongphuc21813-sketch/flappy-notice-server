from flask import Flask, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import string

app = Flask(__name__)
app.config['SECRET_KEY'] = 'flappy_secret_key'
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Cấu trúc dữ liệu lưu phòng chơi:
# rooms = {
#    "ROOM_ID": {
#        "players": { socket_id: {"id": socket_id, "name": "Player 1", "y": 200, "alive": True, "color": "#ff0000"} },
#        "game_started": False,
#        "pipe_seed": 12345
#    }
# }
rooms = {}

def generate_room_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))

@socketio.on('connect')
def handle_connect():
    print(f"Client kết nối: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    for room_id, room_data in list(rooms.items()):
        if sid in room_data['players']:
            del room_data['players'][sid]
            emit('player_left', {'sid': sid}, to=room_id)
            
            # Kiểm tra số người còn lại nếu game đang chạy
            check_game_over(room_id)
            
            # Xóa phòng nếu trống
            if len(room_data['players']) == 0:
                del rooms[room_id]
            break

@socketio.on('create_room')
def handle_create_room(data):
    sid = request.sid
    room_id = generate_room_code()
    player_name = data.get('name', 'Player 1')
    
    colors = ['#ff5722', '#2196f3', '#4caf50', '#e91e63']
    
    rooms[room_id] = {
        'players': {
            sid: {
                'id': sid,
                'name': player_name,
                'y': 250,
                'alive': True,
                'color': colors[0]
            }
        },
        'game_started': False,
        'pipe_seed': random.randint(1000, 9999)
    }
    
    join_room(room_id)
    emit('room_created', {
        'room_id': room_id,
        'players': list(rooms[room_id]['players'].values()),
        'my_id': sid
    })

@socketio.on('join_room')
def handle_join_room(data):
    sid = request.sid
    room_id = data.get('room_id', '').upper()
    player_name = data.get('name', 'Player')
    
    if room_id not in rooms:
        emit('error_msg', {'message': 'Phòng không tồn tại!'})
        return
        
    room = rooms[room_id]
    
    if len(room['players']) >= 4:
        emit('error_msg', {'message': 'Phòng đã đầy (Tối đa 4 người)!'})
        return
        
    if room['game_started']:
        emit('error_msg', {'message': 'Trận đấu đang diễn ra!'})
        return
        
    colors = ['#ff5722', '#2196f3', '#4caf50', '#e91e63']
    used_colors = [p['color'] for p in room['players'].values()]
    available_colors = [c for c in colors if c not in used_colors]
    
    room['players'][sid] = {
        'id': sid,
        'name': player_name,
        'y': 250,
        'alive': True,
        'color': available_colors[0] if available_colors else '#ffffff'
    }
    
    join_room(room_id)
    
    # Thông báo cho người mới vào
    emit('room_joined', {
        'room_id': room_id,
        'players': list(room['players'].values()),
        'my_id': sid
    })
    
    # Thông báo cho những người còn lại trong phòng
    emit('player_joined', {
        'players': list(room['players'].values())
    }, to=room_id)

@socketio.on('start_game')
def handle_start_game(data):
    room_id = data.get('room_id')
    if room_id in rooms:
        rooms[room_id]['game_started'] = True
        rooms[room_id]['pipe_seed'] = random.randint(1000, 9999)
        
        # Reset trạng thái sống/chết của mọi người
        for p in rooms[room_id]['players'].values():
            p['alive'] = True
            p['y'] = 250
            
        emit('game_started', {
            'seed': rooms[room_id]['pipe_seed'],
            'players': list(rooms[room_id]['players'].values())
        }, to=room_id)

@socketio.on('update_position')
def handle_update_position(data):
    room_id = data.get('room_id')
    y = data.get('y')
    sid = request.sid
    
    if room_id in rooms and sid in rooms[room_id]['players']:
        rooms[room_id]['players'][sid]['y'] = y
        # Phát lại tọa độ cho mọi người trong phòng
        emit('player_moved', {
            'id': sid,
            'y': y
        }, to=room_id, include_self=False)

@socketio.on('player_died')
def handle_player_died(data):
    room_id = data.get('room_id')
    sid = request.sid
    
    if room_id in rooms and sid in rooms[room_id]['players']:
        rooms[room_id]['players'][sid]['alive'] = False
        emit('player_status_change', {
            'id': sid,
            'alive': False
        }, to=room_id)
        
        check_game_over(room_id)

def check_game_over(room_id):
    if room_id not in rooms:
        return
        
    room = rooms[room_id]
    if not room['game_started']:
        return
        
    alive_players = [p for p in room['players'].values() if p['alive']]
    total_players = len(room['players'])
    
    # Nếu nhiều hơn 1 người chơi và chỉ còn 1 người sống => Người đó thắng!
    if total_players > 1 and len(alive_players) == 1:
        winner = alive_players[0]
        room['game_started'] = False
        emit('game_over', {
            'winner_id': winner['id'],
            'winner_name': winner['name']
        }, to=room_id)
    # Nếu tất cả đều chết (chơi 1 mình hoặc đâm va cùng lúc)
    elif len(alive_players) == 0:
        room['game_started'] = False
        emit('game_over', {
            'winner_id': None,
            'winner_name': 'Không ai cả'
        }, to=room_id)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)
