# app_comercial.py — Bot de LEADS COMERCIALES
import os
import re
import json
from datetime import datetime
from typing import Any, Dict, Optional

from flask import Flask, request, Response, g

# LangChain / LangGraph
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_elasticsearch import ElasticsearchStore
from langchain.tools import tool, Tool
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg_pool import ConnectionPool

# Meta WhatsApp Cloud API
import requests

# ==== COMPONENTES ====
from api_keys import openai_api_key
from component_postgresql_comercial import DataBasePostgreSQLComercialManager
from component_firestore import DataBaseFirestoreManager
from component_openai import OpenAIComercialManager

# ==== HELPERS Y PROMPTS ====
from help_prompt import (
    prompt_analisis_nlp_lead,
    prompt_extraccion_datos_personales,
    prompt_sistema_agente,
    prompt_inicio_conversacion_comercial,
)
from help_helpers import (
    SENTIMIENTO_MAP,
    INTERES_MAP,
    INTENCION_MAP,
    score_tiempo_respuesta,
    calcular_datos_capturados,
    calcular_raw_score,
    calcular_score_ema,
    calcular_contactabilidad,
    debe_descartar_lead,
    debe_derivar_a_asesor,
    formatear_conversacion,
    limpiar_json_llm,
)

# Para Vercel mirror
import threading, time, uuid

VERCEL_WEBHOOK_URL = "https://crmcomercial.vercel.app/api/webhook/whatsapp"

# -------------------------------------------------------------------
# 0) CONFIG GLOBAL (ENV)
# -------------------------------------------------------------------
os.environ["OPENAI_API_KEY"] = openai_api_key

# ⚠️  REEMPLAZAR con los tokens del numero comercial
WHATSAPP_TOKEN   = "TU_WHATSAPP_TOKEN_COMERCIAL"
PHONE_NUMBER_ID  = "TU_PHONE_NUMBER_ID_COMERCIAL"
VERIFY_TOKEN     = "token_comercial"

# PostgreSQL
os.environ["DB_URI"] = "postgresql://maquisistema:sayainvestments1601@34.82.84.15:5432/bdMaqui?sslmode=disable"

# Elasticsearch RAG (indice comercial)
os.environ["ELASTIC_URL"]      = "http://34.83.130.207:9200"
os.environ["ELASTIC_USER"]     = "elastic"
os.environ["ELASTIC_PASSWORD"] = "P=IK-doIv668orND5FmG"
os.environ["ELASTIC_INDEX"]    = "comercial-v1"

API_URL = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_TOKEN}",
    "Content-Type": "application/json",
}

ELASTIC_URL      = os.environ["ELASTIC_URL"]
ELASTIC_USER     = os.environ["ELASTIC_USER"]
ELASTIC_PASSWORD = os.environ["ELASTIC_PASSWORD"]
ELASTIC_INDEX    = os.environ["ELASTIC_INDEX"]
DB_URI           = os.environ["DB_URI"]


# -------------------------------------------------------------------
# 1) HELPERS META
# -------------------------------------------------------------------
def send_whatsapp(to_number: str, message_body: str) -> str:
    to = (to_number or "").replace("whatsapp:", "").replace("+", "").strip()
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message_body},
    }
    try:
        r = requests.post(API_URL, headers=HEADERS, json=payload, timeout=15)
    except Exception as e:
        print(f"[META SEND] request error: {e}", flush=True)
        return ""

    print(f"[META SEND] status={r.status_code} to={to}", flush=True)
    print(f"[META SEND] resp={r.text}", flush=True)
    return r.text


# -------------------------------------------------------------------
# 2) INSTANCIAS DE COMPONENTES
# -------------------------------------------------------------------
postgresql_comercial = DataBasePostgreSQLComercialManager(db_uri=DB_URI, schema="comercial")
firestore = DataBaseFirestoreManager()
openai_mgr = OpenAIComercialManager()

def _pg_startup_ping():
    try:
        with postgresql_comercial.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SET search_path TO comercial;")
                cur.execute("SELECT 1;")
                ok = cur.fetchone()[0]
                print(f"[PG-COM] Startup ping OK={ok}", flush=True)
    except Exception as e:
        print(f"[PG-COM] Startup ping FAILED: {e}", flush=True)
_pg_startup_ping()


# -------------------------------------------------------------------
# 3) RAG (Elasticsearch + OpenAI embeddings)
# -------------------------------------------------------------------
db_query = ElasticsearchStore(
    es_url=ELASTIC_URL,
    es_user=ELASTIC_USER,
    es_password=ELASTIC_PASSWORD,
    index_name=ELASTIC_INDEX,
    embedding=OpenAIEmbeddings(),
)
retriever = db_query.as_retriever()

