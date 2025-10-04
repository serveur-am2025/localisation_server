from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_cors import CORS
import logging
import datetime
import os

# Configuration
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'votre_cle_secrete_lampadaire_2024')
CORS(app, resources={r"/*": {"origins": "*"}})

# ✅ Configuration SocketIO pour Render (avec threading)
socketio = SocketIO(
    app, 
    cors_allowed_origins="*",
    async_mode='threading',  # Important pour Render
    logger=True,
    engineio_logger=True,
    ping_timeout=60,
    ping_interval=25
)

# Configuration des logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Stockage temporaire des lampadaires connectés
connected_lampadaires = {}
connected_clients = set()

# =================== HTTP ROUTES ===================

@app.route('/', methods=['GET'])
def index():
    """Page d'accueil du serveur"""
    return jsonify({
        'message': 'Serveur Lampadaires Solaires Actif',
        'version': '2.0',
        'endpoints': {
            'update': '/api/lampadaire/update',
            'alert': '/api/alert',
            'websocket': '/lampadaires'
        },
        'connected_lampadaires': len(connected_lampadaires),
        'connected_clients': len(connected_clients)
    })

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check pour Render"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.datetime.now().isoformat()
    }), 200

@app.route('/api/lampadaire/update', methods=['POST'])
def update_lampadaire():
    """Receive lampadaire data from ESP32"""
    try:
        data = request.json
        lamp_id = data.get('id')
        
        if not lamp_id:
            return jsonify({'error': 'ID lampadaire requis'}), 400
        
        # Ajouter timestamp serveur
        data['server_timestamp'] = datetime.datetime.now().isoformat()
        data['synced'] = True
        
        # Stocker temporairement
        connected_lampadaires[lamp_id] = data
        
        # Broadcast via WebSocket
        socketio.emit('lampadaire_update', {
            'type': 'lampadaire_update',
            'lampadaire': data
        }, namespace='/lampadaires')
        
        logger.info(f"✅ Lampadaire {lamp_id} mis à jour: {data.get('lieu', 'N/A')}")
        
        return jsonify({
            'success': True,
            'message': 'Lampadaire mis à jour',
            'lampadaire_id': lamp_id
        }), 200
        
    except Exception as e:
        logger.error(f"❌ Erreur update lampadaire: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/alert', methods=['POST'])
def create_alert():
    """Receive alert from ESP32"""
    try:
        data = request.json
        
        # Ajouter timestamp serveur
        data['created_at'] = datetime.datetime.now().isoformat()
        data['server_received'] = True
        
        # Broadcast via WebSocket
        socketio.emit('new_alert', {
            'type': 'new_alert',
            'alert': data
        }, namespace='/lampadaires')
        
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
    connected_clients.add(client_id)
    
    logger.info(f"🔌 Client connecté: {client_id} (Total: {len(connected_clients)})")
    
    join_room('lampadaires')
    
    emit('status', {
        'message': 'Connecté au serveur',
        'connected_lampadaires': len(connected_lampadaires),
        'timestamp': datetime.datetime.now().isoformat()
    })

@socketio.on('disconnect', namespace='/lampadaires')
def handle_disconnect():
    """Handle client disconnections"""
    client_id = request.sid
    connected_clients.discard(client_id)
    
    logger.info(f"🔌 Client déconnecté: {client_id} (Restant: {len(connected_clients)})")

@socketio.on('auth', namespace='/lampadaires')
def handle_authenticate(data):
    """Authenticate ESP32 clients"""
    try:
        lampadaire_id = data.get('lampadaire_id')
        token = data.get('token')
        
        if not lampadaire_id or not token:
            emit('authenticated', {
                'success': False,
                'message': 'ID ou token manquant'
            })
            return
        
        # Simple token validation
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
            logger.error("❌ Commande invalide: ID ou commande manquant")
            return
        
        # Envoyer commande au lampadaire spécifique
        room_name = f"lampadaire_{lamp_id}"
        
        emit('command', {
            'type': 'command',
            'command': command,
            'timestamp': datetime.datetime.now().isoformat()
        }, room=room_name)
        
        logger.info(f"📡 Commande '{command}' envoyée au lampadaire #{lamp_id}")
        
    except Exception as e:
        logger.error(f"❌ Erreur command: {e}")

@socketio.on('lampadaire_update', namespace='/lampadaires')
def handle_lampadaire_update(data):
    """Handle updates from ESP32 via WebSocket"""
    try:
        lampadaire_data = data.get('lampadaire')
        
        if lampadaire_data:
            lamp_id = lampadaire_data.get('id')
            connected_lampadaires[lamp_id] = lampadaire_data
            
            # Broadcast à tous les clients Android
            emit('lampadaire_update', {
                'type': 'lampadaire_update',
                'lampadaire': lampadaire_data
            }, broadcast=True)
            
            logger.info(f"🔄 Update WebSocket lampadaire #{lamp_id}")
            
    except Exception as e:
        logger.error(f"❌ Erreur lampadaire_update: {e}")

@socketio.on('alert', namespace='/lampadaires')
def handle_alert(data):
    """Handle alerts from ESP32 via WebSocket"""
    try:
        alert_data = data.get('alert')
        
        if alert_data:
            alert_data['created_at'] = datetime.datetime.now().isoformat()
            
            # Broadcast à tous les clients Android
            emit('new_alert', {
                'type': 'new_alert',
                'alert': alert_data
            }, broadcast=True)
            
            logger.warning(f"⚠️ Alerte WebSocket: {alert_data.get('type')}")
            
    except Exception as e:
        logger.error(f"❌ Erreur alert: {e}")

# =================== DÉMARRAGE SERVEUR ===================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))  # Render utilise PORT
    
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
        debug=False,  # False en production
        allow_unsafe_werkzeug=True
    )
