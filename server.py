import asyncio
import json
import logging
from typing import Dict, Set, Any
import websockets
from websockets import WebSocketServerProtocol

# ------------- Configuration -------------
HOST = "0.0.0.0"
PORT = 8080
PING_INTERVAL = 20         # secondes pour ping/pong (heartbeat)
PING_TIMEOUT = 10          # timeout pong
# -----------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Stockage en mémoire des connexions
connected_android: Set[WebSocketServerProtocol] = set()
connected_esp: Dict[WebSocketServerProtocol, Dict[str, Any]] = dict()
lock = asyncio.Lock()


async def safe_send(ws: WebSocketServerProtocol, message: str):
    """Envoi sûr avec gestion d'exception."""
    try:
        await ws.send(message)
    except Exception as e:
        logging.warning("Erreur en envoyant au client %s : %s", ws.remote_address, e)


async def broadcast_to_android(message: str):
    """Diffuse message à tous les clients Android connectés."""
    async with lock:
        if not connected_android:
            logging.info("Aucun client Android connecté — message non diffused")
            return
        logging.info("Broadcast à %d client(s) Android", len(connected_android))
        coros = [safe_send(ws, message) for ws in list(connected_android)]
    # en dehors du lock pour ne pas bloquer modifications
    await asyncio.gather(*coros, return_exceptions=True)


async def handle_register(ws: WebSocketServerProtocol, data: dict):
    """Traite le message d'enregistrement initial."""
    role = data.get("role")
    if role == "android":
        async with lock:
            connected_android.add(ws)
        logging.info("Android enregistré: %s", ws.remote_address)
        # Option : envoyer état d'accueil
        await safe_send(ws, json.dumps({"type": "welcome", "role": "android"}))
    elif role == "esp32":
        meta = {"id": data.get("id"), "info": data.get("info")}
        async with lock:
            connected_esp[ws] = meta
        logging.info("ESP32 enregistré: %s id=%s", ws.remote_address, meta.get("id"))
        await safe_send(ws, json.dumps({"type": "welcome", "role": "esp32", "ack": True}))
    else:
        # rôle inconnu, on laisse le client ; on détectera ensuite
        logging.info("Register reçu with unknown role: %s", data)


async def process_message(ws: WebSocketServerProtocol, message: str):
    """Valide JSON et route en conséquence."""
    try:
        obj = json.loads(message)
    except json.JSONDecodeError:
        logging.warning("Message non JSON reçu: %s", message)
        # Option : renvoyer erreur
        await safe_send(ws, json.dumps({"type": "error", "message": "invalid_json"}))
        return

    mtype = obj.get("type")
    # Si c'est un enregistrement
    if mtype == "register":
        await handle_register(ws, obj)
        return

    # Si le client n'est pas enregistré mais envoie update => supposer que c'est un ESP32
    async with lock:
        is_esp = ws in connected_esp
        is_android = ws in connected_android

    if not is_esp and not is_android:
        # Première réception non-register : on infère rôle via type
        if mtype in ("lampadaire_update", "alert"):
            # On considère ce ws comme ESP32
            async with lock:
                connected_esp[ws] = {"id": obj.get("lampadaire", {}).get("id")}
            logging.info("Inféré rôle ESP32 pour %s id=%s", ws.remote_address,
                         connected_esp[ws]["id"])
            is_esp = True
        else:
            # On considère comme Android (requête de consultation)
            async with lock:
                connected_android.add(ws)
            is_android = True
            logging.info("Inféré rôle Android pour %s", ws.remote_address)

    # Si message d'esp32 => broadcast aux android
    if mtype in ("lampadaire_update", "alert"):
        # Sender ack
        await safe_send(ws, json.dumps({"type": "ack", "received": True, "orig_type": mtype}))

        # Forward exactement le même JSON au(x) androids
        await broadcast_to_android(json.dumps(obj))
        logging.info("Relayed message type=%s from %s", mtype, ws.remote_address)
    else:
        # Autres types de messages: on peut les router selon besoin (ex: commandes depuis Android vers ESP)
        # Par exemple : si un Android envoie une commande (type=command, target_id=...), forward to relevant ESP
        if mtype == "command":
            target_id = obj.get("target_id")
            if not target_id:
                await safe_send(ws, json.dumps({"type": "error", "message": "missing target_id"}))
                return
            # find esp by id
            async with lock:
                target_ws = None
                for esp_ws, meta in connected_esp.items():
                    if meta.get("id") == target_id:
                        target_ws = esp_ws
                        break
            if target_ws:
                await safe_send(target_ws, json.dumps(obj))
                await safe_send(ws, json.dumps({"type": "ok", "message": "command_sent"}))
                logging.info("Command forwarded to ESP id=%s", target_id)
            else:
                await safe_send(ws, json.dumps({"type": "error", "message": "esp_not_connected"}))
        else:
            # Message inconnu: log et ack léger
            await safe_send(ws, json.dumps({"type": "ack", "received": True, "note": "unknown_type"}))
            logging.info("Message de type inconnu reçu: %s", mtype)