_raw_rag_tool = retriever.as_tool(
    name="consultar_informacion_rag",
    description=(
        "Busca y resume informacion de productos y servicios comerciales. "
        "Usala cuando el lead pregunte detalles sobre productos, precios, "
        "requisitos, beneficios, plazos, etc."
    ),
)

def _rag(query: str) -> str:
    print(f"[TOOL] consultar_informacion_rag | query: {query}", flush=True)
    return _raw_rag_tool.func(query)

consultar_informacion_rag = Tool(
    name="consultar_informacion_rag",
    func=_rag,
    description=_raw_rag_tool.description,
)


# -------------------------------------------------------------------
# 4) HERRAMIENTAS PRINCIPALES (leads comerciales)
# -------------------------------------------------------------------

# 4.1 Analizar mensaje del lead (core)
@tool(
    "analizar_mensaje_lead",
    description=(
        "Analiza el mensaje del lead para determinar sentimiento, nivel de interes "
        "e intencion de compra. Calcula y actualiza el scoring en BD. "
        "Retorna una respuesta lista para WhatsApp."
    ),
)
def analizar_mensaje_lead(payload: str) -> str:
    """
    payload: texto crudo del lead.
    Efecto: Analiza NLP, calcula scoring, actualiza bd_leads + hist_scoring + hist_conversaciones.
    Retorno: reply listo para WhatsApp.
    """
    print(f"[ANALIZAR] payload={payload!r}", flush=True)

    sender = getattr(g, "sender", None)
    lead = postgresql_comercial.buscar_o_crear_lead(sender)
    if not lead:
        return "Hubo un problema al procesar tu consulta. Intentalo de nuevo."

    id_lead = str(lead["id_lead"])
    score_antes = float(lead.get("scoring") or 0)
    contactos = int(lead.get("cantidad_contactos_lead_bot") or 0)

    # Tiempo de respuesta
    t_resp = postgresql_comercial.calcular_tiempo_respuesta(id_lead)
    tiempo_score = score_tiempo_respuesta(t_resp)

    # NLP con OpenAI (usando component_openai)
    nlp = openai_mgr.analizar_mensaje_nlp(payload)

    sentimiento      = nlp["sentimiento"]
    nivel_interes    = nlp["nivel_interes"]
    intencion_compra = nlp["intencion_compra"]
    keywords         = nlp["keywords"]
    reply            = nlp["reply"]
    derivar          = nlp["derivar"]

    print(f"[ANALIZAR] sent={sentimiento} int={nivel_interes} "
          f"comp={intencion_compra} derivar={derivar} t_resp={t_resp}", flush=True)

    # Calcular datos capturados del lead
    datos_cap = calcular_datos_capturados(lead)

    # Calcular scoring (usando help_helpers)
    config = postgresql_comercial.obtener_config_modelo()

    raw_score_val = calcular_raw_score(
        sentimiento=sentimiento,
        nivel_interes=nivel_interes,
        intencion_compra=intencion_compra,
        tiempo_score=tiempo_score,
        datos_capturados=datos_cap,
        config=config,
    )

    score_nuevo = calcular_score_ema(score_antes, raw_score_val, config)

    # Contactabilidad
    contactabilidad = calcular_contactabilidad(contactos)

    # Obtener valores numericos para BD
    sent_num = SENTIMIENTO_MAP.get(sentimiento, 0.5)
    int_num  = INTERES_MAP.get(nivel_interes, 0.0)
    comp_num = INTENCION_MAP.get(intencion_compra, 0.0)

    # Guardar en BD
    postgresql_comercial.actualizar_scoring_lead(
        id_lead=id_lead,
        scoring_nuevo=score_nuevo,
        sentimiento=sentimiento,
        nivel_interes=int_num,
        nivel_intencion_compra=comp_num,
        contactabilidad=contactabilidad,
        evento_trigger="respuesta_bot",
    )

    # Registrar mensaje inbound con metadata NLP
    postgresql_comercial.registrar_mensaje(
        id_lead=id_lead,
        direccion="inbound",
        contenido=payload or "",
        sentimiento=sentimiento,
        intencion=intencion_compra,
        keywords=keywords,
        score_antes=score_antes,
        score_despues=score_nuevo,
        tiempo_respuesta_seg=t_resp,
    )

    # Descarte automatico por rechazo explicito
    if debe_descartar_lead(intencion_compra, sentimiento, score_nuevo, config):
        postgresql_comercial.actualizar_estado_lead(id_lead, "descartado")
        print(f"[ANALIZAR] lead descartado por rechazo explicito score={score_nuevo}", flush=True)

    # Indicar derivacion si score alto
    if debe_derivar_a_asesor(score_nuevo, derivar, config):
        print(f"[ANALIZAR] score={score_nuevo}, sugerir derivacion", flush=True)

    return reply or ("Gracias por escribirnos. Cuentame, en que producto o servicio "
                     "estas interesado? Estoy aqui para ayudarte.")


