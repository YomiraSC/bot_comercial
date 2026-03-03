# Bot Comercial de Leads — Explicacion Completa

## Que hace este bot?

Es un bot de WhatsApp que **captura y nutre leads comerciales**. Cuando un potencial cliente escribe, el bot:

1. Responde sus dudas sobre productos/servicios
2. Analiza cada mensaje para medir que tan interesado esta
3. Le asigna un **score** (puntaje de 0 a 1) que sube o baja con cada interaccion
4. Cuando el score es suficientemente alto, lo deriva a un asesor humano

---

## Arquitectura General

```
WhatsApp (Meta Cloud API)
        |
        v
   Flask Webhook (/webhook_comercial)
        |
        v
   LangGraph Agent (GPT-4)
        |
        +--> analizar_mensaje_lead   --> Scoring + NLP + Reply
        +--> consultar_informacion_rag --> Busqueda en Elasticsearch
        +--> capturar_datos_lead     --> Extrae DNI/nombre/correo
        +--> derivar_a_asesor        --> Asigna asesor al lead
        |
        v
   PostgreSQL (esquema "comercial")   +   Firestore (logs)
        |
        v
   Respuesta por WhatsApp al lead
```

---

## Archivo 1: `app_comercial.py`

### Seccion 0 — Configuracion (lineas 1-62)

Carga todas las credenciales y variables de entorno:

| Variable | Para que sirve |
|----------|---------------|
| `OPENAI_API_KEY` | Autenticacion con GPT-4 (LangChain) |
| `WHATSAPP_TOKEN` | Token de acceso a la API de Meta WhatsApp |
| `PHONE_NUMBER_ID` | ID del numero de WhatsApp Business comercial |
| `VERIFY_TOKEN` | Token para verificar el webhook con Meta |
| `DB_URI` | Cadena de conexion a PostgreSQL en GCP |
| `ELASTIC_*` | Credenciales de Elasticsearch para el RAG |

**Importante:** Las lineas 39-40 dicen `TU_WHATSAPP_TOKEN_COMERCIAL` — hay que reemplazar con los tokens reales del numero comercial.

---

### Seccion 1 — send_whatsapp (lineas 68-84)

Funcion que envia un mensaje de texto por WhatsApp usando la API de Meta.

```
send_whatsapp("5199xxxxxxx", "Hola! En que te puedo ayudar?")
```

Flujo:
1. Limpia el numero (quita `whatsapp:`, `+`, espacios)
2. Arma el payload JSON de Meta
3. Hace POST a `graph.facebook.com`
4. Si falla, imprime error pero no rompe el bot

---

### Seccion 2 — Instancias de componentes (lineas 87-103)

Crea las conexiones principales al arrancar:

- `postgresql_comercial` — Manager de PostgreSQL para el esquema `comercial`
- `firestore` — Manager de Firestore para logs de mensajes
- `_pg_startup_ping()` — Verifica que PostgreSQL este accesible al iniciar

---

### Seccion 3 — RAG con Elasticsearch (lineas 106-135)

**RAG = Retrieval Augmented Generation** (Generacion aumentada con recuperacion)

Esto permite que el bot busque informacion real de productos en una base de documentos, en vez de inventar respuestas.

Flujo:
1. Se conecta a Elasticsearch (indice `comercial-v1`)
2. Usa embeddings de OpenAI para convertir la pregunta del lead en un vector
3. Busca los documentos mas similares en el indice
4. Retorna los fragmentos relevantes al agente

Ejemplo: Si el lead pregunta "cuanto cuesta el seguro de vida?", el RAG busca en los documentos y retorna la info real.

---

### Seccion 4 — Herramientas del Agente (lineas 138-440)

El agente tiene **4 herramientas**. Cada una es una funcion que el agente puede decidir llamar segun el contexto del mensaje.

#### 4.0 — Mapeos de Scoring (lineas 142-157)

Convierten las etiquetas de texto a numeros para el calculo:

```
Sentimiento:  positivo=1.0  |  neutral=0.5  |  negativo=0.0
Interes:      alto=1.0      |  medio=0.5    |  bajo=0.0
Intencion:    alta=1.0      |  media=0.5    |  baja=0.0

Tiempo de respuesta:
  < 2 min    = 1.0  (muy enganchado)
  2-10 min   = 0.7  (interes medio)
  10-60 min  = 0.3  (bajo)
  > 60 min   = 0.0  (penalizacion fuerte)
```

