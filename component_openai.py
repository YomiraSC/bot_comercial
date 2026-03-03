# component_openai.py — OpenAI Manager para Bot Comercial
from openai import OpenAI
from help_prompt import (
    prompt_analisis_nlp_lead,
    prompt_extraccion_datos_personales,
    prompt_inicio_conversacion_comercial,
    prompt_obtener_dni,
)
from help_helpers import limpiar_json_llm
import pytz
import json
from datetime import datetime
from api_keys import openai_api_key


class OpenAIComercialManager:
    """
    Manager de OpenAI para el bot comercial.
    Metodos especializados en analisis NLP de leads:
    sentimiento, nivel de interes, intencion de compra, extraccion de datos.
    """

    def __init__(self):
        self.client = OpenAI(api_key=openai_api_key)
        self.tz = pytz.timezone("America/Lima")
        self.model = "gpt-4.1-2025-04-14"

    # ================================================================
    # ANALISIS NLP DE MENSAJE
    # ================================================================

    def analizar_mensaje_nlp(self, mensaje_lead: str) -> dict:
        """
        Analiza el mensaje del lead y retorna:
        {
            "sentimiento": "positivo|neutral|negativo",
            "nivel_interes": "alto|medio|bajo",
            "intencion_compra": "alta|media|baja",
            "keywords": [...],
            "reply": "...",
            "derivar": true|false
        }
        Si falla, retorna un dict con valores por defecto.
        """
        prompt_sistema = prompt_analisis_nlp_lead()

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": prompt_sistema},
                    {"role": "user", "content": mensaje_lead or ""},
                ],
                temperature=0.2,
                max_tokens=300,
            )

            raw = response.choices[0].message.content.strip()
            raw = limpiar_json_llm(raw)
            print(f"[OPENAI-COM] analizar_nlp raw={raw!r}", flush=True)

            data = json.loads(raw)
            return {
                "sentimiento": (data.get("sentimiento") or "neutral").strip().lower(),
                "nivel_interes": (data.get("nivel_interes") or "bajo").strip().lower(),
                "intencion_compra": (data.get("intencion_compra") or "baja").strip().lower(),
                "keywords": data.get("keywords") or [],
                "reply": (data.get("reply") or "").strip(),
                "derivar": bool(data.get("derivar")),
            }

        except json.JSONDecodeError as e:
            print(f"[OPENAI-COM] analizar_nlp JSON invalido ({e})", flush=True)
            return self._nlp_fallback()
        except Exception as e:
            print(f"[OPENAI-COM] analizar_nlp error: {e}", flush=True)
            return self._nlp_fallback()

    def _nlp_fallback(self) -> dict:
        """Valores por defecto cuando el analisis NLP falla."""
        return {
            "sentimiento": "neutral",
            "nivel_interes": "bajo",
            "intencion_compra": "baja",
            "keywords": [],
            "reply": "",
            "derivar": False,
        }

    # ================================================================
    # EXTRACCION DE DATOS PERSONALES
    # ================================================================

    def extraer_datos_personales(self, mensaje_lead: str) -> dict:
        """
        Extrae DNI, nombre, apellido y correo del mensaje.
        Retorna:
        {
            "dni": "12345678" | null,
            "tipo_documento": "DNI|RUC|CE" | null,
            "nombre": "Juan" | null,
            "apellido": "Perez" | null,
            "correo": "juan@mail.com" | null
        }
        """
        prompt_sistema = prompt_extraccion_datos_personales()

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": prompt_sistema},
                    {"role": "user", "content": mensaje_lead or ""},
                ],
                temperature=0,
                max_tokens=100,
            )

            raw = response.choices[0].message.content.strip()
            raw = limpiar_json_llm(raw)
            print(f"[OPENAI-COM] extraer_datos raw={raw!r}", flush=True)

            data = json.loads(raw)
            return {
                "dni": data.get("dni"),
                "tipo_documento": data.get("tipo_documento"),
                "nombre": data.get("nombre"),
                "apellido": data.get("apellido"),
                "correo": data.get("correo"),
            }

        except Exception as e:
            print(f"[OPENAI-COM] extraer_datos error: {e}", flush=True)
            return {"dni": None, "tipo_documento": None, "nombre": None, "apellido": None, "correo": None}

    # ================================================================
    # OBTENER DNI DE CONVERSACION COMPLETA
    # ================================================================

    def obtener_dni_brindado(self, mensaje_lead: str) -> dict:
        """
        Extrae DNI, RUC o CE del mensaje.
        Retorna {"tipo": "DNI|RUC|CE", "numero": "XXXXXXXX"} o None.
        Compatible con el patron del bot de reactivaciones.
        """
        prompt = prompt_obtener_dni(mensaje_lead)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": prompt}],
                max_tokens=50,
            )

            raw = response.choices[0].message.content.strip()
            raw = limpiar_json_llm(raw)
            print(f"[OPENAI-COM] obtener_dni raw={raw!r}", flush=True)

            dni_data = json.loads(raw)

            if isinstance(dni_data, dict) and "tipo" in dni_data and "numero" in dni_data:
                if dni_data["tipo"] in ["DNI", "RUC", "CE"] and dni_data["numero"] and dni_data["numero"].isdigit():
                    return dni_data

            return None

        except Exception as e:
            print(f"[OPENAI-COM] obtener_dni error: {e}", flush=True)
            return None

    # ================================================================
    # MENSAJES PREDEFINIDOS
    # ================================================================

    def generar_respuesta_inicio_conversacion(self) -> str:
        """Retorna el mensaje de bienvenida del bot comercial."""
        return prompt_inicio_conversacion_comercial()