# 4.2 Capturar datos personales del lead
@tool(
    "capturar_datos_lead",
    description=(
        "Extrae datos personales del lead (DNI, nombre, apellido, correo) "
        "desde el texto de la conversacion y los guarda en la base de datos."
    ),
)
def capturar_datos_lead(payload: str) -> str:
    """
    payload: texto del lead que puede contener datos personales.
    Efecto: Extrae y guarda DNI/nombre/apellido/correo en bd_leads.
    Retorno: confirmacion o solicitud de datos.
    """
    print(f"[CAPTURAR] payload={payload!r}", flush=True)

    # Usar component_openai para extraccion
    data = openai_mgr.extraer_datos_personales(payload)

    dni      = data.get("dni")
    nombre   = data.get("nombre")
    apellido = data.get("apellido")
    correo   = data.get("correo")

    if not any([dni, nombre, apellido, correo]):
        return "No detecte datos personales en tu mensaje. Podrias indicarme tu nombre completo y DNI?"

    sender = getattr(g, "sender", None)
    lead = postgresql_comercial.buscar_o_crear_lead(sender)
    if not lead:
        return "Hubo un problema al registrar tus datos. Intentalo de nuevo."

    id_lead = str(lead["id_lead"])
    ok = postgresql_comercial.actualizar_datos_personales_lead(
        id_lead=id_lead,
        dni=dni,
        nombre=nombre,
        apellido=apellido,
        correo=correo,
    )

    if not ok:
        return "Hubo un problema al guardar tus datos. Puedes intentar nuevamente?"

    partes = []
    if nombre:
        partes.append(f"nombre: {nombre}")
    if apellido:
        partes.append(f"apellido: {apellido}")
    if dni:
        partes.append(f"DNI: {dni}")
    if correo:
        partes.append(f"correo: {correo}")

    resumen = ", ".join(partes)
    return f"Datos registrados correctamente ({resumen}). Gracias!"


# 4.3 Derivar a asesor
@tool(
    "derivar_a_asesor",
    description=(
        "Deriva el lead al mejor asesor disponible cuando el score es alto "
        "o el lead solicita explicitamente hablar con un asesor. "
        "Crea la asignacion y notifica al lead."
    ),
)
def derivar_a_asesor(payload: str) -> str:
    """
    payload: motivo o contexto de la derivacion.
    Efecto: Busca asesor, crea asignacion, actualiza estado a 'prospecto'.
    Retorno: mensaje de confirmacion para el lead.
    """
    print(f"[DERIVAR] payload={payload!r}", flush=True)

    sender = getattr(g, "sender", None)
    lead = postgresql_comercial.buscar_o_crear_lead(sender)
    if not lead:
        return "Hubo un problema al procesar tu solicitud. Intentalo de nuevo."

    id_lead = str(lead["id_lead"])

    # Buscar asesor
    asesor = postgresql_comercial.buscar_mejor_asesor(id_lead)
    if not asesor:
        return ("En este momento no tenemos asesores disponibles. "
                "Te contactaremos a la brevedad. Gracias por tu paciencia!")

    id_asesor = str(asesor.get("id_asesor"))
    nombre_asesor = asesor.get("nombre_asesor", "un asesor especializado")
    id_matching = str(asesor.get("id_matching")) if asesor.get("id_matching") else None

    scores = {
        "score_total": asesor.get("score_total"),
        "score_k": asesor.get("score_k"),
        "score_c": asesor.get("score_c"),
        "score_v": asesor.get("score_v"),
        "score_p": asesor.get("score_p"),
    }

    id_asignacion = postgresql_comercial.crear_asignacion(
        id_lead=id_lead,
        id_asesor=id_asesor,
        id_matching=id_matching,
        scores=scores,
    )

    if not id_asignacion:
        return ("Hubo un problema al asignarte un asesor. "
                "Te contactaremos pronto de todas formas.")

    print(f"[DERIVAR] asignacion={id_asignacion} asesor={nombre_asesor}", flush=True)
    return (f"Te he asignado a {nombre_asesor}, quien se comunicara contigo "
            f"muy pronto para brindarte atencion personalizada. Gracias!")


# -------------------------------------------------------------------
# 5) PROMPT Y AGENTE
# -------------------------------------------------------------------
prompt = ChatPromptTemplate.from_messages(
    [
        ("system", prompt_sistema_agente()),
        ("human", "{messages}"),
    ]
)

