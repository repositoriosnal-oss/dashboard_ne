import pandas as pd
from sqlalchemy import create_engine, text
from werkzeug.security import generate_password_hash
import toml

# Leer conexión
secretos = toml.load(".streamlit/secrets.toml")
engine = create_engine(secretos["postgres"]["url"])

print("Leyendo usuarios actuales...")
# Traemos el ID y la clave actual de todos los usuarios
df_users = pd.read_sql("SELECT id, clave FROM app.usuarios_portal", engine)

print("Hasheando contraseñas (esto puede tardar un segundo)...")
with engine.begin() as conn:
    for index, row in df_users.iterrows():
        clave_actual = str(row['clave'])
        
        # Si ya empieza con pbkdf2 o scrypt, la saltamos (por si acaso)
        if clave_actual.startswith('pbkdf2') or clave_actual.startswith('scrypt'):
            continue
            
        # Generamos el hash de la clave que sea (clave123, punto23pass, etc)
        nuevo_hash = generate_password_hash(clave_actual)
        
        # Actualizamos en la base de datos
        conn.execute(text("UPDATE app.usuarios_portal SET clave = :nuevo WHERE id = :id"), 
                     {"nuevo": nuevo_hash, "id": row['id']})
        print(f"  -> Usuario ID {row['id']} actualizado.")

print("✅ ¡Migración exitosa! Todas las contraseñas ahora están cifradas.")