---

#### 4.1 — `analizar_mensaje_lead` (lineas 160-302)

**Es la herramienta mas importante.** Se ejecuta con casi cada mensaje del lead.

Paso a paso:

1. **Busca/crea el lead** en la BD por numero de telefono
2. **Calcula tiempo de respuesta** — cuantos segundos pasaron desde el ultimo mensaje del bot
3. **Llama a GPT-4** con un prompt NLP que le pide clasificar el mensaje en:
   - `sentimiento`: positivo / neutral / negativo
   - `nivel_interes`: alto / medio / bajo
   - `intencion_compra`: alta / media / baja
   - `keywords`: palabras clave del mensaje
   - `reply`: respuesta para enviar al lead
   - `derivar`: true/false (si debe pasar a asesor)

4. **Calcula el scoring** con esta formula:

```
raw_score = 0.25 * sentimiento + 0.30 * interes + 0.15 * tiempo + 0.30 * intencion

score_nuevo = score_anterior * 0.60 + raw_score * 0.40
```

Esto es un **EMA (Exponential Moving Average)**: el score no cambia bruscamente, sino que va acumulando historia. El `alpha=0.40` significa que el 40% del score viene de este mensaje y el 60% del historial.

5. **Guarda todo en la BD**:
   - Actualiza `bd_leads` (scoring, sentimiento, interes, etc.)
   - Inserta en `hist_scoring` (registro historico del cambio)
   - Inserta en `hist_conversaciones` (el mensaje con metadata NLP)

6. **Acciones automaticas**:
   - Si `intencion=baja` + `sentimiento=negativo` + `score < 0.10` → descarta el lead
   - Si `score >= 0.75` o `derivar=true` → imprime log de derivacion (el agente puede llamar a `derivar_a_asesor`)

7. **Retorna el reply** generado por GPT-4

---

#### 4.2 — `capturar_datos_lead` (lineas 305-382)

Extrae datos personales del texto del lead y los guarda.

Ejemplo: Si el lead dice *"Me llamo Juan Perez, mi DNI es 12345678"*, esta herramienta:

1. Llama a GPT-4 con prompt de extraccion → retorna `{"dni": "12345678", "nombre": "Juan", "apellido": "Perez", "correo": null}`
2. Actualiza los campos no-null en `bd_leads`
3. Retorna confirmacion: *"Datos registrados correctamente (nombre: Juan, apellido: Perez, DNI: 12345678)"*

Si no detecta datos, pide amablemente que los proporcione.

---

#### 4.3 — `derivar_a_asesor` (lineas 385-440)

Asigna al lead con un asesor humano. Se activa cuando:
- El lead pide hablar con alguien
- El score supera el umbral de 0.75

Flujo:
1. Busca el mejor asesor en la tabla `matching` (score pre-calculado)
2. Si no hay matching, usa fallback: asesor disponible con menor cola
3. Crea registro en `hist_asignaciones`
4. Actualiza el lead: `estado_de_lead = 'prospecto'`, `ultimo_asesor_asignado = id_asesor`
5. Incrementa `leads_en_cola` del asesor
6. Retorna mensaje: *"Te he asignado a {nombre_asesor}, quien se comunicara contigo..."*

---

### Seccion 5 — Prompt y Agente (lineas 443-492)

El **prompt del sistema** le dice al agente:
- Que es el asistente comercial de Maqui+
- Cuando usar cada herramienta
- Que nunca mencione cosas internas (scoring, herramientas)
- Que use el reply de las herramientas tal cual
- Que no invente info de productos (usar RAG)

El **agente** se arma con:
- `create_react_agent` de LangGraph (patron ReAct: razona y actua)
- Modelo: `gpt-4.1-2025-04-14`
- Checkpointer: `PostgresSaver` (guarda historial de conversacion por lead)
- Thread ID: `com-{telefono}` (cada lead tiene su propio hilo de memoria)

---

### Seccion 6 — Forward a Vercel (lineas 495-526)

