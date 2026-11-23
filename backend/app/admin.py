"""Create a new administrative account from a trusted deployment console."""
from getpass import getpass
from sqlalchemy import select
from .main import SessionLocal, User, UserCreate, hash_password, record_consent


def main():
    email = input("Correo del administrador: ").strip()
    name = input("Nombre: ").strip()
    password = getpass("Contraseña (mínimo 12 caracteres): ")
    if len(password) < 12 or password != getpass("Repita la contraseña: "):
        raise SystemExit("Las contraseñas deben coincidir y tener al menos 12 caracteres.")
    consent = input("Acepto el tratamiento de datos descrito en la pantalla de registro (sí/no): ")
    if consent.strip().casefold() not in {"sí", "si"}:
        raise SystemExit("Operación cancelada.")
    data = UserCreate(email=email, full_name=name, password=password, consent=True)
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.email == data.email.lower())):
            raise SystemExit("El correo ya está registrado.")
        user = User(email=data.email.lower(), full_name=data.full_name, password_hash=hash_password(password), role="admin", consent=True)
        db.add(user)
        db.flush()
        record_consent(db, user, True)
    print("Cuenta administrativa creada.")


if __name__ == "__main__":
    main()
