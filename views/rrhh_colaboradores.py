import io
import streamlit as st
import pandas as pd
from sqlalchemy import text
from src.conexion_db import conn
from src.rrhh import (ROLES_RRHH_CARGUE, auditar_rrhh, validar_suma_100,
                      validar_solape_asignacion)

st.set_page_config(page_title="Colaboradores RRHH", layout="wide")
st.title("👥 Talento Humano — Colaboradores y Distribución del Bono")
st.markdown("---")

rol = st.session_state.get("rol_actual")
if rol not in ROLES_RRHH_CARGUE:
    st.error("⛔ Acceso denegado. Solo Talento Humano, Gerencia y Administración.")
    st.stop()

tab_cols, tab_dist, tab_asig = st.tabs(
    ["🧑‍💼 Colaboradores", "📊 Plantilla de Distribución por Almacén",
     "🔗 Asignaciones Persona ↔ Puesto"])

# ============================================================
# PESTAÑA 1: COLABORADORES
# ============================================================
with tab_cols:
    st.subheader("Alta manual de un colaborador")
    with st.form("form_alta_colab"):
        c1, c2, c3 = st.columns(3)
        with c1:
            ced = st.text_input("Cédula *", key="ced_nueva")
            nom = st.text_input("Nombre completo *", key="nom_nuevo")
        with c2:
            ing = st.date_input("Fecha de ingreso *", key="ing_nuevo")
            prb = st.number_input("Periodo de prueba (meses)", min_value=0, max_value=12,
                                  value=2, key="prb_nuevo")
        with c3:
            slp = st.number_input("SlpCode SAP (opcional)", value=0, step=1, key="slp_nuevo")
            own = st.number_input("OwnerCode SAP (opcional)", value=0, step=1, key="own_nuevo")
            portal = st.text_input("Usuario del portal (opcional)", key="portal_nuevo")

        submitted = st.form_submit_button("➕ Guardar colaborador")
        if submitted:
            if not ced.strip() or not nom.strip():
                st.error("Cédula y nombre son obligatorios.")
            else:
                with conn.session as con:
                    res = con.execute(text("""
                        INSERT INTO rrhh.colaboradores
                            (cedula, nombre, slp_code, owner_code, usuario_portal,
                             fecha_ingreso, periodo_prueba_meses)
                        VALUES (:ced, :nom, NULLIF(:slp,0), NULLIF(:own,0), NULLIF(:por,''),
                                :ing, :prb)
                        ON CONFLICT (cedula) DO UPDATE SET
                            nombre = EXCLUDED.nombre,
                            slp_code = COALESCE(EXCLUDED.slp_code, rrhh.colaboradores.slp_code),
                            owner_code = COALESCE(EXCLUDED.owner_code, rrhh.colaboradores.owner_code),
                            usuario_portal = COALESCE(EXCLUDED.usuario_portal, rrhh.colaboradores.usuario_portal),
                            fecha_ingreso = EXCLUDED.fecha_ingreso,
                            periodo_prueba_meses = EXCLUDED.periodo_prueba_meses
                        RETURNING id
                    """), {"ced": ced.strip(), "nom": nom.strip(), "slp": int(slp),
                           "own": int(own), "por": portal.strip(), "ing": ing, "prb": int(prb)})
                    cid = res.fetchone()[0]
                    con.commit()
                auditar_rrhh("colaboradores", cid, "UPSERT", None,
                             {"cedula": ced.strip(), "nombre": nom.strip()})
                st.success(f"✅ Colaborador guardado (id {cid}).")
                st.rerun()

    st.markdown("---")
    st.subheader("Carga masiva por Excel")
    st.caption("Plantilla: columnas exactas → `cedula` (texto), `nombre`, `fecha_ingreso` "
               "(AAAA-MM-DD), `periodo_prueba_meses` (opcional, default 2), "
               "`slp_code` (opcional), `owner_code` (opcional), `usuario_portal` (opcional).")
    plantilla = pd.DataFrame({
        "cedula": ["1234567890"], "nombre": ["Ejemplo Pérez"],
        "fecha_ingreso": ["2026-01-15"], "periodo_prueba_meses": [2],
        "slp_code": [""], "owner_code": [""], "usuario_portal": [""],
    })
    buf_p = io.BytesIO()
    with pd.ExcelWriter(buf_p, engine="openpyxl") as xw:
        plantilla.to_excel(xw, index=False, sheet_name="colaboradores")
    st.download_button("📥 Descargar plantilla Excel (.xlsx)", buf_p.getvalue(),
                       file_name="plantilla_colaboradores.xlsx", mime="sheet")

    upl = st.file_uploader("Subir archivo .xlsx o .csv", type=["xlsx", "csv"])
    if upl is not None:
        try:
            df = (pd.read_csv(upl) if upl.name.lower().endswith(".csv")
                  else pd.read_excel(upl, sheet_name="colaboradores"))
        except Exception as e:
            st.error(f"No se pudo leer el archivo: {e}")
            df = None

        if df is not None:
            faltan = {"cedula", "nombre", "fecha_ingreso"} - set(df.columns)
            if faltan:
                st.error(f"Faltan columnas obligatorias: {faltan}")
            else:
                df["cedula"] = df["cedula"].astype(str).str.strip()
                df = df[df["cedula"] != ""].drop_duplicates(subset="cedula", keep="last")
                st.dataframe(df, use_container_width=True, hide_index=True)
                st.info(f"{len(df)} filas listas para importar (se actualizan las cédulas existentes).")

                if st.button("💾 Confirmar importación"):
                    ok, err = 0, []
                    for _, r in df.iterrows():
                        try:
                            with conn.session as con:
                                con.execute(text("""
                                    INSERT INTO rrhh.colaboradores
                                        (cedula, nombre, slp_code, owner_code, usuario_portal,
                                         fecha_ingreso, periodo_prueba_meses)
                                    VALUES (:ced, :nom, NULLIF(:slp,0), NULLIF(:own,0), NULLIF(:por,''),
                                            :ing, :prb)
                                    ON CONFLICT (cedula) DO UPDATE SET
                                        nombre = EXCLUDED.nombre,
                                        slp_code = COALESCE(EXCLUDED.slp_code, rrhh.colaboradores.slp_code),
                                        owner_code = COALESCE(EXCLUDED.owner_code, rrhh.colaboradores.owner_code),
                                        usuario_portal = COALESCE(EXCLUDED.usuario_portal, rrhh.colaboradores.usuario_portal),
                                        fecha_ingreso = EXCLUDED.fecha_ingreso,
                                        periodo_prueba_meses = EXCLUDED.periodo_prueba_meses
                                """), {
                                    "ced": str(r["cedula"]).strip(),
                                    "nom": str(r["nombre"]).strip(),
                                    "slp": int(float(r["slp_code"])) if pd.notna(r.get("slp_code")) and str(r.get("slp_code")).strip() not in ("", "nan") else 0,
                                    "own": int(float(r["owner_code"])) if pd.notna(r.get("owner_code")) and str(r.get("owner_code")).strip() not in ("", "nan") else 0,
                                    "por": str(r.get("usuario_portal") or "").strip(),
                                    "ing": pd.to_datetime(r["fecha_ingreso"]).date(),
                                    "prb": int(float(r.get("periodo_prueba_meses") or 2)),
                                })
                                con.commit()
                            ok += 1
                        except Exception as e:
                            err.append(f"fila {r['cedula']}: {e}")
                    auditar_rrhh("colaboradores", "bulk", "IMPORT", None,
                                 {"filas_ok": ok, "errores": err[:20]})
                    st.success(f"✅ {ok} colaboradores importados.")
                    if err:
                        st.warning("Con errores:\n" + "\n".join(err))
                    st.rerun()

    st.markdown("---")
    st.subheader("Listado de colaboradores")
    df_c = conn.query("""
        SELECT id, cedula, nombre, slp_code, owner_code, usuario_portal,
               fecha_ingreso, periodo_prueba_meses, activo
        FROM rrhh.colaboradores ORDER BY nombre
    """, ttl=0)
    
    if not df_c.empty:
        st.dataframe(df_c, use_container_width=True, hide_index=True)
        with st.expander("Desactivar / reactivar colaborador"):
            sel_id = st.selectbox("ID del colaborador", df_c["id"].tolist())
            if st.button("🔄 Alternar estado activo"):
                fila = df_c[df_c["id"] == sel_id].iloc[0]
                with conn.session as con:
                    con.execute(text("""
                        UPDATE rrhh.colaboradores SET activo = NOT activo WHERE id = :i
                    """), {"i": int(sel_id)})
                    con.commit()
                auditar_rrhh("colaboradores", sel_id, "TOGGLE_ACTIVO",
                             {"activo": bool(fila["activo"])}, {"activo": not bool(fila["activo"])})
                st.rerun()
    else:
        st.info("Aún no hay colaboradores registrados.")

