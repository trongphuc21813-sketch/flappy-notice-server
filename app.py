import os
from flask import Flask, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
CORS(app, resources={r"/*": {"origins" : "*"}})

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

@app.route('/')
def home():
    return jsonify({"status": "success", "message": "Flappy Notice Server is Live!"}), 200

# Thêm hoặc giữ nguyên các sự kiện Socket.IO của bạn bên dưới
@socketio.on('connect')
def handle_connect():
    print('Client connected')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)
