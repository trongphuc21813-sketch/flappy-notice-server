from flask import Flask, request, jsonify
from flask_cors import CORS
import time

app = Flask(__name__)
CORS(app)  # Cho phép mọi trang web/game kết nối tới

notice_data = {
    "system_msg": "",
    "notice_time": 0
}

@app.route('/api/get_notice', methods=['GET'])
def get_notice():
    return jsonify({
        "status": "success",
        "system_msg": notice_data["system_msg"],
        "notice_time": notice_data["notice_time"]
    })

@app.route('/api/set_notice', methods=['POST'])
def set_notice():
    data = request.get_json() or {}
    msg = data.get("msg", "")
    notice_data["system_msg"] = msg
    notice_data["notice_time"] = int(time.time())
    return jsonify({"status": "success", "msg": msg})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