Copia opcional del webhook hacia una app en Vercel (CRM). Se ejecuta en un hilo separado para no bloquear la respuesta.

---

### Seccion 7 — Webhook Flask (lineas 529-622)

Esta es la ruta principal que recibe los mensajes de Meta.

**GET** `/webhook_comercial` → Verificacion del webhook (Meta lo llama una vez para confirmar).

**POST** `/webhook_comercial` → Mensaje entrante. Flujo:

```
1. Parsear JSON de Meta
2. Si es evento de status (delivered/read) → log y return 200
3. Si no hay mensajes → return 200
4. Extraer texto y numero del remitente
5. Buscar o crear lead en BD
6. Log en Firestore (mensaje entrante)
7. Invocar agente LangGraph
   - El agente decide que herramienta(s) usar
   - Retorna respuesta final
8. Si falla el agente → respuesta fallback
9. Registrar respuesta en hist_conversaciones (outbound)
10. Log en Firestore (mensaje saliente)
11. Enviar respuesta por WhatsApp
12. Return 200
```

**Importante:** El `send_whatsapp` (paso 11) se ejecuta SIEMPRE, sin importar que haya pasado antes. El lead siempre recibe respuesta.

---

### Seccion 8 — Run Local (lineas 625-629)

Arranca Flask en el puerto `8081` (diferente al bot de reactivaciones que usa `8080`).

---

## Archivo 2: `component_postgresql_comercial.py`

Manager de base de datos que encapsula todas las operaciones SQL sobre el esquema `comercial`.

### Helpers internos

| Metodo | Que hace |
|--------|----------|
| `_set_schema(cur)` | Ejecuta `SET search_path TO comercial;` en cada query |
| `_row_to_dict(cur, row)` | Convierte una fila de BD en diccionario Python |
| `_norm_tel(raw)` | Normaliza telefono a formato `+5199xxxxxxx` |

### Metodos de Leads

| Metodo | Tabla | Que hace |
|--------|-------|----------|
| `buscar_lead_por_numero(celular)` | bd_leads | Busca lead por telefono |
| `crear_lead_minimo(celular)` | bd_leads | Crea lead nuevo con score=0, origen=inbound |
| `buscar_o_crear_lead(celular)` | bd_leads | Wrapper: busca primero, crea si no existe |

### Metodos de Scoring

| Metodo | Tablas | Que hace |
|--------|--------|----------|
| `actualizar_scoring_lead(...)` | bd_leads + hist_scoring | Lee score anterior, actualiza bd_leads con nuevo score/sentimiento/interes, inserta registro en hist_scoring con delta |

Dentro de una sola transaccion:
1. `SELECT scoring FROM bd_leads` (score anterior)
2. `UPDATE bd_leads SET scoring, sentimiento, interes, contactabilidad, ...`
3. `INSERT INTO hist_scoring (anterior, nuevo, delta, evento, ...)`

### Metodos de Datos Personales

| Metodo | Tabla | Que hace |
|--------|-------|----------|
| `actualizar_datos_personales_lead(id, dni, nombre, apellido, correo)` | bd_leads | UPDATE dinamico — solo actualiza campos no-null |
| `actualizar_estado_lead(id, estado)` | bd_leads | Cambia estado: lead → prospecto → venta / descartado |

### Metodos de Conversaciones

| Metodo | Tabla | Que hace |
|--------|-------|----------|
| `registrar_mensaje(...)` | hist_conversaciones | Inserta mensaje (inbound u outbound) con metadata NLP, keywords JSONB, scores |

### Metodos de Tiempo de Respuesta

| Metodo | Tablas | Que hace |
|--------|--------|----------|
| `obtener_ultimo_outbound_timestamp(id_lead)` | hist_conversaciones | Busca timestamp del ultimo mensaje del bot |
| `calcular_tiempo_respuesta(id_lead)` | hist_conversaciones + bd_leads | Calcula segundos desde ultimo outbound, actualiza promedio movil |

La formula del promedio:
```
nuevo_promedio = (promedio_actual * contactos + tiempo_nuevo) / (contactos + 1)
```

### Metodos de Asesores

