from src.conexion_db import sincronizar_rotacion_articulos, sincronizar_inventario_articulos

if __name__ == "__main__":
    print("📦 Sincronizando rotación de artículos...")
    ok1 = sincronizar_rotacion_articulos()
    print("📦 Sincronizando inventario (snapshot)...")
    ok2 = sincronizar_inventario_articulos()
    print("✅ Proceso finalizado." if (ok1 and ok2) else "❌ Revisa los errores arriba.")