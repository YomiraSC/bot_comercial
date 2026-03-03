# component_postgresql_comercial.py — Esquema comercial
import os
import re
import json
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from psycopg_pool import ConnectionPool


class DataBasePostgreSQLComercialManager:
    """
    Manager para el esquema 'comercial' en PostgreSQL.
    Tablas: bd_leads, bd_asesores, hist_conversaciones,
            hist_scoring, hist_asignaciones, matching, config_modelo.
    """

    def __init__(self, db_uri: Optional[str] = None, schema: str = "comercial"):
        self.db_uri = db_uri or os.environ.get("DB_URI")
        if not self.db_uri:
            raise RuntimeError("DB_URI no definido")
        self.schema = schema
        self.pool = ConnectionPool(conninfo=self.db_uri)
        # Cache para config_modelo (TTL 5 min)
        self._config_cache: Optional[Dict[str, str]] = None
        self._config_ts: float = 0.0

    # ---------- helpers ----------
    def _set_schema(self, cur) -> None:
        cur.execute(f"SET search_path TO {self.schema};")

    def _row_to_dict(self, cur, row) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        cols = [d.name for d in cur.description]
        return {k: v for k, v in zip(cols, row)}

    def _norm_tel(self, raw: Optional[str]) -> str:
        s = "" if raw is None else str(raw)
        s = s.replace("whatsapp:", "")
        s = re.sub(r"\D+", "", s)
        if not s.startswith("51"):
            s = "51" + s
        return f"+{s}" if s else ""

    # ================================================================
    # LEADS
    # ================================================================

    def buscar_lead_por_numero(self, celular: str) -> Optional[Dict[str, Any]]:
        """Busca un lead por numero de telefono normalizado."""
        celular_norm = self._norm_tel(celular)
        print(f"[PG-COM] buscar_lead_por_numero tel_norm={celular_norm!r}", flush=True)
        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    self._set_schema(cur)
                    cur.execute(
                        "SELECT * FROM bd_leads WHERE numero = %s LIMIT 1;",
                        (celular_norm,)
                    )
                    row = cur.fetchone()
                    return self._row_to_dict(cur, row)
        except Exception as e:
            print(f"[PG-COM] buscar_lead_por_numero error: {e}", flush=True)
            return None

    def crear_lead_minimo(self, celular: str) -> Optional[Dict[str, Any]]:
        """Crea un lead minimo para un contacto inbound nuevo."""
        celular_norm = self._norm_tel(celular)
        print(f"[PG-COM] crear_lead_minimo tel_norm={celular_norm!r}", flush=True)
        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    self._set_schema(cur)
                    cur.execute(
                        """
                        INSERT INTO bd_leads
                            (numero, origen_lead, suborigen_lead, estado_de_lead,
                             scoring, nivel_interes_actual, sentimiento_actual,
                             nivel_intencion_de_compra, cantidad_contactos_lead_bot,
                             fecha_creacion, fecha_actualizacion)
                        VALUES (%s, 'inbound', 'whatsapp', 'lead',
                                0.0000, 0.00, 'neutral', 0.00, 0,
                                NOW(), NOW())
                        RETURNING *;
                        """,
                        (celular_norm,)
                    )
                    row = cur.fetchone()
                    lead = self._row_to_dict(cur, row)
                    print(f"[PG-COM] lead creado id={lead.get('id_lead') if lead else None}", flush=True)
                    return lead
        except Exception as e:
            print(f"[PG-COM] crear_lead_minimo error: {e}", flush=True)
            return None

    def buscar_o_crear_lead(self, celular: str) -> Optional[Dict[str, Any]]:
        """Busca un lead por numero; si no existe, lo crea."""
        lead = self.buscar_lead_por_numero(celular)
        if lead is None:
            lead = self.crear_lead_minimo(celular)
        return lead

    # ================================================================
    # SCORING
    # ================================================================

    def actualizar_scoring_lead(
        self,
        id_lead: str,
        scoring_nuevo: float,
        sentimiento: str,
        nivel_interes: float,
        nivel_intencion_compra: float,
        contactabilidad: float,
        evento_trigger: str = "respuesta_bot",
    ) -> bool:
        """
        Actualiza el scoring del lead en bd_leads e inserta registro en hist_scoring.
        """
        print(f"[PG-COM] actualizar_scoring id_lead={id_lead} score={scoring_nuevo} "
              f"sent={sentimiento} int={nivel_interes} comp={nivel_intencion_compra}", flush=True)
        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    self._set_schema(cur)

                    # Leer scoring actual
                    cur.execute(
                        "SELECT scoring, cantidad_contactos_lead_bot FROM bd_leads WHERE id_lead = %s;",
                        (id_lead,)
                    )
                    row = cur.fetchone()
                    scoring_anterior = float(row[0] or 0) if row else 0.0
                    contactos_prev = int(row[1] or 0) if row else 0

                    delta = round(scoring_nuevo - scoring_anterior, 4)

                    # Actualizar bd_leads
                    cur.execute(
                        """
                        UPDATE bd_leads
                        SET scoring = %s,
                            ultimo_scoring = %s,
                            sentimiento_actual = %s,
                            nivel_interes_actual = %s,
                            nivel_intencion_de_compra = %s,
                            contactabilidad = %s,
                            cantidad_contactos_lead_bot = %s,
                            ultima_fecha_gestion_bot = NOW(),
                            fecha_actualizacion = NOW()
                        WHERE id_lead = %s;
                        """,
                        (
                            round(scoring_nuevo, 4),
                            round(scoring_nuevo, 4),
                            sentimiento,
                            round(nivel_interes, 2),
                            round(nivel_intencion_compra, 2),
                            round(contactabilidad, 2),
                            contactos_prev + 1,
                            id_lead,
                        )
                    )

                    # Insertar hist_scoring
                    cur.execute(
                        """
                        INSERT INTO hist_scoring
                            (id_lead, scoring_anterior, scoring_nuevo, delta_scoring,
                             evento_trigger, nivel_interes, sentimiento, contactabilidad)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                        """,
                        (
                            id_lead,
                            round(scoring_anterior, 4),
                            round(scoring_nuevo, 4),
                            delta,
                            evento_trigger,
                            round(nivel_interes, 2),
                            sentimiento,
                            round(contactabilidad, 2),
                        )
                    )
                    print(f"[PG-COM] scoring actualizado OK delta={delta}", flush=True)
                    return True
        except Exception as e:
            print(f"[PG-COM] actualizar_scoring error: {e}", flush=True)
            return False

    # ================================================================
    # DATOS PERSONALES
    # ================================================================

    def actualizar_datos_personales_lead(
        self,
        id_lead: str,
        dni: Optional[str] = None,
        nombre: Optional[str] = None,
        apellido: Optional[str] = None,
        correo: Optional[str] = None,
    ) -> bool:
        """Actualiza datos personales del lead (solo campos no-null)."""
        sets = []
        params = []
        if dni is not None:
            sets.append("dni = %s")
            params.append(dni.strip())
        if nombre is not None:
            sets.append("nombre = %s")
            params.append(nombre.strip())
        if apellido is not None:
            sets.append("apellido = %s")
            params.append(apellido.strip())
        if correo is not None:
            sets.append("correo = %s")
            params.append(correo.strip())

        if not sets:
            return True  # nada que actualizar

        sets.append("fecha_actualizacion = NOW()")
        params.append(id_lead)

        sql = f"UPDATE bd_leads SET {', '.join(sets)} WHERE id_lead = %s;"
        print(f"[PG-COM] actualizar_datos_personales id_lead={id_lead} campos={len(sets)-1}", flush=True)

        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    self._set_schema(cur)
                    cur.execute(sql, tuple(params))
                    return True
        except Exception as e:
            print(f"[PG-COM] actualizar_datos_personales error: {e}", flush=True)
            return False

    # ================================================================
    # ESTADO DEL LEAD
    # ================================================================

    def actualizar_estado_lead(self, id_lead: str, nuevo_estado: str) -> bool:
        """Cambia estado_de_lead (lead/prospecto/venta/descartado)."""
        print(f"[PG-COM] actualizar_estado id_lead={id_lead} -> {nuevo_estado}", flush=True)
        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    self._set_schema(cur)
                    cur.execute(
                        """
                        UPDATE bd_leads
                        SET estado_de_lead = %s, fecha_actualizacion = NOW()
                        WHERE id_lead = %s;
                        """,
                        (nuevo_estado, id_lead)
                    )
                    return True
        except Exception as e:
            print(f"[PG-COM] actualizar_estado error: {e}", flush=True)
            return False

    # ================================================================
    # CONVERSACIONES
    # ================================================================

    def registrar_mensaje(
        self,
        id_lead: str,
        direccion: str,
        contenido: str,
        canal: str = "whatsapp",
        tipo_mensaje: str = "text",
        id_campana: Optional[str] = None,
        sentimiento: Optional[str] = None,
        intencion: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        score_antes: Optional[float] = None,
        score_despues: Optional[float] = None,
        tiempo_respuesta_seg: Optional[int] = None,
    ) -> Optional[str]:
        """Inserta mensaje en hist_conversaciones. Retorna id_mensaje o None."""
        delta_score = None
        if score_antes is not None and score_despues is not None:
            delta_score = round(score_despues - score_antes, 4)

        kw_json = json.dumps(keywords, ensure_ascii=False) if keywords else None

        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    self._set_schema(cur)
                    cur.execute(
                        """
                        INSERT INTO hist_conversaciones
                            (id_lead, id_campana, direccion, contenido, tipo_mensaje, canal,
                             sentimiento, intencion, keywords,
                             score_antes, score_despues, delta_score,
                             tiempo_respuesta_seg)
                        VALUES (%s, %s, %s, %s, %s, %s,
                                %s, %s, %s::jsonb,
                                %s, %s, %s,
                                %s)
                        RETURNING id_mensaje;
                        """,
                        (
                            id_lead, id_campana, direccion, contenido, tipo_mensaje, canal,
                            sentimiento, intencion, kw_json,
                            score_antes, score_despues, delta_score,
                            tiempo_respuesta_seg,
                        )
                    )
                    row = cur.fetchone()
                    mid = str(row[0]) if row else None
                    print(f"[PG-COM] registrar_mensaje OK id={mid} dir={direccion}", flush=True)
                    return mid
        except Exception as e:
            print(f"[PG-COM] registrar_mensaje error: {e}", flush=True)
            return None

    # ================================================================
    # TIEMPO DE RESPUESTA
    # ================================================================

    def obtener_ultimo_outbound_timestamp(self, id_lead: str) -> Optional[datetime]:
        """Retorna timestamp del ultimo mensaje outbound para calcular t_resp."""
        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    self._set_schema(cur)
                    cur.execute(
                        """
                        SELECT "timestamp"
                        FROM hist_conversaciones
                        WHERE id_lead = %s AND direccion = 'outbound'
                        ORDER BY "timestamp" DESC
                        LIMIT 1;
                        """,
                        (id_lead,)
                    )
                    row = cur.fetchone()
                    return row[0] if row else None
        except Exception as e:
            print(f"[PG-COM] obtener_ultimo_outbound error: {e}", flush=True)
            return None

    def calcular_tiempo_respuesta(self, id_lead: str) -> Optional[int]:
        """
        Calcula segundos desde el ultimo outbound hasta ahora.
        Tambien actualiza ultimo_tiempo_de_contacto y tiempo_respuesta_promedio.
        """
        last_out = self.obtener_ultimo_outbound_timestamp(id_lead)
        if last_out is None:
            return None

        # Asegurar que last_out tenga timezone
        if last_out.tzinfo is None:
            last_out = last_out.replace(tzinfo=timezone.utc)

        delta_seg = int((datetime.now(timezone.utc) - last_out).total_seconds())
        delta_seg = max(0, delta_seg)

        # Actualizar en bd_leads
        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    self._set_schema(cur)
                    cur.execute(
                        """
                        UPDATE bd_leads
                        SET ultimo_tiempo_de_contacto = %s,
                            tiempo_respuesta_promedio = COALESCE(
                                (tiempo_respuesta_promedio * cantidad_contactos_lead_bot + %s)
                                / NULLIF(cantidad_contactos_lead_bot + 1, 0),
                                %s
                            ),
                            fecha_actualizacion = NOW()
                        WHERE id_lead = %s;
                        """,
                        (delta_seg, delta_seg, delta_seg, id_lead)
                    )
        except Exception as e:
            print(f"[PG-COM] calcular_tiempo_respuesta update error: {e}", flush=True)

        print(f"[PG-COM] tiempo_respuesta={delta_seg}s id_lead={id_lead}", flush=True)
        return delta_seg

    # ================================================================
    # ASESORES Y ASIGNACION
    # ================================================================

    def buscar_mejor_asesor(self, id_lead: str) -> Optional[Dict[str, Any]]:
        """
        Busca el mejor asesor: primero en matching (por score_total),
        luego fallback por disponibilidad y menor cola.
        """
        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    self._set_schema(cur)

                    # Primero: matching pre-calculado
                    cur.execute(
                        """
                        SELECT m.*, a.nombre_asesor, a.disponibilidad, a.leads_en_cola
                        FROM matching m
                        JOIN bd_asesores a ON m.id_asesor = a.id_asesor
                        WHERE m.id_lead = %s
                          AND m.asignado = false
                          AND a.disponibilidad = 'disponible'
                        ORDER BY m.score_total DESC
                        LIMIT 1;
                        """,
                        (id_lead,)
                    )
                    row = cur.fetchone()
                    if row:
                        return self._row_to_dict(cur, row)

                    # Fallback: mejor asesor disponible
                    cur.execute(
                        """
                        SELECT * FROM bd_asesores
                        WHERE disponibilidad = 'disponible'
                        ORDER BY leads_en_cola ASC, ratio_conversion_de_venta DESC
                        LIMIT 1;
                        """
                    )
                    row = cur.fetchone()
                    return self._row_to_dict(cur, row)
        except Exception as e:
            print(f"[PG-COM] buscar_mejor_asesor error: {e}", flush=True)
            return None

    def crear_asignacion(
        self,
        id_lead: str,
        id_asesor: str,
        id_matching: Optional[str] = None,
        scores: Optional[Dict[str, float]] = None,
    ) -> Optional[str]:
        """
        Crea asignacion en hist_asignaciones, actualiza lead y asesor.
        Retorna id_asignacion.
        """
        scores = scores or {}
        print(f"[PG-COM] crear_asignacion lead={id_lead} asesor={id_asesor}", flush=True)
        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    self._set_schema(cur)

                    # Insertar asignacion
                    cur.execute(
                        """
                        INSERT INTO hist_asignaciones
                            (id_lead, id_asesor, id_matching,
                             score_total_al_asignar, score_k_al_asignar,
                             score_c_al_asignar, score_v_al_asignar, score_p_al_asignar,
                             estado_gestion, fecha_asignacion)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'en_espera', NOW())
                        RETURNING id_asignacion;
                        """,
                        (
                            id_lead, id_asesor, id_matching,
                            scores.get("score_total"),
                            scores.get("score_k"),
                            scores.get("score_c"),
                            scores.get("score_v"),
                            scores.get("score_p"),
                        )
                    )
                    row = cur.fetchone()
                    id_asignacion = str(row[0]) if row else None

                    # Actualizar lead
                    cur.execute(
                        """
                        UPDATE bd_leads
                        SET ultimo_asesor_asignado = %s,
                            estado_de_lead = 'prospecto',
                            fecha_actualizacion = NOW()
                        WHERE id_lead = %s;
                        """,
                        (id_asesor, id_lead)
                    )

                    # Incrementar cola del asesor
                    cur.execute(
                        """
                        UPDATE bd_asesores
                        SET leads_en_cola = COALESCE(leads_en_cola, 0) + 1,
                            fecha_actualizacion = NOW()
                        WHERE id_asesor = %s;
                        """,
                        (id_asesor,)
                    )

                    # Marcar matching si aplica
                    if id_matching:
                        cur.execute(
                            """
                            UPDATE matching
                            SET asignado = true, fecha_asignacion = NOW()
                            WHERE id_matching = %s;
                            """,
                            (id_matching,)
                        )

                    print(f"[PG-COM] asignacion creada OK id={id_asignacion}", flush=True)
                    return id_asignacion
        except Exception as e:
            print(f"[PG-COM] crear_asignacion error: {e}", flush=True)
            return None

    # ================================================================
    # CONFIG MODELO (pesos, umbrales)
    # ================================================================

    def obtener_config_modelo(self) -> Dict[str, str]:
        """Lee config_modelo con cache de 5 minutos."""
        now = time.time()
        if self._config_cache is not None and (now - self._config_ts) < 300:
            return self._config_cache

        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    self._set_schema(cur)
                    cur.execute("SELECT parametro, valor FROM config_modelo;")
                    rows = cur.fetchall()
                    config = {r[0]: r[1] for r in rows}
                    self._config_cache = config
                    self._config_ts = now
                    print(f"[PG-COM] config_modelo cargado: {len(config)} params", flush=True)
                    return config
        except Exception as e:
            print(f"[PG-COM] obtener_config_modelo error: {e}", flush=True)
            return {}