async def unregister(ws: WebSocketServerProtocol):
    """Nettoyage lors de la fermeture de connexion."""
    async with lock:
        if ws in connected_android:
            connected_android.remove(ws)
            logging.info("Android déconnecté: %s", ws.remote_address)
        if ws in connected_esp:
            info = connected_esp.pop(ws)
            logging.info("ESP32 déconnecté: %s id=%s", ws.remote_address, info.get("id"))


async def ws_handler(ws: WebSocketServerProtocol, path: str):
    """Handler principal pour chaque connexion WebSocket."""
    logging.info("Connexion entrante: %s path=%s", ws.remote_address, path)
    try:
        # On définit des pings automatique via la boucle ci-dessous
        async for message in ws:
            # chaque message reçu est traité
            await process_message(ws, message)
    except websockets.exceptions.ConnectionClosedOK:
        logging.info("Connexion fermée proprement: %s", ws.remote_address)
    except Exception as e:
        logging.exception("Erreur connexion %s : %s", ws.remote_address, e)
    finally:
        await unregister(ws)


async def periodic_ping(server):
    """Ping régulier pour s'assurer de la vivacité des connections."""
    while True:
        await asyncio.sleep(PING_INTERVAL)
        async with lock:
            sockets = list(connected_android | set(connected_esp.keys()))
        if not sockets:
            continue
        logging.debug("Ping %d sockets", len(sockets))
        coros = []
        for s in sockets:
            # send ping and wait pong with timeout
            try:
                coros.append(asyncio.wait_for(s.ping(), timeout=PING_TIMEOUT))
            except Exception as e:
                logging.debug("Ping exception for %s: %s", s.remote_address, e)
        if coros:
            # attendre que tous terminent (catch exceptions)
            results = await asyncio.gather(*coros, return_exceptions=True)
            # nettoyer les sockets morts
            dead = []
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    # r correspond à l'exception du ping d'un socket donné
                    # identifier quel socket a échoué est plus compliqué dans cette boucle, on fera une passe de nettoyage simple:
                    pass
            # simple nettoyage : purge les websockets fermés
            async with lock:
                to_remove_android = {s for s in connected_android if s.closed}
                to_remove_esp = {s for s in connected_esp.keys() if s.closed}
                for s in to_remove_android:
                    connected_android.discard(s)
                    logging.info("Nettoyage Android socket fermée: %s", getattr(s, "remote_address", None))
                for s in to_remove_esp:
                    meta = connected_esp.pop(s, None)
                    logging.info("Nettoyage ESP socket fermée: %s id=%s", getattr(s, "remote_address", None), meta and meta.get("id"))
        # boucle continue


async def main():
    logging.info("Démarrage du serveur WebSocket sur %s:%d", HOST, PORT)
    server = await websockets.serve(ws_handler, HOST, PORT, ping_interval=None)  # on gère ping manuel
    # lancer tache ping
    ping_task = asyncio.create_task(periodic_ping(server))
    try:
        await server.wait_closed()
    finally:
        ping_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Arrêt demandé par l'utilisateur")