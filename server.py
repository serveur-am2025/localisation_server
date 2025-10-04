from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_cors import CORS
import logging
import datetime
import os

# Configuration
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'votre_cle_secrete_lampadaire_2024')

# ✅ CORS amélioré pour Android
CORS(app, resources={
    r"/*": {
        "origins": "*",
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

# ✅ Configuration SocketIO optimisée pour Render + Android
socketio = SocketIO(
    app, 
    cors_allowed_origins="*",
    async_mode='threading',
    logger=True,
    engineio_logger=True,
    ping_timeout=120,  # Augmenté pour connexions lentes
    ping_interval=25,
    max_http_buffer_size=1e8,  # Augmentation buffer
    transports=['websocket', 'polling'],  # Support fallback polling
    always_connect=True
)

# Configuration des logs améliorée
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Stockage temporaire
connected_lampadaires = {}
connected_clients = {}
esp_status = {}

# =================== HTTP ROUTES ===================

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        'message': 'Serveur Lampadaires Solaires Actif',
        'version': '2.1',
        'status': 'healthy',
        'endpoints': {
            'health': '/api/health',
            'update': '/api/lampadaire/update',
            'alert': '/api/alert',
            'websocket': '/lampadaires'
        },
        'stats': {
            'connected_lampadaires': len(connected_lampadaires),
            'connected_clients': len(connected_clients),
            'esp_devices': len(esp_status)
        }
    })

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check pour Render"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.datetime.now().isoformat(),
        'server': 'operational'
    }), 200

@app.route('/api/lampadaire/update', methods=['POST', 'OPTIONS'])
def update_lampadaire():
    """Receive lampadaire data from ESP32"""
    if request.method == 'OPTIONS':
        return '', 200
        
    try:
        data = request.json
        lamp_id = data.get('id')
        
        if not lamp_id:
            return jsonify({'error': 'ID lampadaire requis'}), 400
        
        data['server_timestamp'] = datetime.datetime.now().isoformat()
        data['synced'] = True
        
        connected_lampadaires[lamp_id] = data
        
        # Broadcast via WebSocket
        socketio.emit('lampadaire_update', {
            'type': 'lampadaire_update',
            'lampadaire': data
        }, namespace='/lampadaires', broadcast=True)
        
        logger.info(f"✅ Lampadaire {lamp_id} mis à jour: {data.get('lieu', 'N/A')}")
        
        return jsonify({
            'success': True,
            'message': 'Lampadaire mis à jour',
            'lampadaire_id': lamp_id
        }), 200
        
    except Exception as e:
        logger.error(f"❌ Erreur update lampadaire: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/alert', methods=['POST', 'OPTIONS'])
def create_alert():
    """Receive alert from ESP32"""
    if request.method == 'OPTIONS':
        return '', 200
        
    try:
        data = request.json
        data['created_at'] = datetime.datetime.now().isoformat()
        data['server_received'] = True
        
        socketio.emit('new_alert', {
            'type': 'new_alert',
            'alert': data
        }, namespace='/lampadaires', broadcast=True)
        
        logger.warning(f"⚠️ Alerte: {data.get('type')} - Lampadaire #{data.get('lampadaire_id')}")
        
        return jsonify({
            'success': True,
            'message': 'Alerte reçue',
            'alert_type': data.get('type')
        }), 200
        
    except Exception as e:
        logger.error(f"❌ Erreur create alert: {e}")
        return jsonify({'error': str(e)}), 500

# =================== WEBSOCKET EVENTS ===================

@socketio.on('connect', namespace='/lampadaires')
def handle_connect():
    """Handle client connections"""
    client_id = request.sid
    client_info = {
        'sid': client_id,
        'connected_at': datetime.datetime.now().isoformat(),
        'remote_addr': request.remote_addr
    }
    connected_clients[client_id] = client_info
    
    logger.info(f"🔌 Client connecté: {client_id} (Total: {len(connected_clients)})")
    
    join_room('lampadaires')
    
    emit('status', {
        'message': 'Connecté au serveur',
        'connected_lampadaires': len(connected_lampadaires),
        'timestamp': datetime.datetime.now().isoformat()
    })
    
    # Envoyer l'état actuel des lampadaires
    if connected_lampadaires:
        for lamp_id, lamp_data in connected_lampadaires.items():
            emit('lampadaire_update', {
                'type': 'lampadaire_update',
                'lampadaire': lamp_data
            })

@socketio.on('disconnect', namespace='/lampadaires')
def handle_disconnect():
    """Handle client disconnections"""
    client_id = request.sid
    if client_id in connected_clients:
        del connected_clients[client_id]
    
    logger.info(f"🔌 Client déconnecté: {client_id} (Restant: {len(connected_clients)})")

@socketio.on('auth', namespace='/lampadaires')
def handle_authenticate(data):
    """Authenticate ESP32/Android clients"""
    try:
        lampadaire_id = data.get('lampadaire_id')
        token = data.get('token')
        
        if not lampadaire_id or not token:
            emit('authenticated', {
                'success': False,
                'message': 'ID ou token manquant'
            })
            return
        
        expected_token = f"lampadaire_token_{lampadaire_id}"
        
        if token == expected_token:
            room_name = f"lampadaire_{lampadaire_id}"
            join_room(room_name)
            
            emit('authenticated', {
                'success': True,
                'lampadaire_id': lampadaire_id,
                'message': 'Authentifié avec succès'
            })
            
            logger.info(f"✅ Lampadaire #{lampadaire_id} authentifié")
        else:
            emit('authenticated', {
                'success': False,
                'message': 'Token invalide'
            })
            
    except Exception as e:
        logger.error(f"❌ Erreur auth: {e}")
        emit('authenticated', {'success': False, 'message': str(e)})

@socketio.on('command', namespace='/lampadaires')
def handle_command(data):
    """Handle commands from Android app to ESP32"""
    try:
        lamp_id = data.get('lamp_id')
        command = data.get('command')
        
        if not lamp_id or not command:
            logger.error("❌ Commande invalide")
            return
        
        room_name = f"lampadaire_{lamp_id}"
        
        emit('command', {
            'type': 'command',
            'command': command,
            'timestamp': datetime.datetime.now().isoformat()
        }, room=room_name)
        
        logger.info(f"📡 Commande '{command}' envoyée au lampadaire #{lamp_id}")
        
    except Exception as e:
        logger.error(f"❌ Erreur command: {e}")

@socketio.on('heartbeat', namespace='/lampadaires')
def handle_heartbeat(data):
    """Handle heartbeat from clients"""
    client_id = request.sid
    if client_id in connected_clients:
        connected_clients[client_id]['last_heartbeat'] = datetime.datetime.now().isoformat()
    
    emit('heartbeat_ack', {'timestamp': datetime.datetime.now().isoformat()})

# =================== DÉMARRAGE SERVEUR ===================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    
    logger.info(f"""
    ==========================================
    🚀 Serveur Lampadaires Démarré
    📡 Port: {port}
    🌐 URL: https://localisation-server.onrender.com
    ==========================================
    """)
    
    socketio.run(
        app,
        host='0.0.0.0',
        port=port,
        debug=False,
        allow_unsafe_werkzeug=True
    )