# ============================================================
# PESTAÑA 2: PLANTILLA DE DISTRIBUCIÓN POR ALMACÉN
# ============================================================
with tab_dist:
    st.subheader("Distribución del bono por cargo (%)")
    st.caption("Regla de oro: por almacén y vigencia, la suma de porcentajes debe dar "
               "EXACTAMENTE 100%. Los cargos y porcentajes varían entre almacenes.")

    alm = conn.query("SELECT codigo, nombre_serie FROM app.almacenes WHERE activo=TRUE ORDER BY codigo", ttl=0)
    if alm.empty:
        st.warning("La tabla app.almacenes está vacía. Ejecuta seed_almacenes_desde_maps().")
    else:
        map_alm = dict(zip(alm["codigo"], alm["nombre_serie"].fillna(alm["codigo"])))
        sel_alm = st.selectbox("Almacén", list(map_alm.keys()),
                               format_func=lambda k: f"{k} — {map_alm[k]}")

        df_d = conn.query("""
            SELECT id, almacen, cargo, pct, vigente_desde, vigente_hasta
            FROM rrhh.distribucion_puestos WHERE almacen = :a ORDER BY vigente_desde DESC, cargo
        """, {"a": sel_alm}, ttl=0)
        
        if not df_d.empty:
            total_vig = df_d.groupby("vigente_desde")["pct"].sum()
            for vig, tot in total_vig.items():
                marca = "✅" if abs(float(tot) - 100.0) < 0.001 else "❌"
                st.markdown(f"{marca} Vigencia **{vig}**: suma = `{float(tot):.3f}%`")
            st.dataframe(df_d, use_container_width=True, hide_index=True)

        st.markdown("##### Alta manual de un puesto")
        with st.form("form_puesto"):
            p1, p2, p3, p4 = st.columns(4)
            with p1:
                cargo = st.text_input("Cargo *", key="cargo_nuevo")
            with p2:
                pct = st.number_input("% del bono *", min_value=0.0, max_value=100.0,
                                      step=0.5, value=0.0, key="pct_nuevo")
            with p3:
                v_desde = st.date_input("Vigente desde *", key="vd_nuevo")
            with p4:
                v_hasta = st.date_input("Vigente hasta (vacío = abierto)",
                                        value=None, key="vh_nuevo")
            guardar = st.form_submit_button("💾 Guardar puesto")

        if guardar:
            if not cargo.strip() or pct <= 0:
                st.error("Cargo y porcentaje (>0) son obligatorios.")
            else:
                # Validación anticipada: ¿cuánto sumaría esta vigencia?
                suma_actual = float(df_d[df_d["vigente_desde"] == v_desde]["pct"].sum()) if not df_d.empty else 0.0
                nueva_suma = suma_actual + float(pct)
                with conn.session as con:
                    res = con.execute(text("""
                        INSERT INTO rrhh.distribucion_puestos
                            (almacen, cargo, pct, vigente_desde, vigente_hasta)
                        VALUES (:a, :c, :p, :vd, :vh)
                        ON CONFLICT (almacen, cargo, vigente_desde) DO UPDATE SET
                            pct = EXCLUDED.pct, vigente_hasta = EXCLUDED.vigente_hasta
                        RETURNING id
                    """), {"a": sel_alm, "c": cargo.strip(), "p": float(pct),
                           "vd": v_desde, "vh": v_hasta})
                    pid = res.fetchone()[0]
                    con.commit()
                auditar_rrhh("distribucion_puestos", pid, "UPSERT", None,
                             {"almacen": sel_alm, "cargo": cargo.strip(), "pct": float(pct)})
                if abs(nueva_suma - 100.0) < 0.001:
                    st.success(f"✅ Plantilla completa: {sel_alm} vigencia {v_desde} suma 100%.")
                elif nueva_suma > 100.0:
                    st.error(f"❌ La vigencia {v_desde} de {sel_alm} suma {nueva_suma:.3f}% (>100%). "
                             "Ajusta los porcentajes antes de liquidar.")
                else:
                    st.warning(f"⏳ Vigencia {v_desde} de {sel_alm} suma {nueva_suma:.3f}% "
                               f"(falta {100 - nueva_suma:.3f}% para completar la plantilla).")
                st.rerun()

        st.markdown("---")
        st.subheader("Carga masiva de plantilla (Excel)")
        st.caption("Columnas exactas: `almacen` (código, ej: Q6), `cargo`, `pct`, "
                   "`vigente_desde` (AAAA-MM-DD), `vigente_hasta` (opcional).")
        plant_d = pd.DataFrame({
            "almacen": ["Q6"], "cargo": ["Gerente de punto"], "pct": [22.0],
            "vigente_desde": ["2026-10-01"], "vigente_hasta": [""],
        })
        buf_d = io.BytesIO()
        with pd.ExcelWriter(buf_d, engine="openpyxl") as xw:
            plant_d.to_excel(xw, index=False, sheet_name="distribucion")
        st.download_button("📥 Descargar plantilla distribución (.xlsx)", buf_d.getvalue(),
                           file_name="plantilla_distribucion.xlsx", mime="sheet")

        upl_d = st.file_uploader("Subir plantilla de distribución", type=["xlsx", "csv"], key="upl_dist")
        if upl_d is not None:
            try:
                dfx = (pd.read_csv(upl_d) if upl_d.name.lower().endswith(".csv")
                       else pd.read_excel(upl_d, sheet_name="distribucion"))
            except Exception as e:
                st.error(f"No se pudo leer: {e}")
                dfx = None

            if dfx is not None:
                req = {"almacen", "cargo", "pct", "vigente_desde"}
                if req - set(dfx.columns):
                    st.error(f"Faltan columnas: {req - set(dfx.columns)}")
                else:
                    dfx["vigente_desde"] = pd.to_datetime(dfx["vigente_desde"]).dt.date
                    if "vigente_hasta" in dfx.columns:
                        dfx["vigente_hasta"] = pd.to_datetime(dfx["vigente_hasta"], errors="coerce").dt.date
                    else:
                        dfx["vigente_hasta"] = pd.NaT

                    codigos_invalidos = set(dfx["almacen"]) - set(map_alm.keys())
                    if codigos_invalidos:
                        st.error(f"Códigos de almacén desconocidos: {codigos_invalidos}")
                    else:
                        errs = validar_suma_100(dfx)
                        st.dataframe(dfx, use_container_width=True, hide_index=True)
                        if errs:
                            st.error("**Validación 100% fallida:**\n" + "\n".join(errs))
                            st.caption("Corrige el Excel y vuelve a subirlo. No se importará nada.")
                        else:
                            st.success("✅ Todos los almacenes/vigencias suman exactamente 100%.")
                            if st.button("💾 Importar plantilla completa"):
                                n = 0
                                for _, r in dfx.iterrows():
                                    with conn.session as con:
                                        con.execute(text("""
                                            INSERT INTO rrhh.distribucion_puestos
                                                (almacen, cargo, pct, vigente_desde, vigente_hasta)
                                            VALUES (:a, :c, :p, :vd, :vh)
                                            ON CONFLICT (almacen, cargo, vigente_desde) DO UPDATE SET
                                                pct = EXCLUDED.pct, vigente_hasta = EXCLUDED.vigente_hasta
                                        """), {
                                            "a": str(r["almacen"]).strip(), "c": str(r["cargo"]).strip(),
                                            "p": float(r["pct"]), "vd": r["vigente_desde"],
                                            "vh": (r["vigente_hasta"] if pd.notna(r["vigente_hasta"]) else None),
                                        })
                                        con.commit()
                                    n += 1
                                auditar_rrhh("distribucion_puestos", "bulk", "IMPORT",
                                             None, {"filas": n})
                                st.success(f"✅ {n} puestos importados.")
                                st.rerun()