# Memoria (checkpoint) con Postgres
connection_kwargs = {"autocommit": True, "prepare_threshold": 0}
pool = ConnectionPool(conninfo=DB_URI, max_size=20, kwargs=connection_kwargs)
checkpointer = PostgresSaver(pool)

model = ChatOpenAI(model="gpt-4.1-2025-04-14")

toolkit = [
    consultar_informacion_rag,
    analizar_mensaje_lead,
    capturar_datos_lead,
    derivar_a_asesor,
]

agent_executor = create_react_agent(
    model=model,
    tools=toolkit,
    checkpointer=checkpointer,
    prompt=prompt,
)


# -------------------------------------------------------------------
# 6) FORWARD A VERCEL
# -------------------------------------------------------------------
def forward_to_vercel(raw_meta_body=None, sender=None, message_text=None):
    try:
        if raw_meta_body:
            payload = raw_meta_body
        else:
            if not sender:
                return
            payload = {
                "object": "whatsapp_business_account",
                "entry": [{
                    "id": PHONE_NUMBER_ID,
                    "changes": [{
                        "field": "messages",
                        "value": {
                            "messages": [{
                                "from": sender,
                                "id": f"local_{uuid.uuid4().hex}",
                                "timestamp": str(int(time.time())),
                                "text": {"body": message_text or ""}
                            }]
                        }
                    }]
                }]
            }

        r = requests.post(VERCEL_WEBHOOK_URL, json=payload, timeout=3)
        print(f"[VERCEL] status={r.status_code} body={r.text[:200]}", flush=True)
    except Exception as e:
        print(f"[VERCEL] forward error: {e}", flush=True)


# -------------------------------------------------------------------
# 7) FLASK APP (Webhook Meta)
# -------------------------------------------------------------------
app = Flask(__name__)

@app.route("/webhook_comercial", methods=["GET", "POST"])
def main():
    # Verificacion webhook
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == VERIFY_TOKEN:
            return Response(challenge, status=200, mimetype="text/plain")
        return "Error de verificacion", 403

    # Evento entrante
    if request.is_json:
        data = request.get_json()

        # Forward a Vercel
        threading.Thread(
            target=forward_to_vercel, args=(data, None, None), daemon=True
        ).start()

        try:
            change = data["entry"][0]["changes"][0]
            value = change.get("value", {})

            # Captura de estados de entrega/lectura
            statuses = value.get("statuses", [])
            if statuses:
                for st in statuses:
                    status = st.get("status")
                    print(f"[STATUS] {st.get('id')} -> {status}", flush=True)
                return Response("EVENT_RECEIVED", status=200)

            messages = value.get("messages", [])
            if not messages:
                return Response("EVENT_RECEIVED", status=200)
            msg = messages[0]
            incoming_msg = (msg.get("text") or {}).get("body", "")
            sender = msg.get("from", "")
        except Exception as e:
            print(f"Parse JSON Meta error: {e} | data={data}", flush=True)
            return Response("EVENT_RECEIVED", status=200)
    else:
        incoming_msg = request.form.get("Body", "").strip()
        sender       = request.form.get("From", "").replace("whatsapp:", "").strip()

    # Sender como contexto
    g.sender = sender
    print(f"[DEBUG] IN: {incoming_msg} FROM: {sender}", flush=True)

    # Buscar o crear lead
    lead = postgresql_comercial.buscar_o_crear_lead(sender)
    if lead:
        g.lead_id = str(lead["id_lead"])

    # Log en Firestore
    try:
        firestore.crear_documento(sender, g.lead_id if lead else None, "comercial", incoming_msg, True)
    except Exception as e:
        print(f"Firestore IN error: {e}", flush=True)

    # Invocar agente
    try:
        thread_id = f"com-{sender}"
        config = {"configurable": {"thread_id": thread_id}}
        result = agent_executor.invoke(
            {"messages": [HumanMessage(content=(incoming_msg or " "))]},
            config=config,
        )
        response_text = result["messages"][-1].content
    except Exception as e:
        print(f"Error al invocar agente: {e}", flush=True)
        response_text = "Hubo un problema al procesar tu solicitud. Puedes intentar nuevamente?"

    # Registrar outbound en hist_conversaciones
    if lead:
        postgresql_comercial.registrar_mensaje(
            id_lead=str(lead["id_lead"]),
            direccion="outbound",
            contenido=response_text,
        )

    # Log OUT en Firestore
    try:
        firestore.crear_documento(sender, g.lead_id if lead else None, "comercial", response_text, False)
    except Exception as e:
        print(f"Firestore OUT error: {e}", flush=True)

    send_whatsapp(sender, response_text)
    return Response("EVENT_RECEIVED", status=200)


# -------------------------------------------------------------------
# 8) RUN LOCAL
# -------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)