| Metodo | Tablas | Que hace |
|--------|--------|----------|
| `buscar_mejor_asesor(id_lead)` | matching + bd_asesores | 1ro busca en matching por score, fallback por menor cola |
| `crear_asignacion(...)` | hist_asignaciones + bd_leads + bd_asesores + matching | Inserta asignacion, cambia estado a prospecto, incrementa cola, marca matching |

### Metodo de Configuracion

| Metodo | Tabla | Que hace |
|--------|-------|----------|
| `obtener_config_modelo()` | config_modelo | Lee pesos y umbrales, con cache de 5 minutos |

Los pesos se leen de BD para poder ajustarlos sin reiniciar el bot:
```
w_sentimiento = 0.25
w_interes     = 0.30
w_tiempo      = 0.15
w_intencion   = 0.30
alpha_ema     = 0.40
umbral_derivacion = 0.75
```

---

## Formula de Scoring — Ejemplo Completo

Supongamos un lead con `score_anterior = 0.30` que responde en 1 minuto con: *"Si, quiero cotizar el seguro de vida, cuanto cuesta la cuota mensual?"*

GPT-4 clasifica:
- sentimiento = **positivo** → 1.0
- interes = **alto** → 1.0
- intencion = **alta** → 1.0
- tiempo = 60 seg → 1.0

```
raw_score = 0.25*1.0 + 0.30*1.0 + 0.15*1.0 + 0.30*1.0 = 1.0

score_nuevo = 0.30 * 0.60 + 1.0 * 0.40 = 0.18 + 0.40 = 0.58
```

El score sube de 0.30 a 0.58. Con otro mensaje positivo similar:
```
score_nuevo = 0.58 * 0.60 + 1.0 * 0.40 = 0.348 + 0.40 = 0.748
```

Ya supera el umbral de 0.75 → se sugiere derivacion a asesor.

---

## Tablas de BD que usa el bot

```
comercial.bd_leads              ← Lead principal (score, estado, datos personales)
comercial.hist_conversaciones   ← Cada mensaje in/out con metadata NLP
comercial.hist_scoring          ← Historial de cambios de score
comercial.hist_asignaciones     ← Asignaciones lead-asesor
comercial.bd_asesores           ← Asesores disponibles
comercial.matching              ← Scores pre-calculados lead-asesor
comercial.config_modelo         ← Pesos y umbrales configurables
```

---

## Ciclo de vida de un lead

```
[Lead nuevo]  score=0, estado="lead"
     |
     v
[Mensajes]  score sube/baja con cada interaccion
     |
     +-- score < 0.10 + negativo --> estado="descartado"
     |
     +-- score >= 0.75 o pide asesor --> derivar_a_asesor
                                              |
                                              v
                                   estado="prospecto"
                                   asignado a asesor
                                              |
                                              v
                                   (asesor cierra venta)
                                   estado="venta"
```

---

## Estructura completa de archivos

El proyecto tiene la misma estructura que el bot de reactivaciones, con cada responsabilidad separada en su propio archivo:

```
bot_comercial/
  app_comercial.py                  ← App Flask principal (webhook + agente + tools)
  component_postgresql_comercial.py ← Manager PostgreSQL (esquema comercial)
  component_firestore.py            ← Manager Firestore (coleccion "comercial")
  component_openai.py               ← Manager OpenAI (analisis NLP de leads)
  help_prompt.py                    ← Todos los prompts del sistema
  help_helpers.py                   ← Utilidades de scoring y formateo
  api_keys.py                       ← Clave de OpenAI
  comercial_schema_final.sql        ← DDL de la BD (referencia)
  EXPLICACION_BOT_COMERCIAL.md      ← Este archivo
```

### Comparacion con el bot de reactivaciones

| Reactivaciones | Comercial | Funcion |
|---|---|---|
| `app.py` | `app_comercial.py` | App Flask + webhook + agente |
| `component_postgresql.py` | `component_postgresql_comercial.py` | Operaciones PostgreSQL |
| `component_firestore.py` | `component_firestore.py` | Logs de mensajes en Firestore |
| `component_openai.py` | `component_openai.py` | Llamadas a GPT-4 |
| `help_prompt.py` | `help_prompt.py` | Prompts del sistema |
| `help_helpers.py` | `help_helpers.py` | Funciones utilitarias |
| `api_keys.py` | `api_keys.py` | Claves de API |

