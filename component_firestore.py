# component_firestore.py — Firestore para Bot Comercial
from datetime import datetime, timedelta
import pytz
from google.cloud import firestore


class DataBaseFirestoreManager:
    """
    Manager de Firestore para el bot comercial.
    Usa la coleccion 'comercial' para registrar mensajes in/out.
    """

    def __init__(self):
        self.db = self._connect()
        self.tz = pytz.timezone("America/Lima")
        self.collection_name = "comercial"

    def _connect(self):
        try:
            db = firestore.Client()
            print("[FIRESTORE] Conexion exitosa a Firestore", flush=True)
            return db
        except Exception as e:
            print(f"[FIRESTORE] ERROR al conectar: {e}", flush=True)
            return None

    def _reconnect_if_needed(self):
        try:
            _ = self.db.collection(self.collection_name).document("connection_test").get()
        except Exception as e:
            print(f"[FIRESTORE] Conexion perdida, reconectando... {e}", flush=True)
            self.db = self._connect()

    def crear_documento(self, celular, id_lead, id_bot, mensaje, sender):
        """
        Crea un documento en la coleccion 'comercial'.
        sender=True si es mensaje del lead, False si es del bot.
        """
        self._reconnect_if_needed()

        data = {
            "celular": celular,
            "fecha": firestore.SERVER_TIMESTAMP,
            "id_lead": id_lead,
            "id_bot": id_bot,
            "mensaje": mensaje,
            "sender": sender,
        }
        try:
            doc_ref = self.db.collection(self.collection_name).document()
            doc_ref.set(data)
            print("[FIRESTORE] Documento creado exitosamente.", flush=True)
        except Exception as e:
            print(f"[FIRESTORE] Error al crear documento: {e}", flush=True)

    def recuperar_mensajes_hoy(self, id_bot, celular):
        """Recupera todos los mensajes de hoy para un lead especifico."""
        self._reconnect_if_needed()

        try:
            now = datetime.now(self.tz)
            start_datetime = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_datetime = start_datetime + timedelta(days=1)

            query = (
                self.db.collection(self.collection_name)
                .where("id_bot", "==", id_bot)
                .where("celular", "==", celular)
                .where("fecha", ">=", start_datetime)
                .where("fecha", "<", end_datetime)
            )

            docs = query.stream()
            mensajes = [doc.to_dict() for doc in docs]
            return mensajes

        except Exception as e:
            print(f"[FIRESTORE] Error al recuperar mensajes: {e}", flush=True)
            return []

    def recuperar_mensajes_hoy_alt(self, id_bot, celular):
        """Recupera mensajes de hoy (UTC) para un lead especifico."""
        self._reconnect_if_needed()

        try:
            now = datetime.utcnow()
            start_datetime = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_datetime = start_datetime + timedelta(days=1)

            print(f"[FIRESTORE] Rango: {start_datetime} - {end_datetime} | cel={celular}", flush=True)

            query = (
                self.db.collection(self.collection_name)
                .where(field_path="id_bot", op_string="==", value=id_bot)
                .where(field_path="celular", op_string="==", value=celular)
                .where(field_path="fecha", op_string=">=", value=start_datetime)
                .where(field_path="fecha", op_string="<", value=end_datetime)
            )

            docs = query.stream()
            mensajes = [doc.to_dict() for doc in docs]
            print(f"[FIRESTORE] Mensajes recuperados: {len(mensajes)}", flush=True)
            return mensajes

        except Exception as e:
            print(f"[FIRESTORE] Error al recuperar mensajes: {e}", flush=True)
            return []