# ============================================================
# PESTAÑA 3: ASIGNACIONES PERSONA ↔ PUESTO (con historial)
# ============================================================
with tab_asig:
    st.subheader("Asignar colaboradores a un puesto de un almacén")
    st.caption("Un colaborador no puede tener dos asignaciones que se solapen en el tiempo. "
               "El periodo de prueba se calcula automáticamente desde fecha de ingreso + meses.")

    cols_a = conn.query("""
        SELECT id, cedula, nombre, fecha_ingreso, periodo_prueba_meses
        FROM rrhh.colaboradores WHERE activo=TRUE ORDER BY nombre
    """, ttl=0)
    alm_a = conn.query("SELECT codigo, nombre_serie FROM app.almacenes WHERE activo=TRUE ORDER BY codigo", ttl=0)
    
    if cols_a.empty or alm_a.empty:
        st.info("Necesitas al menos un colaborador y almacenes sembrados para asignar puestos.")
    else:
        map_col = {f"{r['nombre']} — CC {r['cedula']}": r["id"] for _, r in cols_a.iterrows()}
        map_alm_a = dict(zip(alm_a["codigo"], alm_a["nombre_serie"].fillna(alm_a["codigo"])))
        cargos_existentes = conn.query(
            "SELECT DISTINCT cargo FROM rrhh.distribucion_puestos ORDER BY cargo", ttl=0)["cargo"].tolist()

        with st.form("form_asig"):
            a1, a2, a3, a4 = st.columns(4)
            with a1:
                sel_col_a = st.selectbox("Colaborador *", list(map_col.keys()))
            with a2:
                sel_alm_a = st.selectbox("Almacén *", list(map_alm_a.keys()),
                                         format_func=lambda k: f"{k} — {map_alm_a[k]}")
            with a3:
                sel_cargo = st.text_input("Cargo * (debe existir en la plantilla)",
                                          value=cargos_existentes[0] if cargos_existentes else "")
            with a4:
                fdesde = st.date_input("Desde *")
                hasta = st.date_input("Hasta (vacío = vigente)", value=None)
            asig_btn = st.form_submit_button("💾 Guardar asignación")

        if asig_btn:
            cid = map_col[sel_col_a]
            conflictos = validar_solape_asignacion(cid, fdesde, hasta)
            if not sel_cargo.strip():
                st.error("El cargo es obligatorio.")
            elif conflictos:
                st.error("❌ Solape detectado:\n" + "\n".join(conflictos))
            else:
                # Validación blanda: ¿el cargo existe en la plantilla vigente de ese almacén?
                plant_ok = conn.query("""
                    SELECT 1 FROM rrhh.distribucion_puestos
                    WHERE almacen = :a AND cargo = :c
                      AND vigente_desde <= :fd
                      AND (vigente_hasta IS NULL OR vigente_hasta >= :fd)
                """, {"a": sel_alm_a, "c": sel_cargo.strip(), "fd": fdesde}, ttl=0)
                if plant_ok.empty:
                    st.warning(f"⚠️ El cargo '{sel_cargo}' no está en la plantilla vigente de "
                               f"{sel_alm_a}. Se guarda igual, pero revisa la pestaña de distribución.")
                with conn.session as con:
                    res = con.execute(text("""
                        INSERT INTO rrhh.asignaciones
                            (colaborador_id, almacen, cargo, fecha_desde, fecha_hasta)
                        VALUES (:cid, :alm, :car, :fd, :fh) RETURNING id
                    """), {"cid": cid, "alm": sel_alm_a, "car": sel_cargo.strip(),
                           "fd": fdesde, "fh": hasta})
                    aid = res.fetchone()[0]
                    con.commit()
                auditar_rrhh("asignaciones", aid, "CREATE", None,
                             {"colaborador": sel_col_a, "almacen": sel_alm_a,
                              "cargo": sel_cargo.strip(), "desde": str(fdesde),
                              "hasta": str(hasta) if hasta else None})
                st.success(f"✅ Asignación #{aid} guardada.")
                st.rerun()

        st.markdown("---")
        st.subheader("Historial de asignaciones")
        df_as = conn.query("""
            SELECT a.id, c.nombre, c.cedula, a.almacen, a.cargo,
                   a.fecha_desde, COALESCE(a.fecha_hasta::TEXT, 'Vigente') AS fecha_hasta,
                   (CURRENT_DATE < c.fecha_ingreso + make_interval(months => c.periodo_prueba_meses))
                     AS en_periodo_prueba
            FROM rrhh.asignaciones a
            JOIN rrhh.colaboradores c ON c.id = a.colaborador_id
            ORDER BY a.fecha_desde DESC LIMIT 300
        """, ttl=0)
        
        if not df_as.empty:
            st.dataframe(df_as, use_container_width=True, hide_index=True)
            st.caption("`en_periodo_prueba = True` ⇒ ese colaborador NO recibe bono este mes.")
            with st.expander("Cerrar una asignación vigente (poner fecha_hasta)"):
                vigentes = df_as[df_as["fecha_hasta"] == "Vigente"]
                if vigentes.empty:
                    st.info("No hay asignaciones vigentes abiertas.")
                else:
                    sel_id = st.selectbox("ID asignación", vigentes["id"].tolist())
                    f_cierre = st.date_input("Fecha hasta", key="f_cierre_asig")
                    if st.button("🔒 Cerrar asignación"):
                        with conn.session as con:
                            con.execute(text("""
                                UPDATE rrhh.asignaciones SET fecha_hasta = :fh WHERE id = :i
                            """), {"fh": f_cierre, "i": int(sel_id)})
                            con.commit()
                        auditar_rrhh("asignaciones", sel_id, "CERRAR",
                                     {"fecha_hasta": None}, {"fecha_hasta": str(f_cierre)})
                        st.success("Asignación cerrada.")
                        st.rerun()
        else:
            st.info("Sin asignaciones registradas.")