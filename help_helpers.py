# help_helpers.py — Utilidades para el Bot Comercial de Leads
# Score Post Conversacion - 5 variables ponderadas
from typing import Optional, Dict, List


# ================================================================
# MAPEOS DE SCORING
# ================================================================

SENTIMIENTO_MAP = {"positivo": 1.0, "neutral": 0.5, "negativo": 0.0}
INTERES_MAP     = {"alto": 1.0, "medio": 0.5, "bajo": 0.0}
INTENCION_MAP   = {"alta": 1.0, "media": 0.5, "baja": 0.0}

# Pesos por defecto (se sobreescriben con config_modelo de BD)
# Score Post Conversacion: 5 variables
PESOS_DEFAULT = {
    "w_sentimiento": 0.20,      # 20% - Tono del mensaje
    "w_interes": 0.25,          # 25% - Nivel de interes
    "w_tiempo": 0.15,           # 15% - Tiempo de respuesta
    "w_intencion": 0.30,        # 30% - Intencion de compra
    "w_datos": 0.10,            # 10% - Datos capturados (DNI, nombre, correo)
    "alpha_ema": 0.40,          # Factor EMA (40% nuevo, 60% anterior)
    "umbral_derivacion": 0.75,  # Score para derivar a asesor
    "umbral_descarte": 0.30,    # Score minimo antes de descartar
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


def calcular_datos_capturados(lead: Dict) -> float:
    """
    Calcula el porcentaje de datos personales capturados del lead.
    Campos considerados: DNI, nombre, correo.
    Retorna valor entre 0 y 1:
      - 0 datos = 0.0
      - 1 dato  = 0.33
      - 2 datos = 0.67
      - 3 datos = 1.0
    """
    campos = [
        lead.get("dni"),
        lead.get("nombre"),
        lead.get("correo"),
    ]
    capturados = sum(1 for c in campos if c and str(c).strip())
    return round(capturados / 3.0, 2)


def calcular_raw_score(
    sentimiento: str,
    nivel_interes: str,
    intencion_compra: str,
    tiempo_score: float,
    datos_capturados: float = 0.0,
    config: Optional[Dict[str, str]] = None,
) -> float:
    """
    Calcula el raw_score ponderado a partir de las 5 variables.
    raw_score = w_s*S + w_i*I + w_t*T + w_c*C + w_d*D
    Retorna valor en [0, 1].
    """
    config = config or {}

    w_s = float(config.get("w_sentimiento", PESOS_DEFAULT["w_sentimiento"]))
    w_i = float(config.get("w_interes", PESOS_DEFAULT["w_interes"]))
    w_t = float(config.get("w_tiempo", PESOS_DEFAULT["w_tiempo"]))
    w_c = float(config.get("w_intencion", PESOS_DEFAULT["w_intencion"]))
    w_d = float(config.get("w_datos", PESOS_DEFAULT["w_datos"]))

    sent_num = SENTIMIENTO_MAP.get(sentimiento, 0.5)
    int_num  = INTERES_MAP.get(nivel_interes, 0.0)
    comp_num = INTENCION_MAP.get(intencion_compra, 0.0)

    raw = (
        w_s * sent_num +
        w_i * int_num +
        w_t * tiempo_score +
        w_c * comp_num +
        w_d * datos_capturados
    )
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
    config: Optional[Dict[str, str]] = None,
) -> bool:
    """
    Determina si el lead debe descartarse automaticamente.
    Condicion: intencion baja + sentimiento negativo + score < umbral_descarte
    """
    config = config or {}
    umbral = float(config.get("umbral_descarte", PESOS_DEFAULT["umbral_descarte"]))

    return (
        intencion_compra == "baja"
        and sentimiento == "negativo"
        and score_nuevo < umbral
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