---

## Archivo 3: `component_firestore.py`

Manager de Firestore adaptado para el bot comercial. Usa la coleccion `"comercial"` (en vez de `"reactivaciones"`).

### Clase: `DataBaseFirestoreManager`

| Metodo | Que hace |
|--------|----------|
| `_connect()` | Establece conexion con Firestore |
| `_reconnect_if_needed()` | Verifica conexion y reconecta si se perdio |
| `crear_documento(celular, id_lead, id_bot, mensaje, sender)` | Crea documento en coleccion `comercial`. `sender=True` si es del lead, `False` si es del bot |
| `recuperar_mensajes_hoy(id_bot, celular)` | Recupera mensajes del dia para un lead (zona Lima) |
| `recuperar_mensajes_hoy_alt(id_bot, celular)` | Version alternativa usando UTC |

Diferencias con el de reactivaciones:
- Coleccion: `"comercial"` en vez de `"reactivaciones"`
- Campo: `id_lead` en vez de `id_cliente`
- El nombre de coleccion se define en `self.collection_name` (facil de cambiar)

---

## Archivo 4: `component_openai.py`

Manager de OpenAI con metodos especializados para analisis NLP de leads comerciales.

### Clase: `OpenAIComercialManager`

| Metodo | Que hace | Retorna |
|--------|----------|---------|
| `analizar_mensaje_nlp(mensaje)` | Clasifica sentimiento, interes e intencion del mensaje | `{"sentimiento": "...", "nivel_interes": "...", "intencion_compra": "...", "keywords": [...], "reply": "...", "derivar": bool}` |
| `extraer_datos_personales(mensaje)` | Extrae DNI, nombre, apellido y correo del texto | `{"dni": "..." o null, "tipo_documento": "...", "nombre": "...", "apellido": "...", "correo": "..."}` |
| `obtener_dni_brindado(mensaje)` | Extrae DNI/RUC/CE de la conversacion | `{"tipo": "DNI", "numero": "12345678"}` o `None` |
| `generar_respuesta_inicio_conversacion()` | Mensaje de bienvenida del bot | String de bienvenida |

Cada metodo tiene fallback: si GPT-4 falla o retorna JSON invalido, devuelve valores por defecto (neutral/bajo/baja) en vez de romper.

