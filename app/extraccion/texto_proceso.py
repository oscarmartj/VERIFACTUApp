"""Proceso hijo de extracción de texto nativo (pdfplumber), ejecutado con límites de memoria y CPU.

Uso interno: python texto_proceso.py <pdf> <max_paginas>
Escribe en la salida estándar un JSON con, por página, su tamaño y sus palabras con posición. Es un script
autónomo (no importa la aplicación) para poder ejecutarse aislado del proceso del servidor.
"""
import json
import sys

import pdfplumber

MAX_CARACTERES_PAGINA = 50_000


def main() -> int:
    ruta, max_paginas = sys.argv[1], int(sys.argv[2])
    salida = []
    with pdfplumber.open(ruta) as pdf:
        for i, page in enumerate(pdf.pages[:max_paginas], start=1):
            if len(page.chars) > MAX_CARACTERES_PAGINA:
                print(json.dumps({"error": f"La página {i} tiene demasiado texto"}))
                return 3
            palabras = []
            for w in page.extract_words(keep_blank_chars=False, use_text_flow=False, x_tolerance=1.5, y_tolerance=2):
                t = w["text"].strip()
                if t:
                    palabras.append([t, w["x0"], w["top"], w["x1"], w["bottom"]])
            salida.append({"numero": i, "ancho": float(page.width), "alto": float(page.height), "palabras": palabras})
            page.close()
    print(json.dumps({"paginas": salida}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
