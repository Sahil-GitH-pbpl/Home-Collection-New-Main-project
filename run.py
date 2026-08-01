import os

from app import create_app
from app.extensions import socketio

app = create_app()

if __name__ == '__main__':
    socketio.run(
        app,
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "3000")),
        debug=os.getenv("APP_DEBUG", "1").strip().lower() in {"1", "true", "yes"},
        allow_unsafe_werkzeug=True,
    )