Diferencias con el de reactivaciones:
- Reactivaciones tiene metodos para: clasificar intenciones de pago, generar codigos, manejar contratos
- Comercial tiene metodos para: analisis NLP con scoring, extraccion de datos personales, deteccion de intencion de compra
- Comercial usa `gpt-4.1-2025-04-14` como modelo por defecto
- Comercial incluye `limpiar_json_llm()` para quitar bloques ````json` de respuestas

---

## Archivo 5: `help_prompt.py`

Contiene todos los prompts del sistema centralizados. Esto permite modificar el comportamiento del bot sin tocar la logica de `app_comercial.py`.

| Funcion | Donde se usa | Que hace |
|---------|-------------|----------|
| `prompt_analisis_nlp_lead()` | `component_openai.py` → `analizar_mensaje_nlp()` | Prompt que clasifica sentimiento, interes e intencion y genera reply |
| `prompt_extraccion_datos_personales()` | `component_openai.py` → `extraer_datos_personales()` | Prompt que extrae DNI/nombre/apellido/correo del texto |
| `prompt_sistema_agente()` | `app_comercial.py` → seccion 5 | Prompt de sistema del agente LangGraph (define personalidad, reglas, cuando usar herramientas) |
| `prompt_inicio_conversacion_comercial()` | `component_openai.py` → `generar_respuesta_inicio()` | Mensaje de bienvenida del bot |
| `prompt_obtener_dni(conversation_text)` | `component_openai.py` → `obtener_dni_brindado()` | Extrae DNI/RUC/CE de conversacion completa |

Diferencias con el de reactivaciones:
- Reactivaciones tiene prompts para: clasificar intenciones de pago (5 tipos), generar codigos, pedir eleccion de contrato
- Comercial tiene prompts para: analisis NLP multivariable (sentimiento + interes + intencion), extraccion de datos, nurturing de leads

---

## Archivo 6: `help_helpers.py`

Funciones utilitarias y constantes de scoring. Separa la logica de calculo de `app_comercial.py` para mantenerlo limpio.

### Constantes

| Constante | Contenido |
|-----------|-----------|
| `SENTIMIENTO_MAP` | `{"positivo": 1.0, "neutral": 0.5, "negativo": 0.0}` |
| `INTERES_MAP` | `{"alto": 1.0, "medio": 0.5, "bajo": 0.0}` |
| `INTENCION_MAP` | `{"alta": 1.0, "media": 0.5, "baja": 0.0}` |
| `PESOS_DEFAULT` | Pesos por defecto si `config_modelo` no esta disponible |

### Funciones de Scoring

| Funcion | Que hace |
|---------|----------|
| `score_tiempo_respuesta(segundos)` | Convierte segundos a score: <2min=1.0, 2-10min=0.7, 10-60min=0.3, >60min=0.0 |
| `calcular_raw_score(sentimiento, interes, intencion, tiempo, config)` | Calcula score ponderado: `w_s*S + w_i*I + w_t*T + w_c*C` |
| `calcular_score_ema(score_antes, raw_score, config)` | Aplica EMA: `score_ant*(1-alpha) + raw*alpha` |
| `calcular_contactabilidad(contactos)` | `min(1.0, (contactos+1)/10)` |

### Funciones de Decision

| Funcion | Que hace |
|---------|----------|
| `debe_descartar_lead(intencion, sentimiento, score)` | `True` si intencion=baja + sentimiento=negativo + score<0.10 |
| `debe_derivar_a_asesor(score, derivar_flag, config)` | `True` si score>=umbral o LLM indico derivar=true |

### Funciones de Formato

| Funcion | Que hace |
|---------|----------|
| `formatear_conversacion(mensajes)` | Formatea lista de Firestore en texto `Lead: ... / Asistente: ...` |
| `limpiar_json_llm(texto)` | Quita bloques ````json``` de respuestas de GPT-4 |

Diferencias con el de reactivaciones:
- Reactivaciones tiene: `agregar_coma_al_dni()`, `quitar_coma_al_dni()`, `formatear_conversacion()` (3 funciones simples)
- Comercial tiene: 10 funciones enfocadas en scoring, decisiones automaticas y formato

---

## Como se conectan todos los archivos

```
app_comercial.py  (orquestador principal)
    |
    +-- importa --> api_keys.py                       (clave OpenAI)
    +-- importa --> component_postgresql_comercial.py  (operaciones BD)
    +-- importa --> component_firestore.py             (logs Firestore)
    +-- importa --> component_openai.py                (llamadas GPT-4)
    |                   |
    |                   +-- importa --> help_prompt.py  (prompts)
    |                   +-- importa --> help_helpers.py (limpiar JSON)
    |                   +-- importa --> api_keys.py     (clave OpenAI)
    |
    +-- importa --> help_prompt.py                     (prompt del agente)
    +-- importa --> help_helpers.py                    (scoring, mapeos, decisiones)
```

Flujo de un mensaje entrante:

```
1. WhatsApp envia POST a /webhook_comercial
2. app_comercial.py parsea el mensaje
3. app_comercial.py llama a component_firestore.py para log
4. app_comercial.py invoca el agente LangGraph
5. El agente decide llamar a analizar_mensaje_lead (tool)
6. El tool llama a component_openai.py → analizar_mensaje_nlp()
   - component_openai.py usa help_prompt.py para el prompt
   - component_openai.py usa help_helpers.py para limpiar JSON
7. El tool usa help_helpers.py para calcular scoring
8. El tool llama a component_postgresql_comercial.py para guardar
9. El tool retorna el reply
10. app_comercial.py registra outbound en PostgreSQL y Firestore
11. app_comercial.py envia respuesta por WhatsApp
```

---

## Archivo 7: `api_keys.py`

Archivo simple con la clave de OpenAI. Debe reemplazarse con la clave real antes de ejecutar.

```python
openai_api_key = "TU_OPENAI_API_KEY"
```

Este archivo NO se sube a git (deberia estar en `.gitignore`).
