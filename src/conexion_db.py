import streamlit as st
import pandas as pd
import numpy as np
import pyodbc
from sqlalchemy import create_engine, text

# ============================================================
# CONEXIÓN CENTRALIZADA POSTGRESQL (usando secretos seguros)
# ============================================================
CADENA_CONEXION_PG = st.secrets["postgres"]["url"]
conn = st.connection("postgresql", type="sql", url=CADENA_CONEXION_PG)

# ============================================================
# FUNCIÓN PRINCIPAL DE SINCRONIZACIÓN ELT DESDE SAP
# ============================================================
def ejecutar_sincronizacion_desde_sap():
    """Extrae datos de SAP HANA y los almacena en PostgreSQL"""
    try:
        sap_conf = st.secrets["sap"]
        conn_str_sap = (
            f"DRIVER={sap_conf['driver']};"
            f"SERVERNODE={sap_conf['server']};"
            f"UID={sap_conf['user']};"
            f"PWD={sap_conf['password']}"
        )
        conexion_sap = pyodbc.connect(conn_str_sap)

        # ============================================================
        # 1. QUERIES DE EXTRACCIÓN SAP (módulos existentes)
        # ============================================================
        query_remisiones = """
            SELECT T0."DocNum" as "Documento", T0."CardName" as "Cliente", T0."DocDate" as "Fecha_Contabilizacion", 
            T0."DocStatus" as "Status_Documento", T0."SlpCode" as "Empleado_Ventas", T0."OwnerCode" as "Propietario_Doc",
            T2."WhsName" as "Sede_Codigo",
            T1."ItemCode" as "Numero_Articulo", T1."Dscription" as "Descripcion", 
            T1."Quantity" as "Cantidad", T1."Price" as "Precio", T1."LineTotal" AS "Precio_Sin_IVA", 
            T1."LineTotal" + T1."VatSum" AS "Precio_Total", T2."WhsName" as "Almacen", T3."U_NAME" as "Nombre_Usuario",
            T5."ItmsGrpNam" as "Linea", T6."FirmName" AS "Marca",
            COALESCE(NULLIF(TRIM(T8."firstName" || ' ' || T8."lastName"), ''), T3."U_NAME") as "Colaborador"
            FROM "NE042025"."ODLN" T0 
            INNER JOIN "NE042025"."DLN1" T1 ON T0."DocEntry" = T1."DocEntry" 
            INNER JOIN "NE042025"."OWHS" T2 ON T1."WhsCode" = T2."WhsCode"  
            INNER JOIN "NE042025"."OUSR" T3 ON T0."UserSign" = T3."USERID"
            INNER JOIN "NE042025"."OITM" T4 ON T1."ItemCode" = T4."ItemCode"
            INNER JOIN "NE042025"."OITB" T5 ON T4."ItmsGrpCod" = T5."ItmsGrpCod"
            INNER JOIN "NE042025"."OMRC" T6 ON T4."FirmCode" = T6."FirmCode"
            LEFT JOIN "NE042025"."OUSR" T7 ON T0."OwnerCode" = T7."USERID"
            LEFT JOIN "NE042025"."OHEM" T8 ON T7."INTERNAL_K" = T8."empID"
            WHERE T0."DocStatus" = 'O' AND T2."WhsName" <> 'BODEGA INGENIERIA'
        """
        query_colaboradores = """
            SELECT T0."lastName" as "Nombre", T0."firstName" as "Apellido", T0."Code" as "Codigo" FROM "NE042025"."OHEM" T0
        """
        query_cotizaciones = """
            SELECT 
                T0."DocNum" as "Documento", 
                T0."CardCode" as "Numero_Cliente",
                T0."CardName" as "Cliente", 
                T0."DocDate" as "Fecha_Contabilizacion", 
                T0."DocStatus" as "Status_Documento", 
                T0."SlpCode" as "Empleado_Ventas", 
                T0."OwnerCode" as "Propietario_Doc",
                T2."WhsName" as "Sede_Codigo", 
                T1."ItemCode" as "Numero_Articulo", 
                T1."Dscription" as "Descripcion", 
                T1."Quantity" as "Cantidad", 
                T1."Price" as "Precio", 
                T1."LineTotal" AS "Precio_Sin_IVA", 
                T1."LineTotal" + T1."VatSum" AS "Precio_Total",
                T1."GrssProfit" AS "Rentabilidad",
                T2."WhsName" as "Almacen", 
                T3."U_NAME" as "Nombre_Usuario",
                T6."CreditLine" AS "Cupo_Credito",
                COALESCE(NULLIF(TRIM(T4."firstName" || ' ' || T4."lastName"), ''), T3."U_NAME") as "Colaborador"
            FROM "NE042025"."OQUT" T0 
            INNER JOIN "NE042025"."QUT1" T1 ON T0."DocEntry" = T1."DocEntry" 
            INNER JOIN "NE042025"."OWHS" T2 ON T1."WhsCode" = T2."WhsCode"  
            INNER JOIN "NE042025"."OUSR" T3 ON T0."UserSign" = T3."USERID" 
            LEFT JOIN "NE042025"."OUSR" T5 ON T0."OwnerCode" = T5."USERID"
            LEFT JOIN "NE042025"."OHEM" T4 ON T5."INTERNAL_K" = T4."empID"
            INNER JOIN "NE042025"."OCRD" T6 ON T0."CardCode" = T6."CardCode"
            WHERE T0."DocStatus" = 'O' AND T2."WhsName" <> 'BODEGA INGENIERIA'
        """
        query_ordenes = """
            SELECT T0."DocNum" as "Documento", T0."CardCode" as "Numero_Cliente", T0."CardName" as "Cliente", T0."DocDate" as "Fecha_Contabilizacion", 
            T0."DocStatus" as "Status_Documento", T0."SlpCode" as "Empleado_Ventas", T0."OwnerCode" as "Propietario_Doc",
            T2."WhsName" as "Sede_Codigo", T1."ItemCode" as "Numero_Articulo", T1."Dscription" as "Descripcion", 
            T1."Quantity" as "Cantidad", T1."Price" as "Precio", T1."LineTotal" AS "Precio_Sin_IVA", 
            T1."LineTotal" + T1."VatSum" AS "Precio_Total", T2."WhsName" as "Almacen", T3."U_NAME" as "Nombre_Usuario",
            COALESCE(NULLIF(TRIM(T4."firstName" || ' ' || T4."lastName"), ''), T3."U_NAME") as "Colaborador"
            FROM "NE042025"."ORDR" T0 INNER JOIN "NE042025"."RDR1" T1 ON T0."DocEntry" = T1."DocEntry" 
            INNER JOIN "NE042025"."OWHS" T2 ON T1."WhsCode" = T2."WhsCode"  
            INNER JOIN "NE042025"."OUSR" T3 ON T0."UserSign" = T3."USERID" 
            LEFT JOIN "NE042025"."OUSR" T5 ON T0."OwnerCode" = T5."USERID"
            LEFT JOIN "NE042025"."OHEM" T4 ON T5."INTERNAL_K" = T4."empID"
            WHERE T0."DocStatus" = 'O' AND T2."WhsName" <> 'BODEGA INGENIERIA'
        """
        query_solicitudes = """
            SELECT 
            T0."DocNum" as "Documento", T0."Comments" as "Observaciones", T0."DocDate" as "Fecha_Contabilizacion", 
            T0."DocStatus" as "Status_Documento", T0."SlpCode" as "Empleado_Ventas", T0."OwnerCode" as "Propietario_Doc",
            T2."WhsName" as "Sede_Codigo", 
            T1."ItemCode" as "Numero_Articulo", T1."Dscription" as "Descripcion", 
            T1."Quantity" as "Cantidad", T1."Price" as "Precio", 
            T1."LineTotal" AS "Precio_Sin_IVA", T1."LineTotal" + T1."VatSum" AS "Precio_Total",
            T2."WhsName" as "Almacen", T3."U_NAME" as "Nombre_Usuario",
            COALESCE(NULLIF(TRIM(T4."firstName" || ' ' || T4."lastName"), ''), T3."U_NAME") as "Colaborador"
            FROM "NE042025"."OPRQ" T0 
            INNER JOIN "NE042025"."PRQ1" T1 ON T0."DocEntry" = T1."DocEntry" 
            INNER JOIN "NE042025"."OWHS" T2 ON T1."WhsCode" = T2."WhsCode"  
            INNER JOIN "NE042025"."OUSR" T3 ON T0."UserSign" = T3."USERID" 
            LEFT JOIN "NE042025"."OUSR" T5 ON T0."OwnerCode" = T5."USERID"
            LEFT JOIN "NE042025"."OHEM" T4 ON T5."INTERNAL_K" = T4."empID"
            WHERE T0."DocStatus" = 'O' AND T2."WhsName" <> 'BODEGA INGENIERIA'
        """
        query_traslados = """
            SELECT 
                T0."DocNum" as "Documento", T0."Comments" as "Observaciones", 
                T6."WhsName" as "Almacen_Origen",
                T0."DocDate" as "Fecha_Contabilizacion", 
                T0."DocStatus" as "Status_Documento", 
                T0."OwnerCode" as "Propietario_Doc",
                T2."WhsName" as "Sede_Codigo",
                T1."ItemCode" as "Numero_Articulo", T1."Dscription" as "Descripcion", 
                T1."Quantity" as "Cantidad", T1."Price" as "Precio", 
                T1."LineTotal" AS "Precio_Sin_IVA", T1."LineTotal" + T1."VatSum" AS "Precio_Total",
                T2."WhsName" as "Almacen", T3."U_NAME" as "Nombre_Usuario",
                COALESCE(NULLIF(TRIM(T4."firstName" || ' ' || T4."lastName"), ''), T3."U_NAME") as "Colaborador"
            FROM "NE042025"."OWTQ" T0 
            INNER JOIN "NE042025"."WTQ1" T1 ON T0."DocEntry" = T1."DocEntry" 
            INNER JOIN "NE042025"."OWHS" T2 ON T1."WhsCode" = T2."WhsCode"  
            INNER JOIN "NE042025"."OUSR" T3 ON T0."UserSign" = T3."USERID" 
            LEFT JOIN "NE042025"."OUSR" T5 ON T0."OwnerCode" = T5."USERID"
            LEFT JOIN "NE042025"."OHEM" T4 ON T5."INTERNAL_K" = T4."empID"
            LEFT JOIN "NE042025"."OWHS" T6 ON T0."Filler" = T6."WhsCode"
            WHERE T0."DocStatus" = 'O' AND T2."WhsName" <> 'BODEGA INGENIERIA'
        """
        query_facturas = """
            SELECT T0."DocNum" as "Documento", T0."CardCode" as "Numero_Cliente", T0."CardName" as "Cliente", T0."DocDate" as "Fecha_Contabilizacion", 
            T0."DocStatus" as "Status_Documento", T0."SlpCode" as "Empleado_Ventas", T0."OwnerCode" as "Propietario_Doc",
            T2."WhsName" as "Sede_Codigo", T1."ItemCode" as "Numero_Articulo", T1."Dscription" as "Descripcion", 
            T1."Quantity" as "Cantidad", T1."Price" as "Precio", T1."LineTotal" AS "Precio_Sin_IVA", 
            T1."LineTotal" + T1."VatSum" AS "Precio_Total", T2."WhsName" as "Almacen", T3."U_NAME" as "Nombre_Usuario",
            COALESCE(NULLIF(TRIM(T4."firstName" || ' ' || T4."lastName"), ''), T3."U_NAME") as "Colaborador"
            FROM "NE042025"."OINV" T0 
            INNER JOIN "NE042025"."INV1" T1 ON T0."DocEntry" = T1."DocEntry" 
            INNER JOIN "NE042025"."OWHS" T2 ON T1."WhsCode" = T2."WhsCode"  
            INNER JOIN "NE042025"."OUSR" T3 ON T0."UserSign" = T3."USERID" 
            LEFT JOIN "NE042025"."OUSR" T5 ON T0."OwnerCode" = T5."USERID"
            LEFT JOIN "NE042025"."OHEM" T4 ON T5."INTERNAL_K" = T4."empID"
            WHERE T0."DocStatus" = 'O' AND T0."isIns" = 'Y' AND T2."WhsName" <> 'BODEGA INGENIERIA'
        """
        query_notas = """
            SELECT T0."DocNum" as "Documento", T0."CardCode" as "Numero_Cliente", T0."CardName" as "Cliente", T0."DocDate" as "Fecha_Contabilizacion", 
            T0."DocStatus" as "Status_Documento", T0."SlpCode" as "Empleado_Ventas", T0."OwnerCode" as "Propietario_Doc",
            T2."WhsName" as "Sede_Codigo", T1."ItemCode" as "Numero_Articulo", T1."Dscription" as "Descripcion", 
            T1."Quantity" as "Cantidad", T1."Price" as "Precio", T1."LineTotal" AS "Precio_Sin_IVA", 
            T1."LineTotal" + T1."VatSum" AS "Precio_Total", T2."WhsName" as "Almacen", T3."U_NAME" as "Nombre_Usuario",
            COALESCE(NULLIF(TRIM(T4."firstName" || ' ' || T4."lastName"), ''), T3."U_NAME") as "Colaborador"
            FROM "NE042025"."ORIN" T0 
            INNER JOIN "NE042025"."RIN1" T1 ON T0."DocEntry" = T1."DocEntry" 
            INNER JOIN "NE042025"."OWHS" T2 ON T1."WhsCode" = T2."WhsCode"  
            INNER JOIN "NE042025"."OUSR" T3 ON T0."UserSign" = T3."USERID" 
            LEFT JOIN "NE042025"."OUSR" T5 ON T0."OwnerCode" = T5."USERID"
            LEFT JOIN "NE042025"."OHEM" T4 ON T5."INTERNAL_K" = T4."empID"
            WHERE T0."DocStatus" = 'O' AND T2."WhsName" <> 'BODEGA INGENIERIA'
        """

        # ============================================================
        # 🎯 VENTANA DE AUDITORÍA LOCAL (periodo fijo y reducido)
        # Mientras validamos contra Power BI/Excel usamos fechas fijas.
        # MESES_HISTORICO_VENTAS (ADD_MONTHS).
        # ============================================================
        MESES_HISTORICO_VENTAS = 14  # reservado para producción
        FECHA_INICIO_AUDITORIA = "2025-04-01"
        FECHA_FIN_AUDITORIA = "2026-08-31"

        # ============================================================
        # 🆕 QUERY: VENTAS NETAS (Facturas - Notas Crédito)
        # ============================================================
        query_ventas_netas = f"""
            SELECT
                T0."DocDate" AS "fecha_contabilizacion",
                T0."DocNum" AS "documento",
                T0."CardCode" AS "codigo_cliente",
                T0."CardName" AS "nombre_cliente",
                T1."LineTotal" AS "precio_sin_iva",
                (T1."LineTotal" + T1."VatSum") AS "precio_con_iva",
                (T1."LineTotal" - T1."GrssProfit") AS "costo_total",
                T1."GrssProfit" AS "rentabilidad",
                T1."TaxCode" AS "tax_code",
                COALESCE(T2."U_NAME", 'Sin Usuario') AS "nombre_usuario",
                T0."SlpCode" AS "codigo_vendedor",
                COALESCE(T4."SlpName", 'Sin Vendedor') AS "nombre_vendedor",
                COALESCE(T3."SeriesName", '') AS "nombre_serie",
                COALESCE(T3."BeginStr", '') AS "prefijo_serie",
                T1."WhsCode" AS "codigo_almacen",
                CASE 
                    WHEN T3."SeriesName" IN ('128E', 'NCNT128.', 'ND-NT128', 'RC-NT128', 'NPNT128', '128J', '128F') THEN 'ALM128'
                    WHEN T3."SeriesName" IN ('134E', '134F', 'NCAU134', 'NDFAU134', 'NPAU134', 'RC-AU134', '134J') THEN 'ALM134'
                    WHEN T3."SeriesName" IN ('170E', '170F', '170J', 'NDFP170', 'NCPU170.', 'NPPU170', 'RC-PU170') THEN 'ALM170'
                    WHEN T3."SeriesName" IN ('7AGE', 'NC7AGOS.', 'NP7AGOS', 'RC-7AGOS', 'NDF7AGOS', '7AGJ', '7AGF') THEN '7AGOS'
                    WHEN T3."SeriesName" IN ('A19E', 'A19F', 'NCAVE19.', 'RC-AVE19', 'A19J', 'NDFAV19', 'NPAVE19') THEN 'AV19'
                    WHEN T3."SeriesName" IN ('ARME', 'ARMJ', 'NCARMEN.', 'NDFARMEN', 'NPARMEN', 'RC-ARMEN', 'ND-ARMEN', 'ARMF') THEN 'ARME'
                    WHEN T3."SeriesName" IN ('CHIE', 'CHIF', 'CHIJ', 'NCCHIA.', 'ND-CHIA', 'NPCHIA', 'RC-CHIA', 'NDFCHIA') THEN 'CHIA' 
                    WHEN T3."SeriesName" IN ('COME', 'COMJ', 'NCCOMER.', 'ND-COMER', 'NDFCOMER', 'NPCOMER', 'RC-COMER', 'COMF') THEN 'EJECOM' 
                    WHEN T3."SeriesName" IN ('CT1E', 'NCCENT1.', 'ND-CENT1', 'NDFCENT1', 'RC-CENT1', 'CT1J', 'NPCENT1', 'CT1F') THEN 'Q1' 
                    WHEN T3."SeriesName" IN ('CT3E', 'CT3F', 'NCCENT3.', 'NPCENT3', 'RC-CENT3', 'CT3J') THEN 'Q3' 
                    WHEN T3."SeriesName" IN ('CT5E', 'CT5F', 'NCCENT5.', 'NPCENT5', 'RC-CENT5', 'NDFCENT5', 'CT5J') THEN 'Q5' 
                    WHEN T3."SeriesName" IN ('CT6E', 'CT6F', 'CT6J', 'NCCENT6.', 'NPCENT6', 'RC-CENT6', 'NDFCENT6') THEN 'Q6' 
                    WHEN T3."SeriesName" IN ('VILE', 'VILF', 'VILJ', 'NCVILLA.', 'NPVILLA', 'RC-VILLA', 'ND-VILLA') THEN 'VILL'
                    WHEN T3."SeriesName" IN ('GIRE', 'GIRJ', 'GIRF', 'NPGIRAR', 'NCGIRAR') THEN 'GIRAR' 
                    ELSE COALESCE(T5."WhsName", 'Sin Almacen') 
                END AS "nombre_almacen",
                T0."OwnerCode" AS "propietario_doc",
                COALESCE(T6."Code", '') AS "codigo_colaborador_slp",
                COALESCE(T6."jobTitle", '') AS "documento_identidad",
                COALESCE(T8."Code", '') AS "codigo_colaborador_owner"
            FROM "NE042025".OINV T0
            INNER JOIN "NE042025".INV1 T1 ON T0."DocEntry" = T1."DocEntry"
            LEFT JOIN "NE042025".OUSR T2 ON T0."UserSign" = T2."USERID"
            LEFT JOIN "NE042025".NNM1 T3 ON T0."Series" = T3."Series"
            LEFT JOIN "NE042025".OSLP T4 ON T0."SlpCode" = T4."SlpCode"
            LEFT JOIN "NE042025".OWHS T5 ON T1."WhsCode" = T5."WhsCode"
            LEFT JOIN "NE042025".OHEM T6 ON T4."SlpCode" = T6."salesPrson"
            LEFT JOIN "NE042025".OUSR T7 ON T0."OwnerCode" = T7."USERID"
            LEFT JOIN "NE042025".OHEM T8 ON T7."INTERNAL_K" = T8."empID"
            WHERE
                T0."CANCELED" = 'N'
                AND T0."DocDate" >= '{FECHA_INICIO_AUDITORIA}'
                AND T0."DocDate" <= '{FECHA_FIN_AUDITORIA}'
                -- AND T0."DocDate" >= ADD_MONTHS(CURRENT_DATE, -{MESES_HISTORICO_VENTAS})
                -- AND T0."DocDate" <= CURRENT_DATE
                AND (T3."SeriesName" IS NULL 
                     OR T3."SeriesName" NOT IN ('INGE','NDFINGEN','INGF','NCINGEN.','ND-INGEN','NPINGEN','FactClie','RC-INGEN'))
                AND COALESCE(T5."WhsName", '') <> 'BODEGA INGENIERIA'

            UNION ALL

            SELECT
                T0."DocDate" AS "fecha_contabilizacion",
                T0."DocNum" AS "documento",
                T0."CardCode" AS "codigo_cliente",
                T0."CardName" AS "nombre_cliente",
                -T1."LineTotal" AS "precio_sin_iva",
                (T1."LineTotal" + T1."VatSum") AS "precio_con_iva",
                -(T1."LineTotal" - T1."GrssProfit") AS "costo_total",
                -T1."GrssProfit" AS "rentabilidad",
                T1."TaxCode" AS "tax_code",
                COALESCE(T2."U_NAME", 'Sin Usuario') AS "nombre_usuario",
                T0."SlpCode" AS "codigo_vendedor",
                COALESCE(T4."SlpName", 'Sin Vendedor') AS "nombre_vendedor",
                COALESCE(T3."SeriesName", '') AS "nombre_serie",
                COALESCE(T3."BeginStr", '') AS "prefijo_serie",
                T1."WhsCode" AS "codigo_almacen",
                CASE 
                    WHEN T3."SeriesName" IN ('128E', 'NCNT128.', 'ND-NT128', 'RC-NT128', 'NPNT128', '128J', '128F') THEN 'ALM128'
                    WHEN T3."SeriesName" IN ('134E', '134F', 'NCAU134', 'NDFAU134', 'NPAU134', 'RC-AU134', '134J') THEN 'ALM134'
                    WHEN T3."SeriesName" IN ('170E', '170F', '170J', 'NDFP170', 'NCPU170.', 'NPPU170', 'RC-PU170') THEN 'ALM170'
                    WHEN T3."SeriesName" IN ('7AGE', 'NC7AGOS.', 'NP7AGOS', 'RC-7AGOS', 'NDF7AGOS', '7AGJ', '7AGF') THEN '7AGOS'
                    WHEN T3."SeriesName" IN ('A19E', 'A19F', 'NCAVE19.', 'RC-AVE19', 'A19J', 'NDFAV19', 'NPAVE19') THEN 'AV19'
                    WHEN T3."SeriesName" IN ('ARME', 'ARMJ', 'NCARMEN.', 'NDFARMEN', 'NPARMEN', 'RC-ARMEN', 'ND-ARMEN', 'ARMF') THEN 'ARME'
                    WHEN T3."SeriesName" IN ('CHIE', 'CHIF', 'CHIJ', 'NCCHIA.', 'ND-CHIA', 'NPCHIA', 'RC-CHIA', 'NDFCHIA') THEN 'CHIA' 
                    WHEN T3."SeriesName" IN ('COME', 'COMJ', 'NCCOMER.', 'ND-COMER', 'NDFCOMER', 'NPCOMER', 'RC-COMER', 'COMF') THEN 'EJECOM' 
                    WHEN T3."SeriesName" IN ('CT1E', 'NCCENT1.', 'ND-CENT1', 'NDFCENT1', 'RC-CENT1', 'CT1J', 'NPCENT1', 'CT1F') THEN 'Q1' 
                    WHEN T3."SeriesName" IN ('CT3E', 'CT3F', 'NCCENT3.', 'NPCENT3', 'RC-CENT3', 'CT3J') THEN 'Q3' 
                    WHEN T3."SeriesName" IN ('CT5E', 'CT5F', 'NCCENT5.', 'NPCENT5', 'RC-CENT5', 'NDFCENT5', 'CT5J') THEN 'Q5' 
                    WHEN T3."SeriesName" IN ('CT6E', 'CT6F', 'CT6J', 'NCCENT6.', 'NPCENT6', 'RC-CENT6', 'NDFCENT6') THEN 'Q6' 
                    WHEN T3."SeriesName" IN ('VILE', 'VILF', 'VILJ', 'NCVILLA.', 'NPVILLA', 'RC-VILLA', 'ND-VILLA') THEN 'VILL' 
                    WHEN T3."SeriesName" IN ('GIRE', 'GIRJ', 'GIRF', 'NPGIRAR', 'NCGIRAR') THEN 'GIRAR' 
                    ELSE COALESCE(T5."WhsName", 'Sin Almacen') 
                END AS "nombre_almacen",
                T0."OwnerCode" AS "propietario_doc",
                COALESCE(T6."Code", '') AS "codigo_colaborador_slp",
                COALESCE(T6."jobTitle", '') AS "documento_identidad",
                COALESCE(T8."Code", '') AS "codigo_colaborador_owner"
            FROM "NE042025".ORIN T0
            INNER JOIN "NE042025".RIN1 T1 ON T0."DocEntry" = T1."DocEntry"
            LEFT JOIN "NE042025".OUSR T2 ON T0."UserSign" = T2."USERID"
            LEFT JOIN "NE042025".NNM1 T3 ON T0."Series" = T3."Series"
            LEFT JOIN "NE042025".OSLP T4 ON T0."SlpCode" = T4."SlpCode"
            LEFT JOIN "NE042025".OWHS T5 ON T1."WhsCode" = T5."WhsCode"
            LEFT JOIN "NE042025".OHEM T6 ON T4."SlpCode" = T6."salesPrson"
            LEFT JOIN "NE042025".OUSR T7 ON T0."OwnerCode" = T7."USERID"
            LEFT JOIN "NE042025".OHEM T8 ON T7."INTERNAL_K" = T8."empID"
            WHERE
                T0."CANCELED" = 'N'
                AND T0."DocDate" >= '{FECHA_INICIO_AUDITORIA}'
                AND T0."DocDate" <= '{FECHA_FIN_AUDITORIA}'
                -- AND T0."DocDate" >= ADD_MONTHS(CURRENT_DATE, -{MESES_HISTORICO_VENTAS})
                -- AND T0."DocDate" <= CURRENT_DATE
                AND (T3."SeriesName" IS NULL 
                     OR T3."SeriesName" NOT IN ('INGE','NDFINGEN','INGF','NCINGEN.','ND-INGEN','NPINGEN','FactClie','RC-INGEN'))
                AND COALESCE(T5."WhsName", '') <> 'BODEGA INGENIERIA'
        """

        # ============================================================
        #  QUERY: PAGOS APLICADOS (base de liquidación de bonos)
        # ============================================================
        query_bonos_pagos = f"""
            SELECT
                T0."DocNum" AS "pago_nro",
                T0."CardCode" AS "codigo_cliente",
                T0."CardName" AS "nombre_cliente",
                T0."DocDate" AS "fecha_pago",
                T0."DocDate" AS "fecha_aplicacion",
                T0."DocTotal" AS "total_pagado",
                T0."PrjCode" AS "proyecto",
                COALESCE(T5."SeriesName", '') AS "nombre_serie_pago",
                T2."DocNum" AS "nro_factura",
                T2."DocDate" AS "fecha_factura",
                T2."DocTotal" AS "total_factura_original",
                T1."SumApplied" AS "monto_aplicado",
                COALESCE(T4."SlpName", '') AS "nom_empleado",
                COALESCE(T6."SeriesName", '') AS "nombre_serie_factura",
                'Factura' AS "tipo_doc_pagado",
                'Pago Directo' AS "origen_relacion",
                'Pago Aplicado en el Mes' AS "estado_comision"
            FROM "NE042025"."ORCT" T0
            INNER JOIN "NE042025"."RCT2" T1 ON T0."DocEntry" = T1."DocNum"
            INNER JOIN "NE042025"."OINV" T2 ON T1."DocEntry" = T2."DocEntry" AND T1."InvType" = '13'
            INNER JOIN "NE042025"."OSLP" T4 ON T2."SlpCode" = T4."SlpCode"
            LEFT JOIN "NE042025"."NNM1" T5 ON T0."Series" = T5."Series"
            LEFT JOIN "NE042025"."NNM1" T6 ON T2."Series" = T6."Series"
            WHERE T0."Canceled" = 'N'
              -- AND T0."DocDate" >= ADD_MONTHS(CURRENT_DATE, -{{MESES_HISTORICO_VENTAS}})
              AND T0."DocDate" >= '{FECHA_INICIO_AUDITORIA}'
              AND T0."DocDate" <= '{FECHA_FIN_AUDITORIA}'
              AND T5."SeriesName" NOT IN ('INGE','NDFINGEN','INGF','NCINGEN.','ND-INGEN','NPINGEN','FactClie','RC-INGEN')

            UNION ALL

            SELECT
                T0."DocNum" AS "pago_nro",
                T0."CardCode" AS "codigo_cliente",
                T0."CardName" AS "nombre_cliente",
                T0."DocDate" AS "fecha_pago",
                T6."ReconDate" AS "fecha_aplicacion",
                T0."DocTotal" AS "total_pagado",
                T0."PrjCode" AS "proyecto",
                COALESCE(T5."SeriesName", '') AS "nombre_serie_pago",
                T4."DocNum" AS "nro_factura",
                T4."DocDate" AS "fecha_factura",
                T4."DocTotal" AS "total_factura_original",
                T3."ReconSum" AS "monto_aplicado",
                COALESCE(T7."SlpName", '') AS "nom_empleado",
                COALESCE(T8."SeriesName", '') AS "nombre_serie_factura",
                'Factura' AS "tipo_doc_pagado",
                'Reconciliación Interna' AS "origen_relacion",
                CASE
                    WHEN YEAR(T0."DocDate") = YEAR(T6."ReconDate") AND MONTH(T0."DocDate") = MONTH(T6."ReconDate")
                    THEN 'Pago Aplicado en el Mes'
                    ELSE 'Anticipo Aplicado Posterior'
                END AS "estado_comision"
            FROM "NE042025"."ORCT" T0
            INNER JOIN "NE042025"."ITR1" T2 ON T0."DocEntry" = T2."SrcObjAbs" AND T2."SrcObjTyp" = '24'
            INNER JOIN "NE042025"."ITR1" T3 ON T2."ReconNum" = T3."ReconNum" AND T3."SrcObjTyp" = '13'
            INNER JOIN "NE042025"."OINV" T4 ON T3."SrcObjAbs" = T4."DocEntry"
            LEFT JOIN "NE042025"."NNM1" T5 ON T0."Series" = T5."Series"
            INNER JOIN "NE042025"."OITR" T6 ON T2."ReconNum" = T6."ReconNum"
            INNER JOIN "NE042025"."OSLP" T7 ON T4."SlpCode" = T7."SlpCode"
            LEFT JOIN "NE042025"."NNM1" T8 ON T4."Series" = T8."Series"
            WHERE T0."Canceled" = 'N'
              AND T6."CancelAbs" = 0
              -- AND T0."DocDate" >= ADD_MONTHS(CURRENT_DATE, -{{MESES_HISTORICO_VENTAS}})
              AND T6."ReconDate" >= '{FECHA_INICIO_AUDITORIA}'
              AND T6."ReconDate" <= '{FECHA_FIN_AUDITORIA}'
              AND T5."SeriesName" NOT IN ('INGE','NDFINGEN','INGF','NCINGEN.','ND-INGEN','NPINGEN','FactClie','RC-INGEN')
              AND NOT EXISTS (
                  SELECT 1 FROM "NE042025"."RCT2" R
                  WHERE R."DocNum" = T0."DocEntry"
                    AND R."DocEntry" = T4."DocEntry"
                    AND R."InvType" = '13'
              )
        """ #.replace("{MESES_HISTORICO_VENTAS}", str(MESES_HISTORICO_VENTAS))   # quitar comentario para aplicar por meses

        # ============================================================
        # 🎁 QUERY: ANTICIPOS (recibos con aplicación diferida / saldo a cuenta)
        # ============================================================
        query_bonos_anticipos = f"""
            SELECT
                T0."DocNum" AS "pago_nro",
                T0."CardCode" AS "codigo_cliente",
                T0."CardName" AS "nombre_cliente",
                T0."DocDate" AS "fecha_recibo_pago",
                T0."DocTotal" AS "total_pago_original",
                T0."U_NAL_Tipo_Factura" AS "tipo_factura",
                COALESCE(T5."SeriesName", '') AS "nombre_serie_pago",
                T0."PrjCode" AS "proyecto",
                T4."ReconDate" AS "fecha_aplicacion",
                COALESCE(T2."ReconSum", 0) AS "monto_aplicado",
                T3."DocNum" AS "nro_factura_aplicada",
                T3."DocDate" AS "fecha_factura",
                COALESCE(T6."SeriesName", '') AS "nombre_serie_factura",
                CASE
                    WHEN T4."ReconNum" IS NULL THEN 'Anticipo Pendiente (Saldo a Favor)'
                    WHEN TO_VARCHAR(T0."DocDate", 'YYYYMM') < TO_VARCHAR(T4."ReconDate", 'YYYYMM')
                         THEN 'Anticipo Aplicado Mes Posterior'
                    ELSE 'Pago Aplicado en el mismo Mes'
                END AS "tipo_movimiento",
                CASE
                    WHEN T4."ReconNum" IS NULL THEN T0."OpenBal"
                    ELSE 0
                END AS "saldo_a_cuenta"
            FROM "NE042025"."ORCT" T0
            LEFT JOIN "NE042025"."NNM1" T5 ON T0."Series" = T5."Series"
            LEFT JOIN "NE042025"."ITR1" T1 ON T0."DocEntry" = T1."SrcObjAbs" AND T1."SrcObjTyp" = '24'
            LEFT JOIN "NE042025"."ITR1" T2 ON T1."ReconNum" = T2."ReconNum" AND T2."SrcObjTyp" = '13'
            LEFT JOIN "NE042025"."OINV" T3 ON T2."SrcObjAbs" = T3."DocEntry"
            LEFT JOIN "NE042025"."OITR" T4 ON T1."ReconNum" = T4."ReconNum" AND T4."CancelAbs" = 0
            LEFT JOIN "NE042025"."NNM1" T6 ON T3."Series" = T6."Series"
            WHERE T0."Canceled" = 'N'
              AND T5."SeriesName" NOT IN ('INGE','NDFINGEN','INGF','NCINGEN.','ND-INGEN','NPINGEN','FactClie','RC-INGEN')
              AND T0."DocDate" >= '{FECHA_INICIO_AUDITORIA}'
              AND T0."DocDate" <= '{FECHA_FIN_AUDITORIA}'
        """
        
        # ============================================================
        # 2. EJECUTAR QUERIES EN SAP
        # ============================================================
        df_remisiones = pd.read_sql(query_remisiones, conexion_sap)
        df_colaboradores = pd.read_sql(query_colaboradores, conexion_sap)
        df_cotizaciones = pd.read_sql(query_cotizaciones, conexion_sap)
        df_ordenes = pd.read_sql(query_ordenes, conexion_sap)
        df_solicitudes = pd.read_sql(query_solicitudes, conexion_sap)
        df_traslados = pd.read_sql(query_traslados, conexion_sap)
        df_facturas = pd.read_sql(query_facturas, conexion_sap)
        df_notas = pd.read_sql(query_notas, conexion_sap)
        df_ventas_netas = pd.read_sql(query_ventas_netas, conexion_sap)
        df_bonos_pagos = pd.read_sql(query_bonos_pagos, conexion_sap)
        df_bonos_anticipos = pd.read_sql(query_bonos_anticipos, conexion_sap)        

        conexion_sap.close()

        # ============================================================
        # 3. CONEXIÓN A POSTGRES Y CARGA DE DATOS
        # ============================================================
        pg_engine = create_engine(CADENA_CONEXION_PG)

        sql_vista_rem = """CREATE OR REPLACE VIEW sap_raw.vw_remisiones_con_rangos AS SELECT r.*, sap_raw.calcular_rango_dias(r."Fecha_Contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.remisiones r;"""
        sql_vista_cot = """CREATE OR REPLACE VIEW sap_raw.vw_cotizaciones_con_rangos AS SELECT c.*, sap_raw.calcular_rango_dias(c."Fecha_Contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.cotizaciones c;"""
        sql_vista_ord = """CREATE OR REPLACE VIEW sap_raw.vw_ordenes_venta_con_rangos AS SELECT o.*, sap_raw.calcular_rango_dias(o."Fecha_Contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.ordenes_venta o;"""
        sql_vista_sol = """CREATE OR REPLACE VIEW sap_raw.vw_solicitudes_compras_con_rangos AS SELECT s.*, sap_raw.calcular_rango_dias(s."Fecha_Contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.solicitudes_compras s;"""
        sql_vista_tra = """CREATE OR REPLACE VIEW sap_raw.vw_solicitudes_traslados_con_rangos AS SELECT s.*, sap_raw.calcular_rango_dias(s."Fecha_Contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.solicitudes_traslados s;"""
        sql_vista_fac = """CREATE OR REPLACE VIEW sap_raw.vw_facturas_reserva_con_rangos AS SELECT f.*, sap_raw.calcular_rango_dias(f."Fecha_Contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.facturas_reserva f;"""
        sql_vista_nc = """CREATE OR REPLACE VIEW sap_raw.vw_notas_credito_con_rangos AS SELECT n.*, sap_raw.calcular_rango_dias(n."Fecha_Contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.notas_credito n;"""
        sql_vista_ventas = """CREATE OR REPLACE VIEW sap_raw.vw_ventas_netas_con_rangos AS SELECT v.*, sap_raw.calcular_rango_dias(v."fecha_contabilizacion"::date) AS "Rango_Dias" FROM sap_raw.ventas_netas v;"""

        with pg_engine.begin() as pg_conn:
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_remisiones_con_rangos CASCADE;"))
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_cotizaciones_con_rangos CASCADE;"))
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_ordenes_venta_con_rangos CASCADE;"))
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_solicitudes_compras_con_rangos CASCADE;"))
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_solicitudes_traslados_con_rangos CASCADE;"))
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_facturas_reserva_con_rangos CASCADE;"))
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_notas_credito_con_rangos CASCADE;"))
            pg_conn.execute(text("DROP VIEW IF EXISTS sap_raw.vw_ventas_netas_con_rangos CASCADE;"))

        df_remisiones.to_sql('remisiones', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_colaboradores.to_sql('colaboradores', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_cotizaciones.to_sql('cotizaciones', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_ordenes.to_sql('ordenes_venta', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_solicitudes.to_sql('solicitudes_compras', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_traslados.to_sql('solicitudes_traslados', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_facturas.to_sql('facturas_reserva', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_notas.to_sql('notas_credito', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_ventas_netas.to_sql('ventas_netas', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_bonos_pagos.to_sql('bonos_pagos', pg_engine, schema='sap_raw', if_exists='replace', index=False)
        df_bonos_anticipos.to_sql('bonos_anticipos', pg_engine, schema='sap_raw', if_exists='replace', index=False)

        with pg_engine.begin() as pg_conn:
            pg_conn.execute(text(sql_vista_rem))
            pg_conn.execute(text(sql_vista_cot))
            pg_conn.execute(text(sql_vista_ord))
            pg_conn.execute(text(sql_vista_sol))
            pg_conn.execute(text(sql_vista_tra))
            pg_conn.execute(text(sql_vista_fac))
            pg_conn.execute(text(sql_vista_nc))
            pg_conn.execute(text(sql_vista_ventas))

            pg_conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ventas_fecha ON sap_raw.ventas_netas (fecha_contabilizacion)"))
            pg_conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ventas_almacen ON sap_raw.ventas_netas (codigo_almacen)"))
            pg_conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ventas_vendedor ON sap_raw.ventas_netas (codigo_vendedor)"))
            pg_conn.execute(text("CREATE INDEX IF NOT EXISTS idx_bonos_fecha ON sap_raw.bonos_pagos (fecha_pago)"))
            pg_conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ant_fecha ON sap_raw.bonos_anticipos (fecha_recibo_pago)"))

        return True
    except Exception as e:
        st.error(f"❌ Error en sincronización: {e}")
        return False


# ============================================================
# FUNCIONES DE CARGA Y TRANSFORMACIÓN (módulos existentes)
# ============================================================
@st.cache_data(ttl=600)
def cargar_y_transformar_remisiones():
    try:
        df = conn.query("SELECT * FROM sap_raw.vw_remisiones_con_rangos")
        columnas_esperadas = ['Documento', 'Cliente', 'Fecha_Contabilizacion', 'Status_Documento', 'Empleado_Ventas', 'Propietario_Doc', 'Sede_Codigo', 'Numero_Articulo', 'Descripcion', 'Cantidad', 'Precio', 'Precio_Sin_IVA', 'Precio_Total', 'Almacen', 'Nombre_Usuario', 'Linea', 'Marca', 'Colaborador', 'Rango_Dias']
        mapeo = {c.lower().strip(): c for c in columnas_esperadas}
        df.columns = [mapeo.get(c.lower().strip(), c) for c in df.columns]
        df['Fecha_Contabilizacion'] = pd.to_datetime(df['Fecha_Contabilizacion'], errors='coerce')
        df['Dias'] = (pd.Timestamp.now().normalize() - df['Fecha_Contabilizacion']).dt.days
        df['Fecha_Texto'] = df['Fecha_Contabilizacion'].dt.strftime('%d/%m/%Y')
        if 'Colaborador' not in df.columns: df['Colaborador'] = "Sin Asignar"
        else: df['Colaborador'] = df['Colaborador'].fillna('Sin Asignar').replace('', 'Sin Asignar')
        return df
    except Exception as e:
        st.error(f"Error analizando datos en Postgres: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=600)
def cargar_y_transformar_cotizaciones():
    try:
        df = conn.query("SELECT * FROM sap_raw.vw_cotizaciones_con_rangos")
        columnas_esperadas = ['Documento', 'Numero_Cliente', 'Cliente', 'Fecha_Contabilizacion', 'Status_Documento', 'Empleado_Ventas', 'Propietario_Doc', 'Sede_Codigo', 'Numero_Articulo', 'Descripcion', 'Cantidad', 'Precio', 'Precio_Sin_IVA', 'Precio_Total', 'Rentabilidad', 'Almacen', 'Nombre_Usuario', 'Cupo_Credito', 'Colaborador', 'Rango_Dias']
        mapeo = {c.lower().strip(): c for c in columnas_esperadas}
        df.columns = [mapeo.get(c.lower().strip(), c) for c in df.columns]
        df['Fecha_Contabilizacion'] = pd.to_datetime(df['Fecha_Contabilizacion'], errors='coerce')
        df['Dias'] = (pd.Timestamp.now().normalize() - df['Fecha_Contabilizacion']).dt.days
        df['Fecha_Texto'] = df['Fecha_Contabilizacion'].dt.strftime('%d/%m/%Y')
        return df
    except Exception as e:
        st.error(f"Error analizando cotizaciones en Postgres: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=600)
def cargar_y_transformar_ordenes():
    try:
        df = conn.query("SELECT * FROM sap_raw.vw_ordenes_venta_con_rangos")
        columnas_esperadas = ['Documento', 'Numero_Cliente', 'Cliente', 'Fecha_Contabilizacion', 'Status_Documento', 'Empleado_Ventas', 'Propietario_Doc', 'Sede_Codigo', 'Numero_Articulo', 'Descripcion', 'Cantidad', 'Precio', 'Precio_Sin_IVA', 'Precio_Total', 'Almacen', 'Nombre_Usuario', 'Colaborador', 'Rango_Dias']
        mapeo = {c.lower().strip(): c for c in columnas_esperadas}
        df.columns = [mapeo.get(c.lower().strip(), c) for c in df.columns]
        df['Fecha_Contabilizacion'] = pd.to_datetime(df['Fecha_Contabilizacion'], errors='coerce')
        df['Dias'] = (pd.Timestamp.now().normalize() - df['Fecha_Contabilizacion']).dt.days
        df['Fecha_Texto'] = df['Fecha_Contabilizacion'].dt.strftime('%d/%m/%Y')
        return df
    except Exception as e:
        st.error(f"Error analizando órdenes de venta en Postgres: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=600)
def cargar_y_transformar_solicitudes():
    try:
        df = conn.query("SELECT * FROM sap_raw.vw_solicitudes_compras_con_rangos")
        columnas_esperadas = ['Documento', 'Observaciones', 'Fecha_Contabilizacion', 'Status_Documento', 'Empleado_Ventas', 'Propietario_Doc', 'Sede_Codigo', 'Numero_Articulo', 'Descripcion', 'Cantidad', 'Precio', 'Precio_Sin_IVA', 'Precio_Total', 'Almacen', 'Nombre_Usuario', 'Colaborador', 'Rango_Dias']
        mapeo = {c.lower().strip(): c for c in columnas_esperadas}
        df.columns = [mapeo.get(c.lower().strip(), c) for c in df.columns]
        df['Fecha_Contabilizacion'] = pd.to_datetime(df['Fecha_Contabilizacion'], errors='coerce')
        df['Dias'] = (pd.Timestamp.now().normalize() - df['Fecha_Contabilizacion']).dt.days
        df['Fecha_Texto'] = df['Fecha_Contabilizacion'].dt.strftime('%d/%m/%Y')
        return df
    except Exception as e:
        st.error(f"Error analizando solicitudes en Postgres: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=600)
def cargar_y_transformar_traslados():
    try:
        df = conn.query("SELECT * FROM sap_raw.vw_solicitudes_traslados_con_rangos")
        columnas_esperadas = [
            'Documento', 'Observaciones', 'Almacen_Origen', 'Fecha_Contabilizacion', 'Status_Documento',
            'Propietario_Doc', 'Numero_Articulo', 'Descripcion', 'Cantidad', 'Precio',
            'Precio_Sin_IVA', 'Precio_Total', 'Almacen', 'Nombre_Usuario', 'Colaborador', 'Rango_Dias'
        ]
        mapeo = {c.lower().strip(): c for c in columnas_esperadas}
        df.columns = [mapeo.get(c.lower().strip(), c) for c in df.columns]
        df['Fecha_Contabilizacion'] = pd.to_datetime(df['Fecha_Contabilizacion'], errors='coerce')
        df['Dias'] = (pd.Timestamp.now().normalize() - df['Fecha_Contabilizacion']).dt.days
        df['Fecha_Texto'] = df['Fecha_Contabilizacion'].dt.strftime('%d/%m/%Y')
        return df
    except Exception as e:
        st.error(f"Error analizando traslados en Postgres: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=600)
def cargar_y_transformar_facturas():
    try:
        df = conn.query("SELECT * FROM sap_raw.vw_facturas_reserva_con_rangos")
        columnas_esperadas = [
            'Documento', 'Numero_Cliente', 'Cliente', 'Fecha_Contabilizacion', 'Status_Documento',
            'Empleado_Ventas', 'Propietario_Doc', 'Sede_Codigo', 'Numero_Articulo',
            'Descripcion', 'Cantidad', 'Precio', 'Precio_Sin_IVA', 'Precio_Total',
            'Almacen', 'Nombre_Usuario', 'Colaborador', 'Rango_Dias'
        ]
        mapeo = {c.lower().strip(): c for c in columnas_esperadas}
        df.columns = [mapeo.get(c.lower().strip(), c) for c in df.columns]
        df['Fecha_Contabilizacion'] = pd.to_datetime(df['Fecha_Contabilizacion'], errors='coerce')
        df['Dias'] = (pd.Timestamp.now().normalize() - df['Fecha_Contabilizacion']).dt.days
        df['Fecha_Texto'] = df['Fecha_Contabilizacion'].dt.strftime('%d/%m/%Y')
        return df
    except Exception as e:
        st.error(f"Error analizando facturas reserva en Postgres: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=600)
def cargar_y_transformar_notas():
    try:
        df = conn.query("SELECT * FROM sap_raw.vw_notas_credito_con_rangos")
        columnas_esperadas = [
            'Documento', 'Numero_Cliente', 'Cliente', 'Fecha_Contabilizacion', 'Status_Documento',
            'Empleado_Ventas', 'Propietario_Doc', 'Sede_Codigo', 'Numero_Articulo',
            'Descripcion', 'Cantidad', 'Precio', 'Precio_Sin_IVA', 'Precio_Total',
            'Almacen', 'Nombre_Usuario', 'Colaborador', 'Rango_Dias'
        ]
        mapeo = {c.lower().strip(): c for c in columnas_esperadas}
        df.columns = [mapeo.get(c.lower().strip(), c) for c in df.columns]
        df['Fecha_Contabilizacion'] = pd.to_datetime(df['Fecha_Contabilizacion'], errors='coerce')
        df['Dias'] = (pd.Timestamp.now().normalize() - df['Fecha_Contabilizacion']).dt.days
        df['Fecha_Texto'] = df['Fecha_Contabilizacion'].dt.strftime('%d/%m/%Y')
        return df
    except Exception as e:
        st.error(f"Error analizando notas crédito en Postgres: {e}")
        return pd.DataFrame()

# ============================================================
# 🎛️ METAS DE VENTAS POR PERÍODO (valores por defecto para nuevos períodos)
# ============================================================
METAS_2025 = [
    (0, True, 1_380_000_000, False, 0.00),
    (1_380_000_000, True, 1_955_000_000, False, 0.33),
    (1_955_000_000, True, 2_300_000_000, False, 0.67),
    (2_300_000_000, True, None, False, 1.00),
]

METAS_2026 = [
    (0, True, 2_190_000_000, False, 0.00),
    (2_190_000_000, True, 2_580_000_000, False, 0.67),
    (2_580_000_000, True, None, False, 1.00),
]


# ============================================================
#  FUNCIÓN: CARGA Y TRANSFORMACIÓN DE VENTAS NETAS
# ============================================================
@st.cache_data(ttl=600)
def cargar_y_transformar_ventas():
    """Lee ventas netas desde PostgreSQL"""
    try:
        df = conn.query("SELECT * FROM sap_raw.ventas_netas")
        if df.empty:
            return pd.DataFrame()

        df["fecha_contabilizacion"] = pd.to_datetime(df["fecha_contabilizacion"], errors="coerce")
        for col in ["precio_sin_iva", "costo_total", "rentabilidad"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        
        # ✅ Asignación directa (el SQL ya trae códigos cortos)
        df["Almacen_Corto"] = df["nombre_almacen"]
        df["Mes_Texto"] = df["fecha_contabilizacion"].dt.strftime("%b %Y")
        df["Dias"] = (pd.Timestamp.now().normalize() - df["fecha_contabilizacion"]).dt.days
        return df
    except Exception as e:
        st.error(f"Error analizando ventas netas en Postgres: {e}")
        return pd.DataFrame()


# ============================================================
# ✅ FUNCIÓN ORIGINAL: RLS GENERAL (Para Remisiones, Cotizaciones, etc.)
# ============================================================
def aplicar_seguridad_rls(df):
    import streamlit as st
    if df is None or df.empty: 
        return df
    rol = st.session_state.get('rol_actual', 'comercial')
    df_filtrado = df.copy()
    
    if 'Propietario_Doc' in df_filtrado.columns: 
        df_filtrado['Propietario_Doc'] = df_filtrado['Propietario_Doc'].astype(str).str.strip()
    if 'Empleado_Ventas' in df_filtrado.columns: 
        df_filtrado['Empleado_Ventas'] = df_filtrado['Empleado_Ventas'].astype(str).str.strip()
    if 'Sede_Codigo' in df_filtrado.columns: 
        df_filtrado['Sede_Codigo'] = df_filtrado['Sede_Codigo'].astype(str).str.strip()
        
    if rol in ["admin", "gerente", "gerente_comercial"]: 
        return df_filtrado
    elif rol == "admin_punto":
        sap_branch = st.session_state.get('sap_branch_code')
        if sap_branch and str(sap_branch).strip().lower() not in ['none', '']:
            sap_branch = str(sap_branch).strip().upper()
            if 'Sede_Codigo' in df_filtrado.columns:
                return df_filtrado[df_filtrado['Sede_Codigo'].str.upper() == sap_branch]
        return df_filtrado.iloc[0:0]
    elif rol == "comercial":
        sap_owner = str(st.session_state.get('sap_owner_code', '')).strip()
        sap_slp = str(st.session_state.get('sap_slp_code', '')).strip()
        condicion_owner = df_filtrado['Propietario_Doc'] == sap_owner if sap_owner else False
        condicion_slp = df_filtrado['Empleado_Ventas'] == sap_slp if sap_slp else False
        return df_filtrado[condicion_owner | condicion_slp]
    return df_filtrado


# ============================================================
# FUNCIÓN: RLS ESPECÍFICO PARA VENTAS (Metas) - CORREGIDA FINAL
# ============================================================
def aplicar_seguridad_rls_ventas(df: pd.DataFrame) -> pd.DataFrame:
    """RLS específico para el módulo de ventas con metas."""
    if df is None or df.empty:
        return df
    
    rol = st.session_state.get("rol_actual", "comercial")
    df_filtrado = df.copy()

    # ✅ SOLUCIÓN DEFINITIVA: Limpieza robusta de códigos (maneja 466, 466.0, '466.0', etc.)
    def clean_code(val):
        if pd.isna(val):
            return ''
        try:
            # Forzar a float primero para manejar strings como '466.0' o números
            num = float(val)
            if num.is_integer():
                return str(int(num))  # Convierte 466.0 a '466'
            return str(num)
        except (ValueError, TypeError):
            return str(val).strip()



    # Aplicar limpieza a las columnas clave
    if 'propietario_doc' in df_filtrado.columns:
        df_filtrado['propietario_doc'] = df_filtrado['propietario_doc'].apply(clean_code)
    if 'codigo_vendedor' in df_filtrado.columns:
        df_filtrado['codigo_vendedor'] = df_filtrado['codigo_vendedor'].apply(clean_code)
    if 'Almacen_Corto' in df_filtrado.columns:
        df_filtrado['Almacen_Corto'] = df_filtrado['Almacen_Corto'].astype(str).str.strip().str.upper()

    # Roles gerenciales ven todo
    if rol in ["admin", "gerente", "gerente_comercial"]:
        return df_filtrado

    # ==========================================
    # ROL: admin_punto (FILTRADO POR ALMACÉN)
    # ==========================================
    if rol == "admin_punto":
        sap_branch = st.session_state.get('sap_branch_code')
        if sap_branch is None or str(sap_branch).strip().lower() in ['none', '', 'nan']:
            return df_filtrado.iloc[0:0]
        
        branch_str = str(sap_branch).strip().upper()
        mapa_inverso = {
            "EJECUTIVOS COMERCIALES": "EJECOM", "PUNTO 134": "ALM134", "7 DE AGOSTO": "7AGOS", 
            "AVENIDA19": "AV19", "CENTRO 1": "Q1", "CENTRO 3": "Q3", "CENTRO 5": "Q5", "CENTRO 6": "Q6",
            "PUNTO170": "ALM170", "NORTE128": "ALM128", "VILLAVICENCIO": "VILL", "ARMENIA": "ARME", "CHIA": "CHIA",
            "GIRARDOT": "GIRAR",
        }
        codigo_corto = mapa_inverso.get(branch_str, branch_str)
        
        if 'Almacen_Corto' in df_filtrado.columns:
            return df_filtrado[df_filtrado['Almacen_Corto'] == codigo_corto].copy()
        return df_filtrado.iloc[0:0]

    # ==========================================
    # ROL: comercial (FILTRADO ESTRICTO POR PROPIETARIO_DOC, IGUAL QUE POWER BI)
    # ==========================================
    elif rol == "comercial":
        sap_owner_raw = st.session_state.get('sap_owner_code')
        
        if sap_owner_raw is None:
            return df_filtrado.iloc[0:0]
        
        sap_owner_clean = clean_code(sap_owner_raw)
        
        if not sap_owner_clean or sap_owner_clean.lower() in ['none', 'nan', '']:
            return df_filtrado.iloc[0:0]
        
        # ✅ CORRECCIÓN CRÍTICA: Filtrar SOLO por propietario_doc. 
        # Se eliminó por completo el operador OR con codigo_vendedor.
        # Esto evita que aparezcan documentos de otros almacenes (ej. VILL) 
        # solo porque el comercial los creó (OwnerCode), respetando la lógica de Power BI.
        if 'propietario_doc' in df_filtrado.columns:
            df_resultado = df_filtrado[df_filtrado['propietario_doc'] == sap_owner_clean].copy()
        else:
            df_resultado = df_filtrado.iloc[0:0]
        
        return df_resultado

    return df_filtrado



# ============================================================
# 🆕 ROTACIÓN DE ARTÍCULOS (Módulo Compras)
# ============================================================
def sincronizar_rotacion_articulos():
    """Carga incremental: la 1ª vez trae todo desde 2025-04-01;
    después solo recarga los últimos 3 días (correcciones) + días nuevos."""
    try:
        sap_conf = st.secrets["sap"]
        conn_str_sap = (
            f"DRIVER={sap_conf['driver']};"
            f"SERVERNODE={sap_conf['server']};"
            f"UID={sap_conf['user']};"
            f"PWD={sap_conf['password']}"
        )
        conexion_sap = pyodbc.connect(conn_str_sap)
        pg_engine = create_engine(CADENA_CONEXION_PG)

        # 1. Determinar fecha de inicio (blindado: nunca antes de 2025-04-01)
        fecha_inicio = "2025-04-01"
        try:
            with pg_engine.connect() as c:
                max_fecha = c.execute(text(
                    "SELECT MAX(fecha_contabilizacion) FROM sap_raw.rotacion_articulos"
                )).scalar()
            if max_fecha is not None:
                desde = pd.Timestamp(max_fecha) - pd.Timedelta(days=2)
                if desde < pd.Timestamp("2025-04-01"):
                    desde = pd.Timestamp("2025-04-01")
                fecha_inicio = desde.strftime("%Y-%m-%d")
                # Borrar la ventana que se va a recargar (evita duplicados)
                with pg_engine.begin() as c:
                    c.execute(text(
                        "DELETE FROM sap_raw.rotacion_articulos WHERE fecha_contabilizacion >= :f"
                    ), {"f": fecha_inicio})
        except Exception:
            pass  # La tabla no existe aún → carga inicial completa

        # 2. Query SAP: Facturas - Notas Crédito (SIN excluir Ingeniería)
        query_rotacion = f"""
            SELECT
                T0."DocDate" AS "fecha_contabilizacion",
                T0."DocNum" AS "documento",
                T1."ItemCode" AS "codigo_articulo",
                COALESCE(T4."ItemName", 'Sin Nombre') AS "nombre_articulo",
                COALESCE(T5."ItmsGrpNam", 'Sin Línea') AS "nombre_linea",
                T1."Quantity" AS "cantidad",
                T1."LineTotal" AS "precio_sin_iva",
                (T1."LineTotal" + T1."VatSum") AS "precio_con_iva",
                T1."WhsCode" AS "codigo_almacen",
                COALESCE(T2."WhsName", 'Sin Almacen') AS "nombre_almacen"
            FROM "NE042025".OINV T0
            INNER JOIN "NE042025".INV1 T1 ON T0."DocEntry" = T1."DocEntry"
            LEFT JOIN "NE042025".OWHS T2 ON T1."WhsCode" = T2."WhsCode"
            LEFT JOIN "NE042025".OITM T4 ON T1."ItemCode" = T4."ItemCode"
            LEFT JOIN "NE042025".OITB T5 ON T4."ItmsGrpCod" = T5."ItmsGrpCod"
            WHERE T0."CANCELED" = 'N' AND T0."DocDate" >= '{fecha_inicio}'

            UNION ALL

            SELECT
                T0."DocDate" AS "fecha_contabilizacion",
                T0."DocNum" AS "documento",
                T1."ItemCode" AS "codigo_articulo",
                COALESCE(T4."ItemName", 'Sin Nombre') AS "nombre_articulo",
                COALESCE(T5."ItmsGrpNam", 'Sin Línea') AS "nombre_linea",
                -T1."Quantity" AS "cantidad",
                -T1."LineTotal" AS "precio_sin_iva",
                -(T1."LineTotal" + T1."VatSum") AS "precio_con_iva",
                T1."WhsCode" AS "codigo_almacen",
                COALESCE(T2."WhsName", 'Sin Almacen') AS "nombre_almacen"
            FROM "NE042025".ORIN T0
            INNER JOIN "NE042025".RIN1 T1 ON T0."DocEntry" = T1."DocEntry"
            LEFT JOIN "NE042025".OWHS T2 ON T1."WhsCode" = T2."WhsCode"
            LEFT JOIN "NE042025".OITM T4 ON T1."ItemCode" = T4."ItemCode"
            LEFT JOIN "NE042025".OITB T5 ON T4."ItmsGrpCod" = T5."ItmsGrpCod"
            WHERE T0."CANCELED" = 'N' AND T0."DocDate" >= '{fecha_inicio}'
        """
        df_rot = pd.read_sql(query_rotacion, conexion_sap)
        conexion_sap.close()

        # 3. Agregar a PostgreSQL (la tabla se crea sola la primera vez)
        if not df_rot.empty:
            df_rot.to_sql("rotacion_articulos", pg_engine, schema="sap_raw",
                          if_exists="append", index=False)

        # 4. Índices para que el dashboard vuele
        with pg_engine.begin() as c:
            c.execute(text("CREATE INDEX IF NOT EXISTS idx_rot_fecha ON sap_raw.rotacion_articulos (fecha_contabilizacion)"))
            c.execute(text("CREATE INDEX IF NOT EXISTS idx_rot_art ON sap_raw.rotacion_articulos (codigo_articulo)"))
            c.execute(text("CREATE INDEX IF NOT EXISTS idx_rot_lin ON sap_raw.rotacion_articulos (nombre_linea)"))
            c.execute(text("CREATE INDEX IF NOT EXISTS idx_rot_alm ON sap_raw.rotacion_articulos (nombre_almacen)"))

        print(f"✅ Rotación sincronizada: {len(df_rot)} filas desde {fecha_inicio}")
        return True
    except Exception as e:
        print(f"❌ Error sincronizando rotación: {e}")
        return False


# ============================================================
# 🆕 CARGA DE DIMENSIONES (filtros del sidebar, caché 1 hora)
# ============================================================
@st.cache_data(ttl=3600, show_spinner="Cargando dimensiones...")
def cargar_dimensiones_rotacion():
    try:
        df_anios = conn.query("""
            SELECT DISTINCT EXTRACT(YEAR FROM fecha_contabilizacion)::int AS anio
            FROM sap_raw.rotacion_articulos
            WHERE fecha_contabilizacion >= '2025-04-01' ORDER BY 1
        """)
        df_almacenes = conn.query("""
            SELECT DISTINCT nombre_almacen FROM sap_raw.rotacion_articulos
            ORDER BY nombre_almacen
        """)
        df_art_linea = conn.query("""
            SELECT DISTINCT nombre_linea, codigo_articulo, nombre_articulo
            FROM sap_raw.rotacion_articulos
            ORDER BY nombre_linea, codigo_articulo
        """)
        return df_anios, df_almacenes, df_art_linea
    except Exception as e:
        st.error(f"Error cargando dimensiones de rotación: {e}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()


# ============================================================
# 🆕 FUNCIÓN: CARGA Y TRANSFORMACIÓN DE ROTACIÓN DE ARTÍCULOS
# ============================================================
@st.cache_data(ttl=3600, show_spinner="Cargando datos de rotación...")
def cargar_y_transformar_rotacion(
    anios=None, 
    meses=None, 
    almacenes=None, 
    lineas=None, 
    articulos=None
):
    """
    Carga datos de rotación con filtros aplicados en PostgreSQL.
    Cache de 1 hora porque son datos históricos.
    """
    try:
        # Construir query dinámico con filtros
        query = "SELECT * FROM sap_raw.rotacion_articulos WHERE 1=1"
        params = {}
        
        if anios:
            query += " AND EXTRACT(YEAR FROM fecha_contabilizacion) = ANY(:anios)"
            params['anios'] = anios
        
        if meses:
            query += " AND EXTRACT(MONTH FROM fecha_contabilizacion) = ANY(:meses)"
            params['meses'] = meses
        
        if almacenes and almacenes != ['Todos']:
            query += " AND nombre_almacen = ANY(:almacenes)"
            params['almacenes'] = almacenes
        
        if lineas and lineas != ['Todas']:
            query += " AND nombre_linea = ANY(:lineas)"
            params['lineas'] = lineas
        
        if articulos and articulos != ['Todos']:
            query += " AND codigo_articulo = ANY(:articulos)"
            params['articulos'] = articulos
        
        df = conn.query(query, params=params)
        
        if df.empty:
            return pd.DataFrame()
        
        # Transformaciones
        df["fecha_contabilizacion"] = pd.to_datetime(df["fecha_contabilizacion"])
        df["anio"] = df["fecha_contabilizacion"].dt.year
        df["mes"] = df["fecha_contabilizacion"].dt.month
        df["mes_nombre"] = df["fecha_contabilizacion"].dt.strftime("%B")
        df["anio_mes"] = df["fecha_contabilizacion"].dt.strftime("%Y-%m")
        
        return df
        
    except Exception as e:
        st.error(f"Error cargando rotación: {e}")
        return pd.DataFrame()


# ============================================================
# 🆕 SINCRONIZACIÓN DE INVENTARIO (SNAPSHOT - REFRESCO TOTAL)
# ============================================================
def sincronizar_inventario_articulos():
    """Inventario = foto del momento: se reemplaza COMPLETO en cada corrida.
    ✅ SIN exclusiones de almacén: coincide con SAP B1 y Power BI."""
    try:
        sap_conf = st.secrets["sap"]
        conn_str_sap = (
            f"DRIVER={sap_conf['driver']};"
            f"SERVERNODE={sap_conf['server']};"
            f"UID={sap_conf['user']};"
            f"PWD={sap_conf['password']}"
        )
        conexion_sap = pyodbc.connect(conn_str_sap)
        pg_engine = create_engine(CADENA_CONEXION_PG)

        # 🎯 Query alineado con Power BI, SIN cláusula WHERE de exclusión.
        # Se conservan alias snake_case y cálculos idénticos al DAX.
        query_inventario = """
            SELECT
                T0."ItemCode"        AS "codigo_articulo",
                COALESCE(T0."ItemName", 'Sin Nombre') AS "nombre_articulo",
                T0."ItmsGrpCod"      AS "codigo_linea",
                COALESCE(T3."ItmsGrpNam", 'Sin Línea') AS "nombre_linea",
                COALESCE(T0."AvgPrice", 0) AS "costo_articulo",
                COALESCE(T0."LastPurPrc", 0) AS "ultimo_precio_compra",
                T0."LastPurDat"      AS "ultima_fecha_compra",
                COALESCE(T0."U_NAL_CATEGORIA", '')  AS "categoria",
                COALESCE(T0."U_NAL_SUBGRUPO1", '')  AS "subgrupo1",
                COALESCE(T0."U_NAL_SUBGRUPO2", '')  AS "subgrupo2",
                T1."WhsCode"         AS "codigo_almacen",
                COALESCE(T2."WhsName", 'Sin Almacen') AS "nombre_almacen",
                COALESCE(T1."OnHand", 0)     AS "on_hand",
                COALESCE(T1."IsCommited", 0) AS "comprometido",
                COALESCE(T1."OnOrder", 0)    AS "on_order",
                COALESCE(T1."AvgPrice", 0)   AS "precio_promedio_almacen",
                COALESCE(T1."StockValue", 0) AS "valor_stock",
                COALESCE(T0."U_NAL_UnidPaquete", 0) AS "unid_paquete",
                COALESCE(T0."U_Nal_UN_EM_PRO", 0)   AS "unid_empaque_pro",
                (COALESCE(T1."OnHand",0) + COALESCE(T1."OnOrder",0) - COALESCE(T1."IsCommited",0)) AS "disponible",
                (COALESCE(T0."AvgPrice",0) * (COALESCE(T1."OnHand",0) + COALESCE(T1."OnOrder",0) - COALESCE(T1."IsCommited",0))) AS "costo_disponible"
            FROM "NE042025".OITM T0
            INNER JOIN "NE042025".OITW T1 ON T0."ItemCode" = T1."ItemCode"
            INNER JOIN "NE042025".OWHS T2 ON T1."WhsCode" = T2."WhsCode"
            INNER JOIN "NE042025".OITB T3 ON T0."ItmsGrpCod" = T3."ItmsGrpCod"
        """

        print("📦 Consultando inventario desde SAP (todos los almacenes)...")
        df_inv = pd.read_sql(query_inventario, conexion_sap)
        conexion_sap.close()
        print(f"📊 Obtenidas {len(df_inv)} filas de inventario")

        # Refresco total (snapshot): replace borra y recrea la tabla
        df_inv.to_sql("inventario_articulos", pg_engine, schema="sap_raw",
                      if_exists="replace", index=False)

        with pg_engine.begin() as c:
            c.execute(text("CREATE INDEX IF NOT EXISTS idx_inv_art ON sap_raw.inventario_articulos (codigo_articulo)"))
            c.execute(text("CREATE INDEX IF NOT EXISTS idx_inv_alm ON sap_raw.inventario_articulos (nombre_almacen)"))
            c.execute(text("CREATE INDEX IF NOT EXISTS idx_inv_lin ON sap_raw.inventario_articulos (nombre_linea)"))

        print(f"✅ Inventario sincronizado: {len(df_inv)} filas (snapshot completo, sin exclusiones)")
        return True
    except Exception as e:
        print(f"❌ Error sincronizando inventario: {e}")
        import traceback
        traceback.print_exc()
        return False

# ============================================================
# 🆕 CARGA DE INVENTARIO (caché 1 hora)
# ============================================================
@st.cache_data(ttl=3600, show_spinner="Cargando inventario...")
def cargar_inventario_articulos():
    """Lee el snapshot completo de inventario desde PostgreSQL."""
    try:
        return conn.query("SELECT * FROM sap_raw.inventario_articulos")
    except Exception as e:
        st.error(f"Error cargando inventario: {e}")
        return pd.DataFrame()

# ============================================================
# 🎁 BONIFICACIONES: mapeo serie → almacén (LEGADO, se conserva por compatibilidad)
# ============================================================
MAPEO_SERIES_BONO = {
    **{k: "NORTE128" for k in ['128E', 'NCNT128.', 'ND-NT128', 'RC-NT128', 'NPNT128', '128J', '128F']},
    **{k: "PUNTO 134" for k in ['134E', '134F', 'NCAU134', 'NDFAU134', 'NPAU134', 'RC-AU134', '134J']},
    **{k: "PUNTO170" for k in ['170E', '170F', '170J', 'NDFP170', 'NCPU170.', 'NPPU170', 'RC-PU170']},
    **{k: "7 DE AGOSTO" for k in ['7AGE', 'NC7AGOS.', 'NP7AGOS', 'RC-7AGOS', 'NDF7AGOS', '7AGJ', '7AGF']},
    **{k: "AVENIDA19" for k in ['A19E', 'A19F', 'NCAVE19.', 'RC-AVE19', 'A19J', 'NDFAV19', 'NPAVE19']},
    **{k: "ARMENIA" for k in ['ARME', 'ARMJ', 'NCARMEN.', 'NDFARMEN', 'NPARMEN', 'RC-ARMEN', 'ND-ARMEN', 'ARMF']},
    **{k: "CHIA" for k in ['CHIE', 'CHIF', 'CHIJ', 'NCCHIA.', 'ND-CHIA', 'NPCHIA', 'RC-CHIA', 'NDFCHIA']},
    **{k: "EJECUTIVOS COMERCIALES" for k in ['COME', 'COMJ', 'NCCOMER.', 'ND-COMER', 'NDFCOMER', 'NPCOMER', 'RC-COMER', 'COMF']},
    **{k: "CENTRO 1" for k in ['CT1E', 'NCCENT1.', 'ND-CENT1', 'NDFCENT1', 'RC-CENT1', 'CT1J', 'NPCENT1', 'CT1F']},
    **{k: "CENTRO 3" for k in ['CT3E', 'CT3F', 'NCCENT3.', 'NPCENT3', 'RC-CENT3', 'CT3J']},
    **{k: "CENTRO 5" for k in ['CT5E', 'CT5F', 'CT5J', 'NPCENT5', 'RC-CENT5', 'NDFCENT5']},
    **{k: "CENTRO 6" for k in ['CT6E', 'CT6F', 'CT6J', 'NCCENT6.', 'NPCENT6', 'RC-CENT6', 'NDFCENT6']},
    **{k: "VILLAVICENCIO" for k in ['VILE', 'VILF', 'VILJ', 'NCVILLA.', 'NPVILLA', 'RC-VILLA', 'ND-VILLA']},
    **{k: "BODEGA INGENIERIA" for k in ['INGE', 'INGF', 'NCINGEN.', 'ND-INGEN', 'NPINGEN', 'FactClie', 'RC-INGEN', 'NDFINGEN']},
    **{k: "GIRARDOT" for k in ['GIRE', 'GIRJ', 'GIRF', 'NPGIRAR', 'NCGIRAR']},
}

# ============================================================
# 🎁 BONIFICACIONES: mapas literales del modelo Power BI
# ============================================================
INGE_SERIES = {'INGE', 'NDFINGEN', 'INGF', 'NCINGEN.', 'ND-INGEN', 'NPINGEN', 'FactClie', 'RC-INGEN'}

MAP_SERIE_FACTURA = {
    **{k: "NORTE128" for k in ['128E', 'NCNT128.', 'ND-NT128', 'RC-NT128', 'NPNT128', '128J', '128F']},
    **{k: "PUNTO 134" for k in ['134E', '134F', 'NCAU134', 'NDFAU134', 'NPAU134', 'RC-AU134', '134J']},
    **{k: "PUNTO 170" for k in ['170E', '170F', '170J', 'NDFP170', 'NCPU170.', 'NPPU170', 'RC-PU170']},
    **{k: "7 DE AGOSTO" for k in ['7AGE', 'NC7AGOS.', 'NP7AGOS', 'RC-7AGOS', 'NDF7AGOS', '7AGJ', '7AGF']},
    **{k: "AVENIDA 19" for k in ['A19E', 'A19F', 'NCAVE19.', 'RC-AVE19', 'A19J', 'NDFAV19', 'NPAVE19']},
    **{k: "ARMENIA" for k in ['ARME', 'ARMJ', 'NCARMEN.', 'NDFARMEN', 'NPARMEN', 'RC-ARMEN', 'ND-ARMEN', 'ARMF']},
    **{k: "CHIA" for k in ['CHIE', 'CHIF', 'CHIJ', 'NCCHIA.', 'ND-CHIA', 'NPCHIA', 'RC-CHIA', 'NDFCHIA']},
    **{k: "EJECUTIVOS COMERCIALES" for k in ['COME', 'COMJ', 'NCCOMER.', 'ND-COMER', 'NDFCOMER', 'NPCOMER', 'RC-COMER', 'COMF']},
    **{k: "CENTRO 1" for k in ['CT1E', 'NCCENT1.', 'ND-CENT1', 'NDFCENT1', 'RC-CENT1', 'CT1J', 'NPCENT1', 'CT1F']},
    **{k: "CENTRO 3" for k in ['CT3E', 'CT3F', 'NCCENT3.', 'NPCENT3', 'RC-CENT3', 'CT3J']},
    **{k: "CENTRO 5" for k in ['CT5E', 'CT5F', 'NCCENT5.', 'NPCENT5', 'RC-CENT5', 'NDFCENT5', 'CT5J']},
    **{k: "CENTRO 6" for k in ['CT6E', 'CT6F', 'CT6J', 'NCCENT6.', 'NPCENT6', 'RC-CENT6', 'NDFCENT6']},
    **{k: "VILLAVICENCIO" for k in ['VILE', 'VILF', 'VILJ', 'NCVILLA.', 'NPVILLA', 'RC-VILLA', 'ND-VILLA']},
    **{k: "GIRARDOT" for k in ['GIRE', 'GIRJ', 'GIRF', 'NPGIRAR', 'NCGIRAR']},
}

MAP_SERIE_PAGO = {
    **{k: "NORTE128" for k in ['128E', 'NCNT128.', 'ND-NT128', 'RC-NT128']},
    **{k: "PUNTO 134" for k in ['134E', '134F', 'NCAU134', 'NDFAU134', 'NPAU134', 'RC-AU134', '134J']},
    **{k: "PUNTO 170" for k in ['170E', 'NDFP170', 'NCPU170.', 'NPPU170', 'RC-PU170']},
    **{k: "7 DE AGOSTO" for k in ['7AGE', 'NC7AGOS.', 'NP7AGOS', 'RC-7AGOS']},
    **{k: "AVENIDA 19" for k in ['A19E', 'NCAVE19.', 'RC-AVE19']},
    **{k: "ARMENIA" for k in ['ARME', 'ARMJ', 'NCARMEN.', 'NDFARMEN', 'NPARMEN', 'RC-ARMEN']},
    **{k: "CHIA" for k in ['CHIE', 'CHIF', 'CHIJ', 'NCCHIA.', 'ND-CHIA', 'NPCHIA', 'RC-CHIA']},
    **{k: "EJECUTIVOS COMERCIALES" for k in ['COME', 'NCCOMER.', 'ND-COMER', 'NDFCOMER', 'NPCOMER', 'RC-COMER']},
    **{k: "CENTRO 1" for k in ['CT1E', 'NCCENT1.', 'ND-CENT1', 'NDFCENT1', 'RC-CENT1']},
    **{k: "CENTRO 3" for k in ['CT3E', 'NCCENT3.', 'NPCENT3', 'RC-CENT3']},
    **{k: "CENTRO 5" for k in ['CT5E', 'CT5F', 'NPCENT5', 'RC-CENT5', 'NDFCENT5']},
    **{k: "CENTRO 6" for k in ['CT6E', 'CT6J', 'NCCENT6.', 'NPCENT6', 'RC-CENT6', 'NDFCENT6']},
    **{k: "VILLAVICENCIO" for k in ['VILE', 'VILF', 'VILJ', 'NCVILLA.', 'NPVILLA', 'RC-VILLA', 'ND-VILLA']},
    **{k: "GIRARDOT" for k in ['GIRE', 'GIRJ', 'GIRF', 'NPGIRAR', 'NCGIRAR']},
}
SHORT2FULL = {
    "ALM128": "NORTE128", "ALM134": "PUNTO 134", "ALM170": "PUNTO170",
    "7AGOS": "7 DE AGOSTO", "AV19": "AVENIDA 19", "ARME": "ARMENIA",
    "CHIA": "CHIA", "EJECOM": "EJECUTIVOS COMERCIALES",
    "Q1": "CENTRO 1", "Q3": "CENTRO 3", "Q5": "CENTRO 5", "Q6": "CENTRO 6",
    "VILL": "VILLAVICENCIO",
    "GIRAR": "GIRARDOT",
}
META_BONO_PLENO = 2_580_000_000
META_BONO_PARCIAL = 2_190_000_000
FECHA_MIN_FACTURAS = "2025-04-01"


# ============================================================
# 🎁 CARGA DE PAGOS DE BONIFICACIONES (réplica DAX 1-9, metas HISTÓRICAS)
# ============================================================
@st.cache_data(ttl=3600, show_spinner="Cargando pagos de bonificaciones...")
def cargar_bonos_pagos():
    try:
        df = conn.query("SELECT * FROM sap_raw.bonos_pagos")
        if df.empty:
            return df

        df["fecha_pago"] = pd.to_datetime(df["fecha_pago"], errors="coerce")
        df["fecha_aplicacion"] = pd.to_datetime(df["fecha_aplicacion"], errors="coerce")
        df["fecha_factura"] = pd.to_datetime(df["fecha_factura"], errors="coerce")
        for c in ["total_pagado", "total_factura_original", "monto_aplicado"]:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

        serie_fact = df["nombre_serie_factura"].map(MAP_SERIE_FACTURA).fillna("OTRO")
        en_tabla_facturas = (
            df["fecha_factura"] >= pd.Timestamp(FECHA_MIN_FACTURAS)
        ) & (~df["nombre_serie_factura"].isin(INGE_SERIES))
        df["almacen_factura"] = np.where(en_tabla_facturas, serie_fact, "Sin Almacén Encontrado")

        df["factura_coincide"] = df["nombre_serie_pago"].map(MAP_SERIE_PAGO).fillna("OTRO")
        df["nombre_almacen"] = np.where(
            df["almacen_factura"] != "Sin Almacén Encontrado",
            df["almacen_factura"],
            df["factura_coincide"],
        )

        # % Bono histórico por mes-almacén (Opción A: metas del período vigente)
        lookup_pct = pct_bono_por_mes_almacen().rename(
            columns={"nombre_almacen": "almacen_key"}
        )[["anio", "mes", "almacen_key", "pct_bono"]]
        df["_anio_f"] = df["fecha_factura"].dt.year
        df["_mes_f"] = df["fecha_factura"].dt.month
        df = df.merge(lookup_pct, left_on=["_anio_f", "_mes_f", "almacen_factura"],
                      right_on=["anio", "mes", "almacen_key"], how="left")
        df["pct_bono"] = df["pct_bono"].fillna(0.0)
        df.drop(columns=["anio", "mes", "almacen_key", "_anio_f", "_mes_f"], inplace=True, errors="ignore")

        mismo_mes = df["fecha_pago"].dt.to_period("M") == df["fecha_aplicacion"].dt.to_period("M")
        df["monto_validado"] = np.where(mismo_mes, df["monto_aplicado"], np.nan)
        df["valor_base"] = (df["monto_validado"] / 1.19) * 0.975
        df["dias_pago"] = (df["fecha_pago"] - df["fecha_factura"]).dt.days
        tasa = np.select(
            [df["dias_pago"] > 71, df["dias_pago"] >= 61, df["dias_pago"] >= 46,
             df["dias_pago"] >= 31, df["dias_pago"] >= 6],
            [0.0, 0.0015, 0.003, 0.004, 0.006], default=0.008)
        df["tasa"] = tasa
        df["liq_sin_bono"] = df["valor_base"] * tasa
        df["liquidacion_ok"] = df["valor_base"] * tasa * df["pct_bono"]
        df["mes_aplicacion"] = df["fecha_aplicacion"].dt.to_period("M").astype(str)
        return df
    except Exception as e:
        st.error(f"Error cargando pagos de bonificaciones: {e}")
        return pd.DataFrame()


# DAX 3 de la tabla Anticipos
MAP_SERIE_ANTICIPOS = {
    **{k: "NORTE128" for k in ['128E', 'NCNT128.', 'ND-NT128', 'RC-NT128']},
    **{k: "PUNTO 134" for k in ['134E', '134F', 'NCAU134', 'NDFAU134', 'NPAU134', 'RC-AU134']},
    **{k: "PUNTO 170" for k in ['170E', 'NDFP170', 'NCPU170.', 'NPPU170', 'RC-PU170']},
    **{k: "7 DE AGOSTO" for k in ['7AGE', 'NC7AGOS.', 'NP7AGOS', 'RC-7AGOS']},
    **{k: "AVENIDA 19" for k in ['A19E', 'NCAVE19.', 'RC-AVE19']},
    **{k: "ARMENIA" for k in ['ARME', 'ARMJ', 'NCARMEN.', 'NDFARMEN', 'NPARMEN', 'RC-ARMEN']},
    **{k: "CHIA" for k in ['CHIE', 'CHIF', 'CHIJ', 'NCCHIA.', 'ND-CHIA', 'NPCHIA', 'RC-CHIA']},
    **{k: "EJECUTIVOS COMERCIALES" for k in ['COME', 'NCCOMER.', 'ND-COMER', 'NDFCOMER', 'NPCOMER', 'RC-COMER']},
    **{k: "CENTRO 1" for k in ['CT1E', 'NCCENT1.', 'ND-CENT1', 'NDFCENT1', 'RC-CENT1']},
    **{k: "CENTRO 3" for k in ['CT3E', 'NCCENT3.', 'NPCENT3', 'RC-CENT3']},
    **{k: "CENTRO 5" for k in ['CT5E', 'NCCENT5.', 'RC-CENT5']},
    **{k: "CENTRO 6" for k in ['CT6E', 'CT6J', 'NCCENT6.', 'NPCENT6', 'RC-CENT6']},
    **{k: "BODEGA INGENIERIA" for k in ['INGE', 'INGF', 'NCINGEN.', 'ND-INGEN', 'NPINGEN', 'FactClie', 'RC-INGEN']},
    **{k: "VILLAVICENCIO" for k in ['VILE', 'VILF', 'VILJ', 'NCVILLA.', 'NPVILLA', 'RC-VILLA']},
    **{k: "GIRARDOT" for k in ['GIRE', 'GIRJ', 'GIRF', 'NPGIRAR', 'NCGIRAR']},
}

# ============================================================
# 🚩 DETECCIÓN DE PAGOS EN CONCILIACIÓN CONSOLIDADA
# Regla: suma de montos aplicados por recibo > total del recibo + tolerancia.
# Ocurre cuando un recibo participa en una conciliación interna con N pagos
# y M facturas (cada pago "ve" todas las facturas del pool).
# SOLO LECTURA: no altera ningún cálculo existente del bono.
# ============================================================
@st.cache_data(ttl=3600, show_spinner="Detectando pagos consolidados...")
def cargar_pagos_consolidados(tolerancia: float = 100.0):
    q = """
        WITH g AS (
            SELECT
                pago_nro,
                MIN(fecha_recibo_pago)               AS fecha_recibo,
                MIN(nombre_serie_pago)               AS serie,
                MIN(codigo_cliente)                  AS codigo_cliente,
                MIN(nombre_cliente)                  AS nombre_cliente,
                MAX(total_pago_original)             AS pr,
                SUM(monto_aplicado)                  AS vr_aplicado,
                COUNT(*)                             AS n_aplicaciones,
                COUNT(DISTINCT nro_factura_aplicada) AS n_facturas
            FROM sap_raw.bonos_anticipos
            GROUP BY pago_nro
        )
        SELECT
            pago_nro, fecha_recibo, serie, codigo_cliente, nombre_cliente,
            pr, vr_aplicado, n_aplicaciones, n_facturas,
            (vr_aplicado - pr) AS diferencia
        FROM g
        WHERE (vr_aplicado - pr) > :tol
        ORDER BY diferencia DESC
    """
    df = conn.query(q, params={"tol": tolerancia})
    if not df.empty:
        df["fecha_recibo"] = pd.to_datetime(df["fecha_recibo"], errors="coerce")
        df["nombre_almacen"] = df["serie"].map(MAP_SERIE_ANTICIPOS).fillna("OTRO")
    return df


@st.cache_data(ttl=3600, show_spinner="Cargando anticipos...")
def cargar_bonos_anticipos():
    try:
        df = conn.query("SELECT * FROM sap_raw.bonos_anticipos")
        if df.empty:
            return df
        for c in ["fecha_recibo_pago", "fecha_aplicacion", "fecha_factura"]:
            df[c] = pd.to_datetime(df[c], errors="coerce")
        for c in ["total_pago_original", "monto_aplicado", "saldo_a_cuenta"]:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

        df["nombre_almacen"] = df["nombre_serie_pago"].map(MAP_SERIE_ANTICIPOS).fillna("OTRO")

        distinto_mes = (
            (df["fecha_recibo_pago"].dt.to_period("M") != df["fecha_aplicacion"].dt.to_period("M"))
            | df["fecha_aplicacion"].isna()
        )
        factura_posterior = df["fecha_factura"].isna() | (df["fecha_factura"] > df["fecha_recibo_pago"])
        df["monto_validado"] = np.where(distinto_mes & factura_posterior, df["monto_aplicado"], 0.0)
        df["monto_final"] = np.where(df["monto_validado"] == 0, df["saldo_a_cuenta"], df["monto_validado"])
        df["valor_base"] = (df["monto_final"] / 1.19) * 0.975

        serie_fact = df["nombre_serie_factura"].map(MAP_SERIE_FACTURA).fillna("OTRO")
        en_tabla = (
            df["fecha_factura"].notna()
            & (~df["nombre_serie_factura"].isin(INGE_SERIES))
            & (df["fecha_factura"] >= pd.Timestamp(FECHA_MIN_FACTURAS))
        )
        df["almacen_factura"] = np.where(en_tabla, serie_fact, "Sin Almacén Encontrado")
        df["_anio_f"] = df["fecha_factura"].dt.year
        df["_mes_f"] = df["fecha_factura"].dt.month

        lookup = pct_bono_por_mes_almacen().rename(
            columns={"nombre_almacen": "almacen_key"}
        )[["anio", "mes", "almacen_key", "pct_bono"]]
        df = df.merge(lookup, left_on=["_anio_f", "_mes_f", "almacen_factura"],
                      right_on=["anio", "mes", "almacen_key"], how="left")
        df["pct_bono"] = df["pct_bono"].fillna(0.0)
        df.drop(columns=["anio", "mes", "almacen_key", "_anio_f", "_mes_f"], inplace=True, errors="ignore")

        df["liquidacion_ok"] = df["valor_base"] * 0.008 * df["pct_bono"]
        return df
    except Exception as e:
        st.error(f"Error cargando anticipos: {e}")
        return pd.DataFrame()


# ============================================================
# 🎯 VALOR BONO CONCILIADO POR ALMACÉN (fuente única)
# Misma fórmula que "Detalle Bono" ▸ "Distribución por Punto de Venta":
# Liquidación Pagos + Liquidación Anticipos - Descuento por Conciliación.
# La usan tanto "Detalle Bono" como "Conciliación Bono" para que ambos
# módulos siempre muestren el mismo Valor Bono por almacén.
# ============================================================
def calcular_distribucion_bono_almacen(desde, hasta, alm_sel=None):
    """
    Devuelve, por nombre_almacen, el Total_Bono conciliado (Primera Etapa del Bono)
    para el rango [desde, hasta], filtrando opcionalmente por alm_sel.
    Columnas: nombre_almacen, liq_pagos, liq_anticipos, descuento_liq, Total_Bono.
    """
    cols_out = ["nombre_almacen", "liq_pagos", "liq_anticipos", "descuento_liq", "Total_Bono"]

    df_pagos = cargar_bonos_pagos()
    if df_pagos.empty or "estado_comision" not in df_pagos.columns:
        return pd.DataFrame(columns=cols_out)

    pag = df_pagos[df_pagos["estado_comision"] == "Pago Aplicado en el Mes"].copy()
    pag = pag[(pag["fecha_aplicacion"].dt.date >= desde) & (pag["fecha_aplicacion"].dt.date <= hasta)]
    if alm_sel:
        pag = pag[pag["nombre_almacen"].isin(alm_sel)]
    resumen_pagos = pag.groupby("nombre_almacen", as_index=False)["liquidacion_ok"].sum()
    resumen_pagos = resumen_pagos.rename(columns={"liquidacion_ok": "liq_pagos"})

    df_ant = cargar_bonos_anticipos()
    if not df_ant.empty:
        ant = df_ant[
            (df_ant["fecha_recibo_pago"].dt.date >= desde)
            & (df_ant["fecha_recibo_pago"].dt.date <= hasta)
        ].copy()
        if alm_sel:
            ant = ant[ant["nombre_almacen"].isin(alm_sel)]
        ant["liq_app"] = np.where(
            ant["monto_validado"] > 0,
            (ant["monto_validado"] / 1.19) * 0.975 * 0.008 * ant["pct_bono"],
            0,
        )
        resumen_anticipos = ant.groupby("nombre_almacen", as_index=False)["liq_app"].sum()
        resumen_anticipos = resumen_anticipos.rename(columns={"liq_app": "liq_anticipos"})
    else:
        resumen_anticipos = pd.DataFrame(columns=["nombre_almacen", "liq_anticipos"])

    df_cons = cargar_pagos_consolidados()
    if not df_cons.empty:
        dfc = df_cons[
            (df_cons["fecha_recibo"].dt.date >= desde)
            & (df_cons["fecha_recibo"].dt.date <= hasta)
        ].copy()
        if alm_sel:
            dfc = dfc[dfc["nombre_almacen"].isin(alm_sel)]
        dfc["descuento_liq"] = dfc["diferencia"] * 0.008
        resumen_descuentos = dfc.groupby("nombre_almacen", as_index=False)["descuento_liq"].sum()
    else:
        resumen_descuentos = pd.DataFrame(columns=["nombre_almacen", "descuento_liq"])

    resumen = pd.merge(resumen_pagos, resumen_anticipos, on="nombre_almacen", how="outer").fillna(0.0)
    resumen = pd.merge(resumen, resumen_descuentos, on="nombre_almacen", how="outer").fillna(0.0)
    resumen["Total_Bono"] = resumen["liq_pagos"] + resumen["liq_anticipos"] - resumen["descuento_liq"]
    return resumen[cols_out]


# ============================================================
# 🎛️ VARIABLES DEL BONO: bootstrap, resolver y CRUD auditado
# ============================================================
import json as _json

_ROLES_EDIT_BONO = ["admin", "gerente"]

ESCALA_RENT_DEFAULT = [
    (0.17,  False, None,  False, 1.10),
    (0.14,  False, 0.17,  True,  1.00),
    (0.13,  False, 0.14,  True,  0.80),
    (0.115, False, 0.13,  True,  0.60),
    (0.10,  False, 0.115, True,  0.50),
    (0.10,  True,  None,  False, 0.00),
    (0.095, True,  0.10,  False, -0.05),
    (0.09,  True,  0.095, False, -0.10),
    (0.085, True,  0.09,  False, -0.15),
    (0.08,  True,  0.085, False, -0.20),
    (0.075, True,  0.08,  False, -0.25),
    (0.07,  True,  0.075, False, -0.30),
    (0.065, True,  0.07,  False, -0.35),
    (0.06,  True,  0.065, False, -0.40),
    (0.055, True,  0.06,  False, -0.45),
    (0.05,  True,  0.055, False, -0.50),
    (0.045, True,  0.05,  False, -0.55),
    (0.04,  True,  0.045, False, -0.60),
    (0.035, True,  0.04,  False, -0.65),
    (0.03,  True,  0.035, False, -0.70),
    (0.025, True,  0.03,  False, -0.75),
    (0.02,  True,  0.025, False, -0.80),
    (0.015, True,  0.02,  False, -0.85),
    (0.01,  True,  0.015, False, -0.90),
    (0.005, True,  0.01,  False, -0.95),
    (0.0,   True,  0.005, False, -1.00),
    (None,  False, 0.0,   False, -1.00),
]

METAS_2025 = [
    (0,             True, 1_380_000_000, False, 0.00),
    (1_380_000_000, True, 1_955_000_000, False, 0.33),
    (1_955_000_000, True, 2_300_000_000, False, 0.67),
    (2_300_000_000, True, None,          False, 1.00),
]
METAS_2026 = [
    (0,             True, 2_190_000_000, False, 0.00),
    (2_190_000_000, True, 2_580_000_000, False, 0.67),
    (2_580_000_000, True, None,          False, 1.00),
]

SEED_PERIODOS = [
    ("2025",         "2025-01-01", "2025-12-31", 0.50, 2_300_000_000, 100_000_000, 0.06, METAS_2025),
    ("2026 Ene–Jul", "2026-01-01", "2026-07-31", 0.50, 2_580_000_000, 100_000_000, 0.06, METAS_2026),
    ("2026 Ago→",    "2026-08-01", None,          0.75, 2_580_000_000, 100_000_000, 0.06, METAS_2026),
]


def _pg_engine_bono():
    return create_engine(CADENA_CONEXION_PG)


def _auditar_bono(tabla, registro_id, accion, antes, despues):
    usuario = st.session_state.get("usuario_actual", "desconocido")
    with _pg_engine_bono().begin() as c:
        c.execute(text("""
            INSERT INTO app.bono_auditoria (tabla, registro_id, accion, usuario, valores_anteriores, valores_nuevos)
            VALUES (:t, :r, :a, :u, CAST(:va AS jsonb), CAST(:vn AS jsonb))
        """), {
            "t": tabla, "r": registro_id, "a": accion, "u": usuario,
            "va": _json.dumps(antes, default=str) if antes is not None else None,
            "vn": _json.dumps(despues, default=str) if despues is not None else None,
        })

def bootstrap_bono_variables():
    """Idempotente: crea tablas (fase 1), siembra períodos (fase 2) y audita (fase 3).
    Cada fase confirma su transacción para evitar problemas de visibilidad entre conexiones."""
    eng = _pg_engine_bono()

    # ---------- FASE 1: DDL (se confirma al salir del bloque) ----------
    with eng.begin() as c:
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS app.bono_periodos (
                id SERIAL PRIMARY KEY,
                nombre TEXT NOT NULL,
                fecha_inicio DATE NOT NULL,
                fecha_fin DATE,
                factor_desc_exentas NUMERIC NOT NULL DEFAULT 0.50,
                meta_adic_base NUMERIC NOT NULL,
                meta_adic_bloque NUMERIC NOT NULL,
                meta_adic_pct NUMERIC NOT NULL,
                activo BOOLEAN NOT NULL DEFAULT TRUE,
                creado_por TEXT, creado_en TIMESTAMPTZ DEFAULT now(),
                actualizado_por TEXT, actualizado_en TIMESTAMPTZ
            );
            CREATE TABLE IF NOT EXISTS app.bono_metas_ventas (
                id SERIAL PRIMARY KEY,
                periodo_id INT NOT NULL REFERENCES app.bono_periodos(id) ON DELETE CASCADE,
                orden INT NOT NULL,
                umbral_min NUMERIC, min_inclusivo BOOLEAN NOT NULL DEFAULT TRUE,
                umbral_max NUMERIC, max_inclusivo BOOLEAN NOT NULL DEFAULT FALSE,
                pct_aplica NUMERIC NOT NULL
            );
            CREATE TABLE IF NOT EXISTS app.bono_escala_rentabilidad (
                id SERIAL PRIMARY KEY,
                periodo_id INT NOT NULL REFERENCES app.bono_periodos(id) ON DELETE CASCADE,
                orden INT NOT NULL,
                cond_min NUMERIC, min_inclusivo BOOLEAN NOT NULL DEFAULT FALSE,
                cond_max NUMERIC, max_inclusivo BOOLEAN NOT NULL DEFAULT FALSE,
                pct_bono NUMERIC NOT NULL
            );
            CREATE TABLE IF NOT EXISTS app.bono_auditoria (
                id SERIAL PRIMARY KEY,
                tabla TEXT NOT NULL,
                registro_id INT,
                accion TEXT NOT NULL,
                usuario TEXT,
                momento TIMESTAMPTZ DEFAULT now(),
                valores_anteriores JSONB,
                valores_nuevos JSONB
            );
            CREATE TABLE IF NOT EXISTS app.bono_periodos_metas (
                id SERIAL PRIMARY KEY,
                nombre TEXT NOT NULL,
                fecha_inicio DATE NOT NULL,
                fecha_fin DATE,
                activo BOOLEAN NOT NULL DEFAULT TRUE,
                creado_por TEXT, creado_en TIMESTAMPTZ DEFAULT now(),
                actualizado_por TEXT, actualizado_en TIMESTAMPTZ
            );
            CREATE TABLE IF NOT EXISTS app.bono_periodos_escala (
                id SERIAL PRIMARY KEY,
                nombre TEXT NOT NULL,
                fecha_inicio DATE NOT NULL,
                fecha_fin DATE,
                activo BOOLEAN NOT NULL DEFAULT TRUE,
                creado_por TEXT, creado_en TIMESTAMPTZ DEFAULT now(),
                actualizado_por TEXT, actualizado_en TIMESTAMPTZ
            );
        """))

    # ---------- FASE 2: SEMILLA (tablas ya confirmadas y visibles) ----------
    creados = []
    with eng.begin() as c:
        n = c.execute(text("SELECT COUNT(*) FROM app.bono_periodos")).scalar()
        if not n:
            usuario = st.session_state.get("usuario_actual", "bootstrap")
            for (nom, fi, ff, fac, base, bloq, pct, metas) in SEED_PERIODOS:
                pid = c.execute(text("""
                    INSERT INTO app.bono_periodos
                        (nombre, fecha_inicio, fecha_fin, factor_desc_exentas,
                         meta_adic_base, meta_adic_bloque, meta_adic_pct, creado_por)
                    VALUES (:nom, CAST(:fi AS date), CAST(:ff AS date), :fac, :base, :bloq, :pct, :u)
                    RETURNING id
                """), {"nom": nom, "fi": fi, "ff": ff, "fac": fac,
                       "base": base, "bloq": bloq, "pct": pct, "u": usuario}).scalar()
                for i, (mn, mi, mx, mxi, p) in enumerate(metas, 1):
                    c.execute(text("""
                        INSERT INTO app.bono_metas_ventas
                            (periodo_id, orden, umbral_min, min_inclusivo, umbral_max, max_inclusivo, pct_aplica)
                        VALUES (:p, :o, :mn, :mi, :mx, :mxi, :pct)
                    """), {"p": pid, "o": i, "mn": mn, "mi": mi, "mx": mx, "mxi": mxi, "pct": p})
                for i, (cmn, cmi, cmx, cxi, pb) in enumerate(ESCALA_RENT_DEFAULT, 1):
                    c.execute(text("""
                        INSERT INTO app.bono_escala_rentabilidad
                            (periodo_id, orden, cond_min, min_inclusivo, cond_max, max_inclusivo, pct_bono)
                        VALUES (:p, :o, :cmn, :cmi, :cmx, :cxi, :pb)
                    """), {"p": pid, "o": i, "cmn": cmn, "cmi": cmi, "cmx": cmx, "cxi": cxi, "pb": pb})
                creados.append((pid, {"nombre": nom, "fecha_inicio": fi, "fecha_fin": ff}))

    # ---------- FASE 3: AUDITORÍA post-commit (best-effort, no rompe la semilla) ----------
    for pid, payload in creados:
        try:
            _auditar_bono("bono_periodos", pid, "CREATE_SEED", None, payload)
        except Exception:
            pass  # la auditoría nunca debe impedir el arranque

@st.cache_data(ttl=300, show_spinner=False)
def _cargar_config_bono():
    per = conn.query("SELECT * FROM app.bono_periodos WHERE activo ORDER BY fecha_inicio")
    met = conn.query("SELECT * FROM app.bono_metas_ventas ORDER BY periodo_id, orden")
    esc = conn.query("SELECT * FROM app.bono_escala_rentabilidad ORDER BY periodo_id, orden")
    return per, met, esc


@st.cache_data(ttl=300, show_spinner=False)
def _cargar_periodos_metas():
    return conn.query("SELECT * FROM app.bono_periodos_metas WHERE activo ORDER BY fecha_inicio")


@st.cache_data(ttl=300, show_spinner=False)
def _cargar_periodos_escala():
    return conn.query("SELECT * FROM app.bono_periodos_escala WHERE activo ORDER BY fecha_inicio")


def resolver_periodo_metas(fecha):
    """Retorna (fila_período_metas, df_tramos) vigentes para la fecha."""
    if isinstance(fecha, str):
        fecha = pd.Timestamp(fecha if len(fecha) == 10 else fecha + "-01").date()
    elif isinstance(fecha, pd.Timestamp):
        fecha = fecha.date()
    per_m = _cargar_periodos_metas()
    met = conn.query("SELECT * FROM app.bono_metas_ventas ORDER BY periodo_id, orden")
    if per_m.empty:
        return None, None
    mask = (per_m["fecha_inicio"] <= fecha) & (per_m["fecha_fin"].isna() | (per_m["fecha_fin"] >= fecha))
    sel = per_m[mask]
    if sel.empty:
        return None, None
    row = sel.iloc[0]
    return row, met[met["periodo_id"] == int(row["id"])]


def resolver_periodo_escala(fecha):
    """Retorna (fila_período_escala, df_rangos) vigentes para la fecha."""
    if isinstance(fecha, str):
        fecha = pd.Timestamp(fecha if len(fecha) == 10 else fecha + "-01").date()
    elif isinstance(fecha, pd.Timestamp):
        fecha = fecha.date()
    per_e = _cargar_periodos_escala()
    esc = conn.query("SELECT * FROM app.bono_escala_rentabilidad ORDER BY periodo_id, orden")
    if per_e.empty:
        return None, None
    mask = (per_e["fecha_inicio"] <= fecha) & (per_e["fecha_fin"].isna() | (per_e["fecha_fin"] >= fecha))
    sel = per_e[mask]
    if sel.empty:
        return None, None
    row = sel.iloc[0]
    return row, esc[esc["periodo_id"] == int(row["id"])]


def crear_periodo_metas(nombre, f_ini, f_fin, usuario):
    with _pg_engine_bono().begin() as c:
        pid = c.execute(text("""
            INSERT INTO app.bono_periodos_metas (nombre, fecha_inicio, fecha_fin, creado_por)
            VALUES (:n, CAST(:fi AS date), CAST(:ff AS date), :u) RETURNING id
        """), {"n": nombre, "fi": f_ini, "ff": f_fin, "u": usuario}).scalar()
    _auditar_bono("bono_periodos_metas", pid, "CREATE", None,
                  {"nombre": nombre, "fecha_inicio": str(f_ini), "fecha_fin": str(f_fin)})
    _cargar_periodos_metas.clear()
    return pid


def crear_periodo_escala(nombre, f_ini, f_fin, usuario):
    with _pg_engine_bono().begin() as c:
        pid = c.execute(text("""
            INSERT INTO app.bono_periodos_escala (nombre, fecha_inicio, fecha_fin, creado_por)
            VALUES (:n, CAST(:fi AS date), CAST(:ff AS date), :u) RETURNING id
        """), {"n": nombre, "fi": f_ini, "ff": f_fin, "u": usuario}).scalar()
    _auditar_bono("bono_periodos_escala", pid, "CREATE", None,
                  {"nombre": nombre, "fecha_inicio": str(f_ini), "fecha_fin": str(f_fin)})
    _cargar_periodos_escala.clear()
    return pid

# ============================================================
# CRUD ADICIONAL: actualizar y eliminar períodos de metas y escala
# ============================================================
def _validar_sin_solape_tabla(tabla, f_ini, f_fin, excluir_id=None):
    """Verifica que no haya solapamiento de fechas en una tabla de períodos."""
    per = conn.query(f"SELECT * FROM {tabla} WHERE activo")
    ini = pd.Timestamp(f_ini)
    fin = pd.Timestamp(f_fin) if f_fin else pd.Timestamp("2262-01-01")
    for _, r in per.iterrows():
        if excluir_id is not None and int(r["id"]) == int(excluir_id):
            continue
        r_fin = r["fecha_fin"] if pd.notna(r["fecha_fin"]) else pd.Timestamp("2262-01-01")
        if ini <= r_fin and r["fecha_inicio"] <= fin:
            raise ValueError(f"Se solapa con el período '{r['nombre']}'")


def actualizar_periodo_metas(pid, campos, usuario):
    """Actualiza nombre y fechas de un período de tramos de meta."""
    campos = {k: v for k, v in campos.items() if k in {"nombre", "fecha_inicio", "fecha_fin", "activo"}}
    per = _cargar_periodos_metas()
    antes = per[per["id"] == pid]
    if antes.empty:
        raise ValueError("Período de tramos no encontrado")
    antes_dict = antes.iloc[0].to_dict()
    f_ini = campos.get("fecha_inicio", antes_dict["fecha_inicio"])
    f_fin = campos.get("fecha_fin", antes_dict["fecha_fin"])
    _validar_sin_solape_tabla("app.bono_periodos_metas", f_ini, f_fin, excluir_id=pid)
    sets = ", ".join([f"{k} = :{k}" for k in campos])
    params = dict(campos)
    params["pid"] = pid
    params["u"] = usuario
    with _pg_engine_bono().begin() as c:
        c.execute(text(f"UPDATE app.bono_periodos_metas SET {sets}, actualizado_por = :u, actualizado_en = now() WHERE id = :pid"), params)
    _auditar_bono("bono_periodos_metas", pid, "UPDATE", antes_dict, campos)
    _cargar_periodos_metas.clear()
    pct_bono_por_mes_almacen.clear()


def actualizar_periodo_escala(pid, campos, usuario):
    """Actualiza nombre y fechas de un período de escala de rentabilidad."""
    campos = {k: v for k, v in campos.items() if k in {"nombre", "fecha_inicio", "fecha_fin", "activo"}}
    per = _cargar_periodos_escala()
    antes = per[per["id"] == pid]
    if antes.empty:
        raise ValueError("Período de escala no encontrado")
    antes_dict = antes.iloc[0].to_dict()
    f_ini = campos.get("fecha_inicio", antes_dict["fecha_inicio"])
    f_fin = campos.get("fecha_fin", antes_dict["fecha_fin"])
    _validar_sin_solape_tabla("app.bono_periodos_escala", f_ini, f_fin, excluir_id=pid)
    sets = ", ".join([f"{k} = :{k}" for k in campos])
    params = dict(campos)
    params["pid"] = pid
    params["u"] = usuario
    with _pg_engine_bono().begin() as c:
        c.execute(text(f"UPDATE app.bono_periodos_escala SET {sets}, actualizado_por = :u, actualizado_en = now() WHERE id = :pid"), params)
    _auditar_bono("bono_periodos_escala", pid, "UPDATE", antes_dict, campos)
    _cargar_periodos_escala.clear()


def eliminar_periodo_metas(pid, usuario):
    """Elimina un período de tramos y sus tramos asociados (CASCADE)."""
    per = _cargar_periodos_metas()
    antes = per[per["id"] == pid]
    if antes.empty:
        raise ValueError("Período de tramos no encontrado")
    antes_dict = antes.iloc[0].to_dict()
    tramos = conn.query("SELECT * FROM app.bono_metas_ventas WHERE periodo_id = :p", params={"p": pid})
    with _pg_engine_bono().begin() as c:
        c.execute(text("DELETE FROM app.bono_periodos_metas WHERE id = :pid"), {"pid": pid})
    _auditar_bono("bono_periodos_metas", pid, "DELETE",
                  {"periodo": antes_dict, "tramos": tramos.to_dict("records")}, None)
    _cargar_periodos_metas.clear()
    pct_bono_por_mes_almacen.clear()


def eliminar_periodo_escala(pid, usuario):
    """Elimina un período de escala y sus rangos asociados (CASCADE)."""
    per = _cargar_periodos_escala()
    antes = per[per["id"] == pid]
    if antes.empty:
        raise ValueError("Período de escala no encontrado")
    antes_dict = antes.iloc[0].to_dict()
    rangos = conn.query("SELECT * FROM app.bono_escala_rentabilidad WHERE periodo_id = :p", params={"p": pid})
    with _pg_engine_bono().begin() as c:
        c.execute(text("DELETE FROM app.bono_periodos_escala WHERE id = :pid"), {"pid": pid})
    _auditar_bono("bono_periodos_escala", pid, "DELETE",
                  {"periodo": antes_dict, "rangos": rangos.to_dict("records")}, None)
    _cargar_periodos_escala.clear()

def _validar_sin_solape_tabla(tabla, f_ini, f_fin, excluir_id=None):
    per = conn.query(f"SELECT * FROM {tabla} WHERE activo")
    ini = pd.Timestamp(f_ini)
    fin = pd.Timestamp(f_fin) if f_fin else pd.Timestamp("2262-01-01")
    for _, r in per.iterrows():
        if excluir_id is not None and int(r["id"]) == int(excluir_id):
            continue
        r_fin = r["fecha_fin"] if pd.notna(r["fecha_fin"]) else pd.Timestamp("2262-01-01")
        if ini <= r_fin and r["fecha_inicio"] <= fin:
            raise ValueError(f"Se solapa con el período '{r['nombre']}'")


def actualizar_periodo_metas(pid, campos, usuario):
    campos = {k: v for k, v in campos.items() if k in {"nombre", "fecha_inicio", "fecha_fin", "activo"}}
    per = _cargar_periodos_metas()
    antes = per[per["id"] == pid]
    if antes.empty:
        raise ValueError("Período de tramos no encontrado")
    antes_dict = antes.iloc[0].to_dict()
    f_ini = campos.get("fecha_inicio", antes_dict["fecha_inicio"])
    f_fin = campos.get("fecha_fin", antes_dict["fecha_fin"])
    _validar_sin_solape_tabla("app.bono_periodos_metas", f_ini, f_fin, excluir_id=pid)
    sets = ", ".join([f"{k} = :{k}" for k in campos])
    params = dict(campos); params["pid"] = pid; params["u"] = usuario
    with _pg_engine_bono().begin() as c:
        c.execute(text(f"UPDATE app.bono_periodos_metas SET {sets}, actualizado_por = :u, actualizado_en = now() WHERE id = :pid"), params)
    _auditar_bono("bono_periodos_metas", pid, "UPDATE", antes_dict, campos)
    _cargar_periodos_metas.clear()
    pct_bono_por_mes_almacen.clear()


def actualizar_periodo_escala(pid, campos, usuario):
    campos = {k: v for k, v in campos.items() if k in {"nombre", "fecha_inicio", "fecha_fin", "activo"}}
    per = _cargar_periodos_escala()
    antes = per[per["id"] == pid]
    if antes.empty:
        raise ValueError("Período de escala no encontrado")
    antes_dict = antes.iloc[0].to_dict()
    f_ini = campos.get("fecha_inicio", antes_dict["fecha_inicio"])
    f_fin = campos.get("fecha_fin", antes_dict["fecha_fin"])
    _validar_sin_solape_tabla("app.bono_periodos_escala", f_ini, f_fin, excluir_id=pid)
    sets = ", ".join([f"{k} = :{k}" for k in campos])
    params = dict(campos); params["pid"] = pid; params["u"] = usuario
    with _pg_engine_bono().begin() as c:
        c.execute(text(f"UPDATE app.bono_periodos_escala SET {sets}, actualizado_por = :u, actualizado_en = now() WHERE id = :pid"), params)
    _auditar_bono("bono_periodos_escala", pid, "UPDATE", antes_dict, campos)
    _cargar_periodos_escala.clear()


def eliminar_periodo_metas(pid, usuario):
    per = _cargar_periodos_metas()
    antes = per[per["id"] == pid]
    if antes.empty:
        raise ValueError("Período de tramos no encontrado")
    antes_dict = antes.iloc[0].to_dict()
    tramos = conn.query("SELECT * FROM app.bono_metas_ventas WHERE periodo_id = :p", params={"p": pid})
    with _pg_engine_bono().begin() as c:
        c.execute(text("DELETE FROM app.bono_periodos_metas WHERE id = :pid"), {"pid": pid})
    _auditar_bono("bono_periodos_metas", pid, "DELETE",
                  {"periodo": antes_dict, "tramos": tramos.to_dict("records")}, None)
    _cargar_periodos_metas.clear()
    pct_bono_por_mes_almacen.clear()


def eliminar_periodo_escala(pid, usuario):
    per = _cargar_periodos_escala()
    antes = per[per["id"] == pid]
    if antes.empty:
        raise ValueError("Período de escala no encontrado")
    antes_dict = antes.iloc[0].to_dict()
    rangos = conn.query("SELECT * FROM app.bono_escala_rentabilidad WHERE periodo_id = :p", params={"p": pid})
    with _pg_engine_bono().begin() as c:
        c.execute(text("DELETE FROM app.bono_periodos_escala WHERE id = :pid"), {"pid": pid})
    _auditar_bono("bono_periodos_escala", pid, "DELETE",
                  {"periodo": antes_dict, "rangos": rangos.to_dict("records")}, None)
    _cargar_periodos_escala.clear()

@st.cache_data(ttl=300, show_spinner=False)
def pct_bono_por_mes_almacen():
    """% de bono por (año, mes, almacén) usando los tramos del período de METAS vigente."""
    df = conn.query("""
        SELECT EXTRACT(YEAR FROM fecha_contabilizacion)::int AS anio,
               EXTRACT(MONTH FROM fecha_contabilizacion)::int AS mes,
               nombre_almacen,
               SUM(precio_sin_iva) AS ventas_mes
        FROM sap_raw.ventas_netas
        GROUP BY 1, 2, 3
    """)
    if df.empty:
        return pd.DataFrame(columns=["anio", "mes", "nombre_almacen", "ventas_mes", "pct_bono"])
    df["nombre_almacen"] = df["nombre_almacen"].map(lambda x: SHORT2FULL.get(x, x))
    df = df.groupby(["anio", "mes", "nombre_almacen"], as_index=False)["ventas_mes"].sum()

    per_m = _cargar_periodos_metas()
    met = conn.query("SELECT * FROM app.bono_metas_ventas ORDER BY periodo_id, orden")

    resultados = []
    for (anio, mes), grp in df.groupby(["anio", "mes"]):
        fecha = pd.Timestamp(year=int(anio), month=int(mes), day=1).date()
        sel = per_m[(per_m["fecha_inicio"] <= fecha) &
                    (per_m["fecha_fin"].isna() | (per_m["fecha_fin"] >= fecha))]
        grp = grp.copy()
        if sel.empty:
            grp["pct_bono"] = 0.0
            resultados.append(grp)
            continue
        pid = int(sel.iloc[0]["id"])
        metas = met[met["periodo_id"] == pid].sort_values("orden")
        conds, vals = [], []
        for _, t in metas.iterrows():
            c = pd.Series(True, index=grp.index)
            if pd.notna(t["umbral_min"]):
                c &= (grp["ventas_mes"] >= t["umbral_min"]) if t["min_inclusivo"] else (grp["ventas_mes"] > t["umbral_min"])
            if pd.notna(t["umbral_max"]):
                c &= (grp["ventas_mes"] <= t["umbral_max"]) if t["max_inclusivo"] else (grp["ventas_mes"] < t["umbral_max"])
            conds.append(c)
            vals.append(float(t["pct_aplica"]))
        grp["pct_bono"] = np.select(conds, vals, default=0.0)
        resultados.append(grp)
    return pd.concat(resultados, ignore_index=True)


def _invalidar_caches_bono():
    _cargar_config_bono.clear()
    pct_bono_por_mes_almacen.clear()
    cargar_bonos_pagos.clear()
    cargar_bonos_anticipos.clear()


def resolver_periodo_bono(fecha):
    if isinstance(fecha, str):
        s = fecha if len(fecha) == 10 else fecha + "-01"
        fecha = pd.Timestamp(s).date()  # <-- CAMBIO: .date()
    elif isinstance(fecha, pd.Timestamp):
        fecha = fecha.date()  # <-- CAMBIO: .date()
    per, met, esc = _cargar_config_bono()
    if per.empty:
        return None, None, None
    mask = (per["fecha_inicio"] <= fecha) & (
        per["fecha_fin"].isna() | (per["fecha_fin"] >= fecha)
    )
    sel = per[mask]
    if sel.empty:
        return None, None, None
    row = sel.iloc[0]
    pid = int(row["id"])
    return row, met[met["periodo_id"] == pid], esc[esc["periodo_id"] == pid]


def _match_intervalo(valor, df, col_min, col_min_inc, col_max, col_max_inc):
    for _, r in df.iterrows():
        ok_min = True if pd.isna(r[col_min]) else (
            valor >= r[col_min] if r[col_min_inc] else valor > r[col_min])
        ok_max = True if pd.isna(r[col_max]) else (
            valor <= r[col_max] if r[col_max_inc] else valor < r[col_max])
        if ok_min and ok_max:
            return r
    return None


def aplicar_meta_ventas(pvp, metas_df):
    r = _match_intervalo(float(pvp), metas_df, "umbral_min", "min_inclusivo", "umbral_max", "max_inclusivo")
    return float(r["pct_aplica"]) if r is not None else 0.0


def aplicar_escala_rentabilidad(rent_ratio, esc_df):
    r = _match_intervalo(float(rent_ratio), esc_df, "cond_min", "min_inclusivo", "cond_max", "max_inclusivo")
    return float(r["pct_bono"]) if r is not None else -1.0


# ---------------- CRUD auditado ----------------
def _validar_sin_solape(f_ini, f_fin, excluir_id=None):
    per, _, _ = _cargar_config_bono()
    ini = pd.Timestamp(f_ini)
    fin = pd.Timestamp(f_fin) if f_fin else pd.Timestamp("2262-01-01")
    for _, r in per.iterrows():
        if excluir_id is not None and int(r["id"]) == int(excluir_id):
            continue
        r_fin = r["fecha_fin"] if pd.notna(r["fecha_fin"]) else pd.Timestamp("2262-01-01")
        if ini <= r_fin and r["fecha_inicio"] <= fin:
            raise ValueError(f"Se solapa con el período '{r['nombre']}'")


CAMPOS_PERIODO_PERMITIDOS = {
    "nombre", "fecha_inicio", "fecha_fin", "factor_desc_exentas",
    "meta_adic_base", "meta_adic_bloque", "meta_adic_pct", "activo",
}


def crear_periodo(nombre, f_ini, f_fin, factor, base, bloque, pct, metas, escala, usuario):
    _validar_sin_solape(f_ini, f_fin)
    with _pg_engine_bono().begin() as c:
        pid = c.execute(text("""
            INSERT INTO app.bono_periodos
                (nombre, fecha_inicio, fecha_fin, factor_desc_exentas,
                 meta_adic_base, meta_adic_bloque, meta_adic_pct, creado_por)
            VALUES (:nom, CAST(:fi AS date), CAST(:ff AS date), :fac, :base, :bloq, :pct, :u)
            RETURNING id
        """), {"nom": nombre, "fi": f_ini, "ff": f_fin, "fac": factor,
               "base": base, "bloq": bloque, "pct": pct, "u": usuario}).scalar()
        for i, (mn, mi, mx, mxi, p) in enumerate(metas, 1):
            c.execute(text("""INSERT INTO app.bono_metas_ventas
                (periodo_id, orden, umbral_min, min_inclusivo, umbral_max, max_inclusivo, pct_aplica)
                VALUES (:p,:o,:mn,:mi,:mx,:mxi,:pct)"""),
                {"p": pid, "o": i, "mn": mn, "mi": mi, "mx": mx, "mxi": mxi, "pct": p})
        for i, (cmn, cmi, cmx, cxi, pb) in enumerate(escala, 1):
            c.execute(text("""INSERT INTO app.bono_escala_rentabilidad
                (periodo_id, orden, cond_min, min_inclusivo, cond_max, max_inclusivo, pct_bono)
                VALUES (:p,:o,:cmn,:cmi,:cmx,:cxi,:pb)"""),
                {"p": pid, "o": i, "cmn": cmn, "cmi": cmi, "cmx": cmx, "cxi": cxi, "pb": pb})
    _auditar_bono("bono_periodos", pid, "CREATE", None,
                  {"nombre": nombre, "fecha_inicio": f_ini, "fecha_fin": f_fin,
                   "factor_desc_exentas": factor, "meta_adic_base": base,
                   "meta_adic_bloque": bloque, "meta_adic_pct": pct})
    _invalidar_caches_bono()
    return pid


def actualizar_periodo(pid, campos: dict, usuario):
    campos = {k: v for k, v in campos.items() if k in CAMPOS_PERIODO_PERMITIDOS}
    per, met, esc = _cargar_config_bono()
    antes = per[per["id"] == pid]
    if antes.empty:
        raise ValueError("Período no encontrado")
    antes_dict = antes.iloc[0].to_dict()
    f_ini = campos.get("fecha_inicio", antes_dict["fecha_inicio"])
    f_fin = campos.get("fecha_fin", antes_dict["fecha_fin"])
    _validar_sin_solape(f_ini, f_fin, excluir_id=pid)
    sets = ", ".join([f"{k} = :{k}" for k in campos])
    params = {k: v for k, v in campos.items()}
    params["pid"] = pid
    params["u"] = usuario
    with _pg_engine_bono().begin() as c:
        c.execute(text(f"UPDATE app.bono_periodos SET {sets}, actualizado_por = :u, actualizado_en = now() WHERE id = :pid"), params)
    _auditar_bono("bono_periodos", pid, "UPDATE", antes_dict, campos)
    _invalidar_caches_bono()


def reemplazar_metas(periodo_id, metas, usuario):
    per, met, _ = _cargar_config_bono()
    antes = met[met["periodo_id"] == periodo_id].to_dict("records")
    with _pg_engine_bono().begin() as c:
        c.execute(text("DELETE FROM app.bono_metas_ventas WHERE periodo_id = :p"), {"p": periodo_id})
        for i, (mn, mi, mx, mxi, p) in enumerate(metas, 1):
            c.execute(text("""INSERT INTO app.bono_metas_ventas
                (periodo_id, orden, umbral_min, min_inclusivo, umbral_max, max_inclusivo, pct_aplica)
                VALUES (:p,:o,:mn,:mi,:mx,:mxi,:pct)"""),
                {"p": periodo_id, "o": i, "mn": mn, "mi": mi, "mx": mx, "mxi": mxi, "pct": p})
    _auditar_bono("bono_metas_ventas", periodo_id, "REPLACE", antes, metas)
    _invalidar_caches_bono()


def reemplazar_escala(periodo_id, escala, usuario):
    per, _, esc = _cargar_config_bono()
    antes = esc[esc["periodo_id"] == periodo_id].to_dict("records")
    with _pg_engine_bono().begin() as c:
        c.execute(text("DELETE FROM app.bono_escala_rentabilidad WHERE periodo_id = :p"), {"p": periodo_id})
        for i, (cmn, cmi, cmx, cxi, pb) in enumerate(escala, 1):
            c.execute(text("""INSERT INTO app.bono_escala_rentabilidad
                (periodo_id, orden, cond_min, min_inclusivo, cond_max, max_inclusivo, pct_bono)
                VALUES (:p,:o,:cmn,:cmi,:cmx,:cxi,:pb)"""),
                {"p": periodo_id, "o": i, "cmn": cmn, "cmi": cmi, "cmx": cmx, "cxi": cxi, "pb": pb})
    _auditar_bono("bono_escala_rentabilidad", periodo_id, "REPLACE", antes, escala)
    _invalidar_caches_bono()


def listar_auditoria_bono(limite=200):
    return conn.query("""
        SELECT id, momento, usuario, tabla, registro_id, accion, valores_anteriores, valores_nuevos
        FROM app.bono_auditoria ORDER BY id DESC LIMIT :n
    """, params={"n": limite})

#============================================================
#🏦 MÓDULO CONTABILIDAD: Auditoría de descuentos por exentas
#============================================================
def cargar_exentas_por_almacen(mes_periodo: str):
    """
    Calcula el descuento por exentas por almacén para un mes dado.
    mes_periodo: str formato 'YYYY-MM'
    """
    q = """
    SELECT
        nombre_almacen,
        SUM(precio_sin_iva) AS pvp_total,
        SUM(precio_sin_iva) FILTER (WHERE tax_code IN ('IVAEXE', 'IVDEXE', 'IVAIEXE')) AS total_exentas
    FROM sap_raw.ventas_netas 
    WHERE TO_CHAR(fecha_contabilizacion, 'YYYY-MM') = :mes
    GROUP BY nombre_almacen
    ORDER BY pvp_total DESC
    """
    df = conn.query(q, params={"mes": mes_periodo})
    if not df.empty:
        df["nombre_almacen"] = df["nombre_almacen"].map(lambda x: SHORT2FULL.get(x, x))
    return df

def cargar_exentas_por_comercial_ejecom(mes_periodo: str):
    """
    Calcula el descuento por exentas individualmente por comercial
    para los comerciales de EJECUTIVOS COMERCIALES (EJECOM).
    """
    q = """
    SELECT
        nombre_vendedor,
        documento_identidad,
        nombre_almacen,
        SUM(precio_sin_iva) AS pvp_total,
        SUM(precio_sin_iva) FILTER (WHERE tax_code IN ('IVAEXE', 'IVDEXE', 'IVAIEXE')) AS total_exentas
    FROM sap_raw.ventas_netas
    WHERE TO_CHAR(fecha_contabilizacion, 'YYYY-MM') = :mes
      AND nombre_almacen = 'EJECOM'
    GROUP BY nombre_vendedor, documento_identidad, nombre_almacen
    ORDER BY nombre_vendedor
    """
    df = conn.query(q, params={"mes": mes_periodo})
    return df


def guardar_decision_contabilidad(periodo_mes, tipo_registro, nombre_almacen,
                                   nombre_comercial, documento_comercial,
                                   valor_sistema, valor_corregido, decision,
                                   usuario, observacion=""):
    """Guarda o actualiza una decisión de contabilidad (UPSERT)."""
    with _pg_engine_bono().begin() as c:
        c.execute(text("""
            INSERT INTO app.contabilidad_decisiones_exentas 
                (periodo_mes, tipo_registro, nombre_almacen, nombre_comercial,
                 documento_comercial, valor_sistema, valor_corregido, decision,
                 usuario_decision, observacion)
            VALUES (:pm, :tr, :na, :nc, :dc, :vs, :vc, :dec, :u, :obs)
            ON CONFLICT (periodo_mes, tipo_registro, nombre_almacen, nombre_comercial)
            DO UPDATE SET
                valor_corregido = EXCLUDED.valor_corregido,
                decision = EXCLUDED.decision,
                usuario_decision = EXCLUDED.usuario_decision,
                fecha_decision = now(),
                observacion = EXCLUDED.observacion
        """), {
            "pm": periodo_mes, "tr": tipo_registro, "na": nombre_almacen,
            "nc": nombre_comercial, "dc": documento_comercial,
            "vs": valor_sistema, "vc": valor_corregido,
            "dec": decision, "u": usuario, "obs": observacion,
        })
    _auditar_bono("contabilidad_decisiones_exentas", None, f"DECISION_{decision.upper()}",
                  None, {"periodo": periodo_mes, "almacen": nombre_almacen,
                         "comercial": nombre_comercial, "decision": decision,
                         "valor_corregido": valor_corregido})


def obtener_decision_contabilidad(periodo_mes, nombre_almacen, nombre_comercial=""):
    """Obtiene la decisión vigente de contabilidad para un almacén/comercial."""
    q = """
        SELECT valor_corregido, decision, valor_sistema
        FROM app.contabilidad_decisiones_exentas
        WHERE periodo_mes = :pm
          AND nombre_almacen = :na
          AND nombre_comercial = :nc
        ORDER BY fecha_decision DESC
        LIMIT 1
    """
    df = conn.query(q, params={"pm": periodo_mes, "na": nombre_almacen, "nc": nombre_comercial})
    if df.empty:
        return None
    row = df.iloc[0]
    if row["decision"] == "corregido" and row["valor_corregido"] is not None:
        return float(row["valor_corregido"])
    return None  # Aprobado o pendiente → usar valor calculado


def obtener_decisiones_por_periodo(periodo_mes):
    """Obtiene todas las decisiones de un período para el historial."""
    q = """
        SELECT * FROM app.contabilidad_decisiones_exentas
        WHERE periodo_mes = :pm
        ORDER BY tipo_registro, nombre_almacen, nombre_comercial, fecha_decision DESC
    """
    return conn.query(q, params={"pm": periodo_mes})

def obtener_exentas_auditadas_por_almacen(mes_periodo: str):
    """
    Devuelve {almacen: valor_exentas_auditado}.
    Si hay decisión 'corregido' → usa valor_corregido.
    Si no (pendiente/aprobado) → usa el total_exentas calculado.
    Para EJECUTIVOS COMERCIALES suma el valor auditado de cada comercial.
    """
    resultado = {}

    # --- Almacenes (excepto EJECOM) ---
    df_alm = cargar_exentas_por_almacen(mes_periodo)
    for _, row in df_alm.iterrows():
        alm = row["nombre_almacen"]
        if alm == "EJECUTIVOS COMERCIALES":
            continue
        dec = obtener_decision_contabilidad(mes_periodo, alm, "")
        resultado[alm] = float(dec) if dec is not None else float(row["total_exentas"])

    # --- Ejecutivos Comerciales: suma auditada por comercial ---
    df_ej = cargar_exentas_por_comercial_ejecom(mes_periodo)
    total_ej = 0.0
    for _, row in df_ej.iterrows():
        dec = obtener_decision_contabilidad(mes_periodo, "EJECUTIVOS COMERCIALES", row["nombre_vendedor"])
        total_ej += float(dec) if dec is not None else float(row["total_exentas"])
    if not df_ej.empty:
        resultado["EJECUTIVOS COMERCIALES"] = total_ej

    return resultado