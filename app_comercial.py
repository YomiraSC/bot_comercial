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

# Componentes
from api_keys import openai_api_key
from comercial.bot_comercial.component_postgresql_comercial import DataBasePostgreSQLComercialManager
from component_firestore import DataBaseFirestoreManager

# Vercel mirror
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

# --- Mapeos de scoring ---
SENTIMIENTO_MAP = {"positivo": 1.0, "neutral": 0.5, "negativo": 0.0}
INTERES_MAP     = {"alto": 1.0, "medio": 0.5, "bajo": 0.0}
INTENCION_MAP   = {"alta": 1.0, "media": 0.5, "baja": 0.0}

def _score_tiempo_respuesta(segundos: Optional[int]) -> float:
    if segundos is None:
        return 0.5  # primera interaccion, neutral
    if segundos <= 120:
        return 1.0   # < 2 min: alto engagement
    elif segundos <= 600:
        return 0.7   # 2-10 min: medio
    elif segundos <= 3600:
        return 0.3   # 10-60 min: bajo
    else:
        return 0.0   # > 60 min: penalizacion fuerte


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
    tiempo_score = _score_tiempo_respuesta(t_resp)

    # NLP con GPT-4
    llm = ChatOpenAI(model="gpt-4.1-2025-04-14", temperature=0.2)

    system = SystemMessage(content="""
Eres un servicio de ANALISIS NLP + RESPUESTA para un bot de ventas comerciales en WhatsApp.
DEVUELVE SOLO UN JSON VALIDO:

{
  "sentimiento": "<positivo|neutral|negativo>",
  "nivel_interes": "<alto|medio|bajo>",
  "intencion_compra": "<alta|media|baja>",
  "keywords": ["keyword1", "keyword2"],
  "reply": "<mensaje de 1-3 oraciones para WhatsApp, empatico y orientado a venta>",
  "derivar": <true|false>
}

Criterios de clasificacion:
- sentimiento positivo: palabras de aprobacion, entusiasmo, agradecimiento, apertura a avanzar.
- sentimiento neutral: preguntas informativas, respuestas cortas tipo "ok", "ya", "info".
- sentimiento negativo: quejas, rechazo, frustracion, desinteres explicito.

- interes alto: pregunta precios, cotizacion, cuotas, tasas, requisitos para aplicar/comprar.
- interes medio: compara beneficios, detalles del producto, tiempos de entrega o proceso.
- interes bajo: respuestas monosilabicas, "solo estoy viendo", preguntas generales vagas.

- intencion_compra alta: declara decision de comprar/solicitar, pide hablar con asesor, quiere agendar cita, define preferencias concretas (marca/modelo/zona/tipo).
- intencion_compra media: muestra interes sostenido pero no confirma, esta cerca de agendar.
- intencion_compra baja: rechaza, evita dar datos, pide tiempo indefinido, dice "no me interesa", abandono.

Redaccion del "reply":
- Maximo 3 oraciones, tono cercano y profesional orientado a venta.
- Si interes alto: propon paso concreto (cotizacion, demo, asesor).
- Si interes medio: ofrece 1 beneficio clave y pregunta que destrabe.
- Si interes bajo: no presiones, ofrece valor y deja puerta abierta.
- Si negativo fuerte: agradece y respeta, sugiere contacto futuro si cambia de opinion.
- Si derivar=true: indica que un asesor especializado lo contactara.
    """)

    raw = llm.invoke([system, HumanMessage(content=payload or "")]).content
    print(f"[ANALIZAR] raw_json={raw!r}", flush=True)

    try:
        data = json.loads(raw)
    except Exception as e:
        print(f"[ANALIZAR] JSON invalido ({e}); fallback.", flush=True)
        return ("Gracias por escribirnos. Estoy aqui para ayudarte. "
                "Cuentame, que producto o servicio te interesa?")

    sentimiento      = (data.get("sentimiento") or "neutral").strip().lower()
    nivel_interes    = (data.get("nivel_interes") or "bajo").strip().lower()
    intencion_compra = (data.get("intencion_compra") or "baja").strip().lower()
    keywords         = data.get("keywords") or []
    reply            = (data.get("reply") or "").strip()
    derivar          = bool(data.get("derivar"))

    print(f"[ANALIZAR] sent={sentimiento} int={nivel_interes} "
          f"comp={intencion_compra} derivar={derivar} t_resp={t_resp}", flush=True)

    # Calcular scoring
    config = postgresql_comercial.obtener_config_modelo()
    w_s = float(config.get("w_sentimiento", "0.25"))
    w_i = float(config.get("w_interes", "0.30"))
    w_t = float(config.get("w_tiempo", "0.15"))
    w_c = float(config.get("w_intencion", "0.30"))
    alpha = float(config.get("alpha_ema", "0.40"))
    umbral = float(config.get("umbral_derivacion", "0.75"))

    sent_num = SENTIMIENTO_MAP.get(sentimiento, 0.5)
    int_num  = INTERES_MAP.get(nivel_interes, 0.0)
    comp_num = INTENCION_MAP.get(intencion_compra, 0.0)

    raw_score = w_s * sent_num + w_i * int_num + w_t * tiempo_score + w_c * comp_num
    score_nuevo = score_antes * (1 - alpha) + raw_score * alpha
    score_nuevo = max(0.0, min(1.0, round(score_nuevo, 4)))

    # Contactabilidad
    contactabilidad = round(min(1.0, (contactos + 1) / 10.0), 2)

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
    if intencion_compra == "baja" and sentimiento == "negativo" and score_nuevo < 0.10:
        postgresql_comercial.actualizar_estado_lead(id_lead, "descartado")
        print(f"[ANALIZAR] lead descartado por rechazo explicito score={score_nuevo}", flush=True)

    # Indicar derivacion si score alto
    if derivar or score_nuevo >= umbral:
        print(f"[ANALIZAR] score={score_nuevo} >= umbral={umbral}, sugerir derivacion", flush=True)

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

    llm = ChatOpenAI(model="gpt-4.1-2025-04-14", temperature=0)

    system = SystemMessage(content="""
Extrae datos personales del siguiente mensaje. Devuelve SOLO un JSON:

{
  "dni": "<8 digitos si DNI, 11 si RUC, 9 si CE, o null si no se menciona>",
  "nombre": "<nombre si se menciona, o null>",
  "apellido": "<apellido si se menciona, o null>",
  "correo": "<email si se menciona, o null>"
}

Solo extrae datos explicitamente mencionados. No inventes ni deduzcas.
Si no hay ningun dato personal, retorna todos los campos como null.
    """)

    raw = llm.invoke([system, HumanMessage(content=payload or "")]).content
    print(f"[CAPTURAR] raw_json={raw!r}", flush=True)

    try:
        data = json.loads(raw)
    except Exception as e:
        print(f"[CAPTURAR] JSON invalido ({e})", flush=True)
        return "No pude identificar datos personales. Podrias indicarme tu nombre y DNI?"

    dni     = data.get("dni")
    nombre  = data.get("nombre")
    apellido = data.get("apellido")
    correo  = data.get("correo")

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
        ("system",
         """
Eres el asistente comercial de **Maqui+**. Tu rol es nutrir leads y guiarlos hacia una venta.
Responde en 1-3 oraciones, estilo WhatsApp, claro, empatico y orientado a generar interes.

Cuando usar herramientas:
1) Si el lead explica su situacion, hace preguntas, expresa interes o rechazo -> llama SIEMPRE a `analizar_mensaje_lead`.
2) Si el lead pide informacion sobre productos, precios, requisitos, beneficios -> usa `consultar_informacion_rag`.
3) Si el lead proporciona datos personales (DNI, nombre, correo) en su mensaje -> usa `capturar_datos_lead`.
4) Si el lead pide explicitamente hablar con un asesor, o si `analizar_mensaje_lead` indica derivar -> usa `derivar_a_asesor`.

Reglas:
- NUNCA menciones herramientas, scoring, clasificaciones internas ni variables de analisis.
- **Si una herramienta devuelve un 'reply', usalo tal cual como tu respuesta final (no agregues nada extra).**
- Tono: cercano, profesional, proactivo. Busca siempre avanzar la conversacion hacia el cierre.
- Si el usuario inicia con "hola" sin contexto, presentate brevemente y pregunta en que producto esta interesado.
- Si detectas que el lead ya tiene datos faltantes (DNI, nombre), intenta obtenerlos de forma natural sin ser invasivo.
- Si el lead dice "no me interesa" o similares, respeta su decision, agradece y ofrece contacto futuro.
- NUNCA inventes informacion de productos. Si no sabes, usa `consultar_informacion_rag`.
- Cuando derives a un asesor, confirma al lead que alguien se comunicara pronto.
         """),
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
        firestore.crear_documento(sender, None, "comercial", incoming_msg, True)
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
        firestore.crear_documento(sender, None, "comercial", response_text, False)
    except Exception as e:
        print(f"Firestore OUT error: {e}", flush=True)

    send_whatsapp(sender, response_text)
    return Response("EVENT_RECEIVED", status=200)


# -------------------------------------------------------------------
# 8) RUN LOCAL
# -------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)
