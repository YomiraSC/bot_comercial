# help_helpers.py — Utilidades para el Bot Comercial de Leads
from typing import Optional, Dict, List


# ================================================================
# MAPEOS DE SCORING
# ================================================================

SENTIMIENTO_MAP = {"positivo": 1.0, "neutral": 0.5, "negativo": 0.0}
INTERES_MAP     = {"alto": 1.0, "medio": 0.5, "bajo": 0.0}
INTENCION_MAP   = {"alta": 1.0, "media": 0.5, "baja": 0.0}

# Pesos por defecto (se sobreescriben con config_modelo de BD)
PESOS_DEFAULT = {
    "w_sentimiento": 0.25,
    "w_interes": 0.30,
    "w_tiempo": 0.15,
    "w_intencion": 0.30,
    "alpha_ema": 0.40,
    "umbral_derivacion": 0.75,
}


# ================================================================
# FUNCIONES DE SCORING
# ================================================================

def score_tiempo_respuesta(segundos: Optional[int]) -> float:
    """
    Convierte tiempo de respuesta en segundos a un score de engagement.
    < 2 min  = 1.0 (alto engagement)
    2-10 min = 0.7 (medio)
    10-60 min = 0.3 (bajo)
    > 60 min = 0.0 (penalizacion fuerte)
    None (primer mensaje) = 0.5 (neutral)
    """
    if segundos is None:
        return 0.5
    if segundos <= 120:
        return 1.0
    elif segundos <= 600:
        return 0.7
    elif segundos <= 3600:
        return 0.3
    else:
        return 0.0


def calcular_raw_score(
    sentimiento: str,
    nivel_interes: str,
    intencion_compra: str,
    tiempo_score: float,
    config: Optional[Dict[str, str]] = None,
) -> float:
    """
    Calcula el raw_score ponderado a partir de las 4 variables.
    raw_score = w_s*S + w_i*I + w_t*T + w_c*C
    Retorna valor en [0, 1].
    """
    config = config or {}

    w_s = float(config.get("w_sentimiento", PESOS_DEFAULT["w_sentimiento"]))
    w_i = float(config.get("w_interes", PESOS_DEFAULT["w_interes"]))
    w_t = float(config.get("w_tiempo", PESOS_DEFAULT["w_tiempo"]))
    w_c = float(config.get("w_intencion", PESOS_DEFAULT["w_intencion"]))

    sent_num = SENTIMIENTO_MAP.get(sentimiento, 0.5)
    int_num  = INTERES_MAP.get(nivel_interes, 0.0)
    comp_num = INTENCION_MAP.get(intencion_compra, 0.0)

    raw = w_s * sent_num + w_i * int_num + w_t * tiempo_score + w_c * comp_num
    return max(0.0, min(1.0, raw))


def calcular_score_ema(
    score_antes: float,
    raw_score: float,
    config: Optional[Dict[str, str]] = None,
) -> float:
    """
    Calcula el nuevo score usando Exponential Moving Average (EMA).
    score_nuevo = score_antes * (1 - alpha) + raw_score * alpha
    Clamp a [0, 1], redondeado a 4 decimales.
    """
    config = config or {}
    alpha = float(config.get("alpha_ema", PESOS_DEFAULT["alpha_ema"]))

    score_nuevo = score_antes * (1 - alpha) + raw_score * alpha
    return max(0.0, min(1.0, round(score_nuevo, 4)))


def calcular_contactabilidad(contactos: int) -> float:
    """
    Calcula contactabilidad basada en cantidad de interacciones.
    Formula: min(1.0, (contactos + 1) / 10)
    Despues de 10 interacciones se maximiza en 1.0.
    """
    return round(min(1.0, (contactos + 1) / 10.0), 2)


def debe_descartar_lead(
    intencion_compra: str,
    sentimiento: str,
    score_nuevo: float,
) -> bool:
    """
    Determina si el lead debe descartarse automaticamente.
    Condicion: intencion baja + sentimiento negativo + score < 0.10
    """
    return (
        intencion_compra == "baja"
        and sentimiento == "negativo"
        and score_nuevo < 0.10
    )


def debe_derivar_a_asesor(
    score_nuevo: float,
    derivar_flag: bool,
    config: Optional[Dict[str, str]] = None,
) -> bool:
    """
    Determina si el lead debe derivarse a un asesor.
    Condicion: score >= umbral O el LLM indico derivar=true.
    """
    config = config or {}
    umbral = float(config.get("umbral_derivacion", PESOS_DEFAULT["umbral_derivacion"]))
    return derivar_flag or score_nuevo >= umbral


# ================================================================
# UTILIDADES DE FORMATO
# ================================================================

def formatear_conversacion(mensajes: List[Dict]) -> str:
    """
    Formatea una conversacion de Firestore en texto legible.
    Cada mensaje debe tener 'mensaje' y 'sender' (True=lead, False=bot).
    """
    conversacion_formateada = []

    for msg in sorted(mensajes, key=lambda x: x.get("fecha", "")):
        rol = "Lead" if msg.get("sender", True) else "Asistente"
        texto = msg.get("mensaje", "").strip()
        if texto:
            conversacion_formateada.append(f"{rol}: {texto}")

    return "\n".join(conversacion_formateada)


def limpiar_json_llm(texto: str) -> str:
    """
    Limpia la respuesta de un LLM para obtener JSON valido.
    Quita bloques ```json``` y espacios extra.
    """
    texto = texto.strip()
    if texto.startswith("```json"):
        texto = texto[7:]
    elif texto.startswith("```"):
        texto = texto[3:]
    if texto.endswith("```"):
        texto = texto[:-3]
    return texto.strip()
