import os
import threading
import time


def write_text_atomic(path, text, retries=20):
    """Escribe en un temporal único y lo renombra sobre el destino: los lectores nunca ven un fichero a medias.
    En Windows `os.replace` lanza PermissionError si otro proceso lo tiene abierto justo en ese instante (el motor
    lee los ajustes cada 0,5 s), por eso se reintenta unos milisegundos."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(retries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == retries - 1:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(0.01 * (attempt + 1))
