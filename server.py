from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit, join_room
from flask_cors import CORS
import logging
import datetime

# Configuration
app = Flask(__name__)
app.config['SECRET_KEY'] = 'votre_cle_secrete_lampadaire_2024'
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Configuration des logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# HTTP Routes

@app.route('/api/lampadaire/update', methods=['POST'])
def update_lampadaire():
    """Receive lampadaire data from Arduino and broadcast to Android app"""
    data = request.json
    lamp_id = data.get('id')
    
    if not lamp_id:
        return jsonify({'error': 'ID lampadaire requis'}), 400
    
    # Prepare data for broadcasting
    lampadaire_data = {
        'id': lamp_id,
        'latitude': data.get('latitude', 0),
        'longitude': data.get('longitude', 0),
        'etat': data.get('etat', 'OK'),
        'batterie': data.get('batterie', 100),
        'led_status': data.get('led_status', False),
        'luminosite': data.get('luminosite', 0),
        'pir_detection': data.get('pir_detection', False),
        'derniere_remontee': data.get('derniere_remontee', datetime.datetime.now().isoformat()),
        'lieu': data.get('lieu', ''),
        'synced': True
    }
    
    # Broadcast to Android app clients
    socketio.emit('lampadaire_update', lampadaire_data, namespace='/lampadaires')
    
    logger.info(f"Lampadaire {lamp_id} mis à jour")
    return jsonify({'success': True, 'message': 'Lampadaire mis à jour'})

@app.route('/api/alert', methods=['POST'])
def create_alert():
    """Receive alert from Arduino and broadcast to Android app"""
    data = request.json
    
    # Prepare alert data
    alert_data = {
        'lampadaire_id': data.get('lampadaire_id'),
        'type': data.get('type'),
        'titre': data.get('titre'),
        'message': data.get('message'),
        'priorite': data.get('priorite', 'moyenne'),
        'lieu': data.get('lieu'),
        'latitude': data.get('latitude'),
        'longitude': data.get('longitude'),
        'created_at': datetime.datetime.now().isoformat()
    }
    
    # Broadcast to Android app clients
    socketio.emit('new_alert', alert_data, namespace='/lampadaires')
    
    logger.warning(f"Nouvelle alerte: {data.get('type')} - Lampadaire {data.get('lampadaire_id')}")
    return jsonify({'success': True, 'message': 'Alerte reçue'})

# WebSocket Events

@socketio.on('connect', namespace='/lampadaires')
def handle_connect():
    """Handle WebSocket connections from Arduino and Android app"""
    logger.info(f"Client connecté: {request.sid}")
    join_room('lampadaires')
    emit('status', {'message': 'Connecté au serveur de lampadaires'})

@socketio.on('disconnect', namespace='/lampadaires')
def handle_disconnect():
    """Handle WebSocket disconnections"""
    logger.info(f"Client déconnecté: {request.sid}")

@socketio.on('auth', namespace='/lampadaires')
def handle_authenticate(data):
    """Authenticate Arduino clients"""
    lampadaire_id = data.get('lampadaire_id')
    token = data.get('token')
    
    if not lampadaire_id or not token:
        emit('authenticated', {'success': False, 'message': 'ID ou token manquant'})
        return
    
    # Verify token (simple check for lampadaire_token_<ID>)
    expected_token = f"lampadaire_token_{lampadaire_id}"
    if token == expected_token:
        join_room(f"lampadaire_{lampadaire_id}")
        emit('authenticated', {'success': True, 'lampadaire_id': lampadaire_id})
        logger.info(f"Lampadaire {lampadaire_id} authentifié")
    else:
        emit('authenticated', {'success': False, 'message': 'Token invalide'})

@socketio.on('lampadaire_update', namespace='/lampadaires')
def handle_lampadaire_update(data):
    """Handle lampadaire updates from Arduino and broadcast to Android app"""
    lampadaire_data = data.get('lampadaire')
    if lampadaire_data:
        socketio.emit('lampadaire_update', lampadaire_data, namespace='/lampadaires')
        logger.info(f"Update reçu pour lampadaire {lampadaire_data.get('id')}")
    else:
        logger.error("Données lampadaire manquantes dans la mise à jour")

@socketio.on('alert', namespace='/lampadaires')
def handle_alert(data):
    """Handle alerts from Arduino and broadcast to Android app"""
    alert_data = data.get('alert')
    if alert_data:
        alert_data['created_at'] = datetime.datetime.now().isoformat()
        socketio.emit('new_alert', alert_data, namespace='/lampadaires')
        logger.warning(f"Alerte reçue: {alert_data.get('type')} - Lampadaire {alert_data.get('lampadaire_id')}")
    else:
        logger.error("Données alerte manquantes")

@socketio.on('command', namespace='/lampadaires')
def handle_command(data):
    """Handle commands from Android app and send to specific lampadaire"""
    lamp_id = data.get('lamp_id')
    command = data.get('command')
    
    if lamp_id and command:
        # Send command to the specific lampadaire
        socketio.emit('command', {'command': command}, room=f"lampadaire_{lamp_id}", namespace='/lampadaires')
        logger.info(f"Commande {command} envoyée au lampadaire {lamp_id}")
    else:
        logger.error("ID lampadaire ou commande manquant")

if __name__ == '__main__':
    logger.info("Serveur de lampadaires démarré")
    socketio.run(app, host='0.0.0.0', port=8080, debug=True)
