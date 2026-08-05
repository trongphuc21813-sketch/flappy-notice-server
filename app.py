import os
from flask import Flask, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
CORS(app, resources={r"/*": {"origins": "*"}})

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

@app.route('/')
def home():
    return jsonify({"status": "online", "message": "Server Flappy Notice đang hoạt động"}), 200

@socketio.on('connect')
def handle_connect(data=None):
    print('Client connected')

@socketio.on('get_friends_list')
def handle_get_friends_list(data=None):
    pass

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    socketio.run(app, host='0.0.0.0', port=port)
